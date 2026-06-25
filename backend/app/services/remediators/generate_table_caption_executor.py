"""Executor that generates a descriptive caption for a data table.

Mirrors :class:`GenerateAltTextExecutor`: it uses
:class:`SemanticInferenceClient` to derive a short caption from the table's
own column headers and a sample of its data rows (so the model is grounded in
the table's real content, not speculating), writes it to
``TableNode.metadata.properties['caption']`` — which the writer materializes as
an HTML ``<caption>`` and which a re-parse reads back, clearing the flag — and
records provider + confidence so the UI can route low-confidence captions to
human review. Never overwrites an existing caption.
"""

from __future__ import annotations

from typing import List, Optional

from app.ai.semantic_inference import SemanticInferenceClient
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
# output bytes. MUST stay in sync with the GENERATE_TABLE_CAPTION entry in
# pipeline._PERSISTED_ACTIONS — only HTML inserts a <caption> today. For any
# other format the caption would never persist, so we must NOT spend an AI call
# on it (margin leak) nor leave a phantom caption in the in-memory tree.
_PERSISTABLE_FORMATS = {"html"}


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
                f"Automatic captioning is only available for HTML "
                f"(source format: {fmt or 'unknown'}); leaving this table for manual captioning.",
            )

        headers, sample = _table_context(target)
        result = self._client.suggest_table_caption(headers=headers, sample=sample)
        suggestion = (result.text or "").strip()
        if not suggestion:
            return _skip(action_code, plan, "Semantic provider returned empty caption; no changes applied.")

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


def _skip(action_code: ActionCode, plan: RemediationPlan, notes: str) -> ExecutionResult:
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=ExecutionStatus.SKIPPED,
        notes=notes,
    )
