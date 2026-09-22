"""Executor registry and orchestration for remediation actions."""

from __future__ import annotations

from typing import Iterable, List, Optional

from app.models.accessibility import AccessibilityTree, ActionCode
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.dispatcher import RemediationDispatcher
from app.services.remediators.add_table_headers_executor import AddTableHeadersExecutor
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor
from app.services.remediators.flag_for_manual_review_executor import FlagForManualReviewExecutor
from app.services.remediators.fill_form_field_labels_executor import FillFormFieldLabelsExecutor
from app.services.remediators.html_semantics_executors import (
    FixPositiveTabindexExecutor,
    SetInputAutocompleteExecutor,
)
from app.services.remediators.fix_contrast_executor import FixContrastExecutor
from app.services.remediators.fix_list_structure_executor import FixListStructureExecutor
from app.services.remediators.generate_alt_text_executor import GenerateAltTextExecutor
from app.services.remediators.generate_table_caption_executor import GenerateTableCaptionExecutor
from app.services.remediators.improve_link_text_executor import ImproveLinkTextExecutor
from app.services.remediators.normalize_heading_level_executor import NormalizeHeadingLevelExecutor
from app.services.remediators.promote_heading_executor import PromoteHeadingExecutor
from app.services.remediators.remove_decorative_alt_text_executor import RemoveDecorativeAltTextExecutor
from app.services.remediators.resolve_reading_order_executor import ResolveReadingOrderExecutor
from app.services.remediators.set_document_language_executor import SetDocumentLanguageExecutor
from app.services.remediators.set_document_title_executor import SetDocumentTitleExecutor
from app.services.remediators.set_slide_title_executor import SetSlideTitleExecutor
from app.services.remediators.set_table_header_scope_executor import SetTableHeaderScopeExecutor
from app.services.remediators.add_ocr_text_layer_executor import AddOcrTextLayerExecutor
from app.services.remediators.tag_pdf_structure_executor import TagPdfStructureExecutor
from app.ai.semantic_inference import HeuristicProvider, SemanticInferenceClient


def get_default_executors(client: Optional[SemanticInferenceClient] = None) -> List[RemediationExecutor]:
    """Build the executor set for ONE job.

    Every AI-backed executor shares a single inference client, so the per-job
    cost cap (``MAX_AI_COST_PER_JOB_USD``) bounds the whole job. Each executor
    used to build its own client, and with it its own cap: one remediation of
    a page with images, links, tables and no language spent several caps.

    Build a fresh set per job (``execute_plans`` does): the client's running
    cost tally IS the job's budget, so reusing a set would carry one job's
    spend into the next.
    """
    shared = client if client is not None else SemanticInferenceClient()
    return [
        GenerateAltTextExecutor(client=shared),
        RemoveDecorativeAltTextExecutor(),
        NormalizeHeadingLevelExecutor(),
        PromoteHeadingExecutor(),
        AddTableHeadersExecutor(),
        SetTableHeaderScopeExecutor(),
        GenerateTableCaptionExecutor(client=shared),
        FixListStructureExecutor(),
        FillFormFieldLabelsExecutor(),
        SetInputAutocompleteExecutor(),
        FixPositiveTabindexExecutor(),
        FixContrastExecutor(),
        ImproveLinkTextExecutor(client=shared),
        SetDocumentLanguageExecutor(client=shared),
        SetDocumentTitleExecutor(),
        SetSlideTitleExecutor(),
        ResolveReadingOrderExecutor(),
        TagPdfStructureExecutor(),
        AddOcrTextLayerExecutor(),
        FlagForManualReviewExecutor(),
    ]


def get_offline_executors() -> List[RemediationExecutor]:
    """Executors that can never reach a paid AI provider, for FREE paths.

    Gate AI spend here, not with ``requires_ai``: IMPROVE_LINK_TEXT is declared
    ``requires_ai=False`` yet its executor still calls the inference client, so
    a free path that merely filters AI actions still bills per link. Pinning
    the shared client to the heuristic provider makes the spend structurally
    impossible, and no paid client is ever constructed.
    """
    return get_default_executors(client=SemanticInferenceClient(provider=HeuristicProvider()))


def execute_plan(
    plan: RemediationPlan, executors: Optional[Iterable[RemediationExecutor]] = None
) -> List[ExecutionResult]:
    selected = list(executors) if executors is not None else get_default_executors()
    results: List[ExecutionResult] = []
    if not plan.actions:
        return results
    for action in plan.actions:
        for executor in selected:
            if executor.can_handle(action.action_code):
                results.append(executor.execute(plan))
                break
        else:
            results.append(
                ExecutionResult(
                    action_code=action.action_code,
                    target_node_id=plan.target_node_id,
                    status=ExecutionStatus.NOT_IMPLEMENTED,
                    notes="No executor registered for this action.",
                )
            )
    return results


def execute_plans(
    tree: AccessibilityTree,
    plans: List[RemediationPlan],
    dispatcher: Optional[RemediationDispatcher] = None,
) -> List[ExecutionResult]:
    """Example usage:
    tree = run_analyzers(tree)
    plans = plan_remediations(tree, policy)
    results = execute_plans(tree, plans)
    """
    selected_dispatcher = dispatcher or RemediationDispatcher(get_default_executors())
    seen_keys: set[tuple[str, ActionCode]] = set()
    results: List[ExecutionResult] = []
    for plan in plans:
        selection = selected_dispatcher.select(plan)
        key = (plan.target_node_id, selection.action_code)
        if key in seen_keys:
            results.append(
                ExecutionResult(
                    action_code=selection.action_code,
                    target_node_id=plan.target_node_id,
                    status=ExecutionStatus.SKIPPED,
                    notes=f"Duplicate suppressed for key={key}.",
                )
            )
            continue
        seen_keys.add(key)
        if selection.executor is None:
            results.append(
                ExecutionResult(
                    action_code=selection.action_code,
                    target_node_id=plan.target_node_id,
                    status=selection.status,
                    notes=selection.notes,
                )
            )
            continue
        executor = selection.executor
        try:
            results.append(executor.execute(plan, tree=tree))
        except Exception as exc:  # pragma: no cover - deterministic fallback
            results.append(
                ExecutionResult(
                    action_code=selection.action_code,
                    target_node_id=plan.target_node_id,
                    status=ExecutionStatus.NOT_IMPLEMENTED,
                    notes=f"Executor failed with {exc.__class__.__name__}.",
                )
            )
    return results
