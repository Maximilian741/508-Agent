"""Executor for ADD_OCR_TEXT_LAYER (scanned, image-only PDFs).

The actual work — recognizing each scanned page and appending an invisible,
position-matched text layer — happens in the PDF writer. This executor
gates the action on OCR availability so the score stays honest:

- OCR enabled + provider available  -> SUCCESS (writer adds the layer; the
  output becomes searchable and the PDF/UA tagger then has real text to
  structure; re-analysing the output clears SCANNED_DOCUMENT_NO_TEXT).
- OCR disabled or Tesseract missing -> SKIPPED with an explicit note, and
  the finding stays honestly in the pending-manual bucket.
"""

from __future__ import annotations

from typing import Optional

from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
)
from app.services.ocr import get_ocr_provider
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class AddOcrTextLayerExecutor(RemediationExecutor):
    supported_actions = [ActionCode.ADD_OCR_TEXT_LAYER]

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
        fmt = (root.metadata.source_format or "").lower()
        if fmt != "pdf":
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="OCR text layers apply to PDFs only.",
            )
        has_flag = plan.flag.code == AccessibilityFlagCode.SCANNED_DOCUMENT_NO_TEXT or any(
            f.code == AccessibilityFlagCode.SCANNED_DOCUMENT_NO_TEXT
            for f in root.accessibility_flags
        )
        if not has_flag:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Document is not flagged as a scanned/no-text PDF.",
            )

        provider = get_ocr_provider()
        if provider is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=(
                    "OCR is not enabled on this deployment (OCR_ENABLED + a "
                    "Tesseract install are required) — the scan stays queued "
                    "for manual remediation."
                ),
            )

        if root.metadata.properties is None:
            root.metadata.properties = {}
        root.metadata.properties["ocr_text_layer_requested"] = True

        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=(
                f"OCR ({provider.name}) will add an invisible, position-matched "
                "text layer to each scanned page in the remediated file; the "
                "structure tagger then runs on the recognized text."
            ),
        )
