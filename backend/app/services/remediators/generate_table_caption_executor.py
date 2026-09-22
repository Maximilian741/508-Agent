"""Executor that generates a descriptive caption for a data table.

Mirrors :class:`GenerateAltTextExecutor`: it uses
:class:`SemanticInferenceClient` to derive a short caption from the table's
own column headers and a sample of its data rows (so the model is grounded in
the table's real content, not speculating), writes it to
``TableNode.metadata.properties['caption']`` — which the writer materializes as
an HTML ``<caption>`` and which a re-parse reads back, clearing the flag — and
records provider + confidence so the UI can route low-confidence captions to
human review. Never overwrites an existing caption.

Only an AI provider can write one: the offline heuristic abstains (a list of
column names is not a caption), and every suggestion passes
:func:`app.ai.offline_rules.vet_table_caption`, which refuses placeholders
("Data table") and captions that only repeat the header row.
"""

from __future__ import annotations

from typing import List, Optional

from app.ai.offline_rules import vet_table_caption
from app.ai.semantic_inference import SemanticInferenceClient, refusal_reason
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    TableCellNode,
    TableCellType,
    TableNode,
    TableRowNode,
    iter_reading_order,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor

# Formats whose writer actually materializes the generated caption into the
# output bytes. MUST stay in sync with the GENERATE_TABLE_CAPTION entries in
# pipeline._PERSISTED_ACTIONS: HTML inserts a <caption>; DOCX inserts a
# Caption-styled <w:p> above the <w:tbl>. For any other format the caption would
# never persist, so we must NOT spend an AI call on it (margin leak) nor leave a
# phantom caption in the in-memory tree.
_PERSISTABLE_FORMATS = {"html", "docx"}


class GenerateTableCaptionExecutor(RemediationExecutor):
    supported_actions = [ActionCode.GENERATE_TABLE_CAPTION]

    def __init__(self, client: Optional[SemanticInferenceClient] = None) -> None:
        self._client = client or SemanticInferenceClient()

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return _skip(action_code, plan, "No accessibility tree provided; action not executed.")

        target = _find_table_node(tree, plan.target_node_id)
        if target is None:
            return _skip(action_code, plan, "Target table not found; no changes applied.")
        if not _has_caption_flag(plan, target):
            return _skip(action_code, plan, "Table has no caption flag; no changes applied.")

        props = target.metadata.properties or {}
        existing = props.get("caption")
        if isinstance(existing, str) and existing.strip():
            return _skip(action_code, plan, f"Caption already present: {existing!r}.")

        # AI-cost guard: skip BEFORE the provider call for formats whose writer
        # can't persist a <caption>, so we never bill an AI request whose output
        # is silently dropped. The flag still surfaces as pending-manual.
        fmt = (target.metadata.source_format or "").lower()
        if fmt not in _PERSISTABLE_FORMATS:
            return _skip(
                action_code,
                plan,
                f"Captions can't be added automatically to {(fmt.upper() + ' files') if fmt else 'this kind of file'}, "
                "so this table is left for you to caption. You were not charged for it.",
            )

        headers, sample = _table_context(target)
        # Without an AI provider this abstains: a caption says what the table
        # is ABOUT in the author's words, and the header row already announces
        # the column names, so "Table: Region, Q1, Q2" (or, worse, a promoted
        # data row "Table: 2023, 410, 12%", or "Data table") is never clearly
        # better than no caption. Those used to be inserted as visible Caption
        # paragraphs and charged.
        result = self._client.suggest_table_caption(headers=headers, sample=sample)
        suggestion = (result.text or "").strip()
        if not suggestion:
            return _skip(
                action_code,
                plan,
                _needs_a_person(
                    refusal_reason(result) or "We could not produce a caption for this table."
                ),
            )
        problem = vet_table_caption(suggestion, headers)
        if problem:
            return _skip(
                action_code,
                plan,
                _needs_a_person(
                    f"We did not add the suggested caption because {problem}. "
                    "A caption has to say what the table is about"
                ),
            )

        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["caption"] = suggestion
        target.metadata.properties["table_caption_provider"] = result.provider
        target.metadata.properties["table_caption_confidence"] = round(result.confidence, 3)
        target.metadata.properties["table_caption_pending_review"] = True

        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=(
                f"Generated table caption via {result.provider} "
                f"(confidence {result.confidence:.2f}). Pending human review. "
                f"Caption={suggestion!r}."
            ),
        )


def _table_context(table: TableNode) -> "tuple[List[str], str]":
    """Column-header texts + a short sample of data-row text to ground the AI."""
    headers: List[str] = []
    data_rows: List[str] = []
    for row in table.children:
        if not isinstance(row, TableRowNode):
            continue
        cells = [c for c in row.children if isinstance(c, TableCellNode)]
        texts = [(c.content.text or "").strip() if c.content else "" for c in cells]
        if any(c.cell_type == TableCellType.HEADER for c in cells):
            headers.extend(t for t in texts if t)
        elif len(data_rows) < 2:
            joined = " | ".join(t for t in texts if t)
            if joined:
                data_rows.append(joined)
    return headers, "\n".join(data_rows)


def _find_table_node(tree: AccessibilityTree, target_id: str) -> Optional[TableNode]:
    for node in iter_reading_order(tree.root):
        if node.id == target_id and isinstance(node, TableNode):
            return node
    return None


def _has_caption_flag(plan: RemediationPlan, target: TableNode) -> bool:
    if plan.flag.code == AccessibilityFlagCode.TABLE_CAPTION_MISSING:
        return True
    return any(f.code == AccessibilityFlagCode.TABLE_CAPTION_MISSING for f in target.accessibility_flags)


def _needs_a_person(reason: str) -> str:
    reason = (reason or "").strip()
    if reason and not reason.endswith((".", "!", "?")):
        reason += "."
    return f"{reason} Left for you to caption; nothing was written and you were not charged for it.".strip()


def _skip(action_code: ActionCode, plan: RemediationPlan, notes: str) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=ExecutionStatus.SKIPPED,
        notes=notes,
    )
