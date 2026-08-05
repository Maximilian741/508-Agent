"""Derive real, writer-confirmed before/after fixes for a scanned HTML page.

The free URL / whole-site scan is ANALYZE-ONLY — we never write to someone's
live site. But we can still show a developer exactly what to change, by running
the very same remediation engine that rewrites uploaded documents against a
throwaway copy and reporting the diff it produced.

Honesty rules baked in here:

* A diff is emitted ONLY for actions the writer put in its own ``applied`` list.
  If the writer no-oped, there is no fix shown — the same rule that governs
  billing (``pipeline._count_persisted_fixes``).
* NO PAID AI RUNS. Two independent gates, because one is not enough:
  1. the policy whitelist omits every ``requires_ai`` action; and
  2. every executor is built against a forced OFFLINE heuristic inference
     client. Gate 1 alone is insufficient — ``IMPROVE_LINK_TEXT`` is declared
     ``requires_ai=False`` yet its executor still calls ``suggest_link_text``,
     so on a paid provider a free scan of a link-heavy page would bill one
     request per link. Gate 2 makes the spend structurally impossible.
* Nothing is charged, persisted, or stored — the remediated copy is written to a
  temp file purely to read the diff back, then deleted.
* Structural rewrites (list conversion, header-row insertion, caption insertion)
  move elements, so an xpath captured before the mutation no longer addresses
  the same node afterwards. Those are reported as ``kind="structural"`` WITHOUT
  a bogus after-snippet rather than risk showing the wrong element.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from lxml import etree

from app.models.accessibility import ActionCode

logger = logging.getLogger(__name__)

# Actions whose effect is an attribute/text edit on ONE element, so the node's
# xpath still addresses the same element in the remediated document and a
# before/after snippet is exact.
_INPLACE_ACTIONS = {
    "SET_DOCUMENT_TITLE",
    "SET_DOCUMENT_LANGUAGE",
    "NORMALIZE_HEADING_LEVEL",
    "IMPROVE_LINK_TEXT",
    "FILL_FORM_FIELD_LABELS",
    "FIX_CONTRAST",
}

# Everything the scan is allowed to run. Deliberately excludes every
# ``requires_ai=True`` action (GENERATE_ALT_TEXT, GENERATE_TABLE_CAPTION) so a
# free scan cannot burn AI budget.
_SCAN_ACTION_CODES = [
    ActionCode.SET_DOCUMENT_TITLE,
    ActionCode.SET_DOCUMENT_LANGUAGE,
    ActionCode.NORMALIZE_HEADING_LEVEL,
    ActionCode.IMPROVE_LINK_TEXT,
    ActionCode.ADD_TABLE_HEADERS,
    ActionCode.FILL_FORM_FIELD_LABELS,
    ActionCode.FIX_LIST_STRUCTURE,
    ActionCode.FIX_CONTRAST,
    ActionCode.SET_INPUT_AUTOCOMPLETE,
    ActionCode.FIX_POSITIVE_TABINDEX,
]

_SNIPPET_LIMIT = 600


def _offline_dispatcher():
    """Executors wired to a forced OFFLINE (heuristic) inference client.

    Second AI gate — see the module docstring. Any executor that takes a
    ``client`` gets a heuristic-only one, so a free scan cannot reach a paid
    provider even if an action's ``requires_ai`` flag says it doesn't need AI.
    """
    from app.ai.semantic_inference import HeuristicProvider, SemanticInferenceClient
    from app.services.remediators.registry import RemediationDispatcher, get_default_executors

    offline = SemanticInferenceClient(provider=HeuristicProvider())
    executors = []
    for ex in get_default_executors():
        if hasattr(ex, "_client"):
            try:
                ex._client = offline  # noqa: SLF001 - deliberate offline pin
            except Exception:
                pass
        executors.append(ex)
    return RemediationDispatcher(executors)


def _snippet(el: Any) -> Optional[str]:
    """Serialize one element, truncated. Returns None if it can't be rendered."""
    if el is None:
        return None
    try:
        raw = etree.tostring(el, encoding="unicode", with_tail=False)
    except Exception:
        return None
    raw = " ".join(raw.split())  # collapse whitespace so snippets stay one-glance
    if len(raw) > _SNIPPET_LIMIT:
        raw = raw[:_SNIPPET_LIMIT] + " …"
    return raw


