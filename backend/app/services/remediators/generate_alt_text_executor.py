"""Executor for generating alternative text on images.

The executor uses :class:`SemanticInferenceClient` to obtain an alt-text
suggestion (Claude / OpenAI when configured, otherwise a heuristic fallback).
It writes the suggestion to ``ImageNode.alt_text`` only when the new value is
non-empty, and records the provider + confidence in ``metadata.properties`` so
the UI can flag low-confidence suggestions for human review.
"""

from __future__ import annotations

from typing import Optional

from app.ai.semantic_inference import SemanticInferenceClient
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ImageNode,
    iter_reading_order,
)
from app.analyzers.image_analyzer import is_nondescriptive_alt
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor


class GenerateAltTextExecutor(RemediationExecutor):
    supported_actions = [ActionCode.GENERATE_ALT_TEXT]

    def __init__(self, client: Optional[SemanticInferenceClient] = None) -> None:
        self._client = client or SemanticInferenceClient()

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

        target = _find_image_node(tree, plan.target_node_id)
        if target is None:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Target image not found; no changes applied.",
            )
        if target.is_decorative:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Image is decorative; no alt text generated.",
            )

        has_missing = _has_alt_flag(plan, target, AccessibilityFlagCode.MISSING_ALT_TEXT)
        has_nondescriptive = _has_alt_flag(
            plan, target, AccessibilityFlagCode.ALT_TEXT_NOT_DESCRIPTIVE
        )
        if not has_missing and not has_nondescriptive:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Image does not include an alt-text flag; no changes applied.",
            )
        existing_alt = (target.alt_text or "").strip()
        # Existing alt is left alone UNLESS it was flagged non-descriptive (a
        # filename / placeholder) — only then do we replace it. Good alt never
        # carries that flag, so it is never overwritten.
        if existing_alt and not has_nondescriptive:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=f"Alt text already present: {target.alt_text!r}.",
            )

        page = getattr(target.metadata, "page", None)
        location = f"page {page}" if page else "the document"
        context = ""
        if target.content and target.content.text:
            context = target.content.text

        # Pull multimodal image bytes if the parser captured them.
        properties = target.metadata.properties or {}
        image_b64 = properties.get("image_b64") or properties.get("imageBase64")
        image_mime = (
            properties.get("image_mime")
            or properties.get("imageMime")
            or "image/png"
        )
        nearby_caption = properties.get("caption") or properties.get("nearby_text")

        result = self._client.suggest_alt_text(
            label=f"Image {target.id}",
            location=location,
            page=page,
            context=context or nearby_caption,
            caption=nearby_caption,
            image_b64=image_b64,
            image_mime=image_mime,
        )
        suggestion = (result.text or "").strip()
        if not suggestion:
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes="Semantic provider returned empty alt text; no changes applied.",
            )
        # Never write alt text that our OWN analyzer would flag as
        # non-descriptive. The heuristic provider — the fallback when no AI key
        # is configured, or when the per-job AI cost cap trips partway through
        # a large document — emits "Image page-3-img2 shown in page 3." when it
        # has no caption to work from. That names where the image is, not what
        # it shows; shipping it as a fix and crediting it is the overclaim the
        # honesty invariant forbids. Skip with a reason the UI can show, so the
        # image stays in the manual-review queue instead of looking done.
        if is_nondescriptive_alt(suggestion):
            capped = bool(getattr(self._client, "cost_capped", False))
            why = (
                "the per-job AI budget was exhausted before this image"
                if capped
                else "no AI provider is configured and there is no nearby caption to derive it from"
            )
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=(
                    f"Could not generate a real description ({why}); refusing to write the "
                    f"placeholder {suggestion!r}. Left for manual review."
                ),
            )

        replaced = existing_alt
        target.alt_text = suggestion
        # Persist provenance so the UI/manual-review queue can reflect the source.
        # NodeMetadata.properties defaults to {} but ImageNode.model_construct
        # (used for invalid decorative+alt combos) bypasses validators and may
        # leave it None — guard explicitly.
        if target.metadata.properties is None:
            target.metadata.properties = {}
        target.metadata.properties["alt_text_provider"] = result.provider
        target.metadata.properties["alt_text_confidence"] = round(result.confidence, 3)
        target.metadata.properties["alt_text_pending_review"] = True

        if replaced:
            action_note = f"Replaced non-descriptive alt {replaced!r} using {result.provider}"
        else:
            action_note = f"Generated alt text via {result.provider}"
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=(
                f"{action_note} (confidence {result.confidence:.2f}). "
                f"Pending human review. Text={suggestion!r}."
            ),
        )


def _find_image_node(tree: AccessibilityTree, target_id: str) -> Optional[ImageNode]:
    for node in iter_reading_order(tree.root):
        if node.id == target_id and isinstance(node, ImageNode):
            return node
    return None


def _has_alt_flag(
    plan: RemediationPlan, target: ImageNode, code: AccessibilityFlagCode
) -> bool:
    if plan.flag.code == code:
        return True
    return any(flag.code == code for flag in target.accessibility_flags)
