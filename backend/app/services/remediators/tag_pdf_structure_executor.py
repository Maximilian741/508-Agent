"""Executor for TAG_PDF_STRUCTURE (untagged PDFs).

The actual work — reconstructing the structure tree from the content stream —
happens in the PDF writer (`app.pdf.ua_tagger.tag_pdf`) during
`write_remediated`, and ONLY when this executor recorded
`tag_structure_requested`. That flag is the approval: without it the writer
does not tag (it used to tag every PDF, so approving nothing got the 5-credit
product free). The writer confirms a tree was actually built in its `applied`
list, and the pipeline counts and charges the fix only on that confirmation:
the written file carries /StructTreeRoot + /MarkInfo, and re-analysing the
output no longer raises PDF_UNTAGGED (the parser records `pdf_tagged=True`).
"""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
)
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class TagPdfStructureExecutor(RemediationExecutor):
    supported_actions = [ActionCode.TAG_PDF_STRUCTURE]

    def execute(
        self, plan: RemediationPlan, tree: Optional[AccessibilityTree] = None
    ) -> ExecutionResult:
        action_code = self._first_action(plan)
        self._ensure_supported(action_code)
        if tree is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="No accessibility tree provided; action not executed.",
            )

        root = tree.root
        props = root.metadata.properties or {}
        fmt = (root.metadata.source_format or "").lower()
        if fmt != "pdf":
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Structure tagging applies to PDFs only.",
            )
        if props.get("pdf_tagged"):
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="PDF already has a structure tree; nothing to reconstruct.",
            )
        has_flag = plan.flag.code == AccessibilityFlagCode.PDF_UNTAGGED or any(
            f.code == AccessibilityFlagCode.PDF_UNTAGGED for f in root.accessibility_flags
        )
        if not has_flag:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Document is not flagged as untagged; no changes applied.",
            )

        # Record the intent; the PDF writer performs the real reconstruction.
        if root.metadata.properties is None:
            root.metadata.properties = {}
        root.metadata.properties["tag_structure_requested"] = True

        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=(
                "Structure tree will be reconstructed in the remediated file: "
                "headings (by font size and bold section heads), lists, tables "
                "(text-geometry + ruling lines), every image as a figure (with "
                "its description where it has one) or as decoration, links named "
                "by their visible text, form fields next to their labels, and "
                "header/footer artifacts — plus MarkInfo, ParentTree and XMP "
                "metadata."
            ),
        )