def _resolve(doc: Any, xpath: Optional[str]) -> Any:
    if not xpath:
        return None
    try:
        found = doc.xpath(xpath)
    except Exception:
        return None
    return found[0] if len(found) == 1 else None


def derive_scan_fixes(source_path: Path, tree: Any) -> Dict[str, Dict[str, Any]]:
    """Return ``{node_id: fix}`` for changes the writer actually made.

    Best-effort by design: ANY failure returns ``{}`` so a scan degrades to
    findings-without-fixes rather than erroring. Never raises.
    """
    out: Dict[str, Dict[str, Any]] = {}
    tmp_out: Optional[Path] = None
    try:
        # Imported lazily: keeps this module cheap for callers that never scan.
        from app.parsers.html_parser import _parse_document
        from app.services.remediation_planner import RemediationPolicy, plan_remediations
        from app.services.remediators.registry import execute_plans
        from app.writers.html_writer import write_remediated_html

        source_path = Path(source_path)
        pristine = _parse_document(source_path.read_bytes())

        # xpath per node id, captured BEFORE any mutation.
        xpath_by_id: Dict[str, str] = {}
        from app.models.accessibility import iter_reading_order

        for node in iter_reading_order(tree.root):
            xp = (node.metadata.properties or {}).get("__xpath")
            if xp:
                xpath_by_id[node.id] = xp

        policy = RemediationPolicy(
            allow_auto_actions=True,
            allow_ai_actions=False,          # hard gate: no AI spend on a free scan
            require_human_review_for_all=False,
            allowed_action_codes=_SCAN_ACTION_CODES,
        )
        plans = plan_remediations(tree, policy)
        if not plans:
            return {}
        execute_plans(tree, plans, dispatcher=_offline_dispatcher())

        fd, tmp_name = tempfile.mkstemp(suffix=".html")
        import os as _os

        _os.close(fd)
        tmp_out = Path(tmp_name)
        result = write_remediated_html(source_path, tree, tmp_out)
        applied = result.get("applied") if isinstance(result, dict) else []
        if not applied:
            return {}

        remediated = _parse_document(tmp_out.read_bytes())

        for entry in applied:
            if not isinstance(entry, dict):
                continue
            action = entry.get("action")
            node_id = entry.get("target_id")
            if not action or not node_id or node_id in out:
                continue
            xp = xpath_by_id.get(node_id)
            before = _snippet(_resolve(pristine, xp))
            if action in _INPLACE_ACTIONS:
                after = _snippet(_resolve(remediated, xp))
                if not before or not after or before == after:
                    continue
                out[node_id] = {
                    "source": "writer",
                    "kind": "element",
                    "action": action,
                    "before": before,
                    "after": after,
                    "requiresHumanVerification": False,
                }
            else:
                # Structural rewrite: the element moved, so an after-snippet
                # resolved by the pre-mutation xpath could be the WRONG node.
                # Report the change honestly without fabricating markup.
                out[node_id] = {
                    "source": "writer",
                    "kind": "structural",
                    "action": action,
                    "before": before,
                    "after": None,
                    "requiresHumanVerification": False,
                }
    except Exception as exc:  # never break a scan because guidance failed
        logger.warning("derive_scan_fixes failed (scan continues without fixes): %s", exc)
        return {}
    finally:
        if tmp_out is not None:
            try:
                tmp_out.unlink(missing_ok=True)
            except Exception:
                pass
    return out
