"""Executor for generating alternative text on images.

The executor uses :class:`SemanticInferenceClient` to obtain an alt-text
suggestion. A vision provider (Claude / OpenAI) describes the picture from its
bytes; without one, the only honest source is a caption a person wrote FOR the
picture (a ``<figcaption>``, an ``<img title>``, a Word Caption paragraph, or a
"Figure 2: …" label) — see :func:`app.ai.offline_rules.alt_from_caption`.
Anything else is refused with a plain reason and left for a person, never
written and never charged. Every suggestion, from any provider, passes
:func:`app.ai.offline_rules.vet_alt_text` before it is written.
"""

from __future__ import annotations

from typing import Optional

from app.ai.offline_rules import alt_from_caption, vet_alt_text
from app.ai.semantic_inference import InferenceResult, SemanticInferenceClient, refusal_reason
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ActionCode,
    ImageNode,
    iter_reading_order,
)
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

        # Pull multimodal image bytes if the parser captured them.
        properties = target.metadata.properties or {}
        image_b64 = properties.get("image_b64") or properties.get("imageBase64")
        image_mime = (
            properties.get("image_mime")
            or properties.get("imageMime")
            or "image/png"
        )
        # Only a caption, and where the parser found it. "nearby_text" and the
        # node's own content are NOT captions: the text that happens to sit
        # above a picture is as likely to be a nav bar, a byline or the next
        # body paragraph, and every one of those used to be pasted in as the
        # picture's description (behind an internal node id) and charged.
        caption = properties.get("caption")
        caption_source = properties.get("caption_source")
        from_caption = alt_from_caption(caption, caption_source)

        if not image_b64 and not from_caption.ok:
            # Nothing can LOOK at this picture and nobody captioned it: there
            # is no honest description to write, and no reason to spend an AI
            # call guessing one from the surrounding text.
            return _refused(action_code, plan, from_caption.reason)
        shared_caption = from_caption.ok and _caption_shared(tree, target, caption)
        shared_note = (
            "This caption belongs to several pictures at once, so it can't describe each one. "
            "Each picture needs a person to write one sentence saying what it shows."
        )
        if not image_b64 and shared_caption:
            return _refused(action_code, plan, shared_note)

        if not image_b64:
            # Nothing can look at the pixels, so the author's caption IS the
            # description. Asking a text-only model to "improve" it would add
            # words nobody checked against the picture ("Bar chart of…" for
            # what may be a photo) and cost a paid call — so don't.
            result = InferenceResult(text=from_caption.text or "", confidence=0.6, provider="heuristic")
        else:
            result = self._client.suggest_alt_text(
                node_id=target.id,
                label="image",
                location=location,
                page=page,
                caption=caption if from_caption.ok else None,
                caption_source=caption_source if from_caption.ok else None,
                image_b64=image_b64,
                image_mime=image_mime,
            )
        suggestion = (result.text or "").strip()
        if not suggestion:
            capped = bool(getattr(self._client, "cost_capped", False))
            reason = refusal_reason(result) or (
                "Automatic descriptions stopped for this document before reaching this picture."
                if capped
                else "We could not produce a description for this picture."
            )
            return _refused(action_code, plan, reason)
        # A PDF figure's printed "Figure N." caption is context for a provider
        # that can SEE the picture. The offline heuristic cannot: all it can
        # write is the caption again, which a screen reader reads out right
        # after the figure anyway. Not better than no alt: nothing written,
        # nothing charged. (Kept from the PDF lane over the general caption
        # rule, which still applies to Word and HTML captions.)
        if result.provider == "heuristic" and caption_source == "figure_label":
            return ExecutionResult(
                action_code=action_code,
                target_node_id=plan.target_node_id,
                status=ExecutionStatus.SKIPPED,
                notes=(
                    "Only this picture's printed caption is known, and repeating it as the "
                    "description adds nothing a screen reader does not already read. It needs "
                    "a person (or an AI that can see it) to say what it shows. Left for manual "
                    "review; nothing was written and you were not charged for it."
                ),
            )
        if shared_caption and result.provider == "heuristic":
            # A vision call fell back (error / budget) to the caption.
            return _refused(action_code, plan, shared_note)
        # Final gate for ANY provider: never write alt text our own analyzer
        # would flag, an internal id, a menu, a byline or an address. Shipping
        # it as a fix and charging for it is the overclaim the honesty
        # invariant forbids; the picture stays in the "needs you" list.
        problem = vet_alt_text(suggestion, node_id=target.id)
        if problem:
            return _refused(
                action_code,
                plan,
                f"We did not write the suggested description because {problem}.",
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

        source_note = " from the picture's own caption" if result.provider == "heuristic" else ""
        if replaced:
            action_note = f"Replaced non-descriptive alt {replaced!r}{source_note} via {result.provider}"
        else:
            action_note = f"Generated alt text{source_note} via {result.provider}"
        return ExecutionResult(
            action_code=action_code,
            target_node_id=plan.target_node_id,
            status=ExecutionStatus.SUCCESS,
            notes=(
                f"{action_note} (confidence {result.confidence:.2f}). "
                f"Pending human review. Text={suggestion!r}."
            ),
        )


_NEEDS_A_PERSON = "Left for you to describe; nothing was written and you were not charged for it."


def _refused(action_code: ActionCode, plan: RemediationPlan, reason: str) -> ExecutionResult:
    reason = (reason or "").strip()
    if reason and not reason.endswith((".", "!", "?")):
        reason += "."
    return ExecutionResult(
        action_code=action_code,
        target_node_id=plan.target_node_id,
        status=ExecutionStatus.SKIPPED,
        notes=f"{reason} {_NEEDS_A_PERSON}".strip(),
    )


def _caption_shared(tree: AccessibilityTree, target: ImageNode, caption) -> bool:
    """True when another content image carries the same caption text."""
    key = " ".join(str(caption or "").split()).lower()
    if not key:
        return False
    for node in iter_reading_order(tree.root):
        if node is target or not isinstance(node, ImageNode) or node.is_decorative:
            continue
        other = (node.metadata.properties or {}).get("caption")
        if " ".join(str(other or "").split()).lower() == key:
            return True
    return False


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
