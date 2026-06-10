"""Executor registry and orchestration for remediation actions."""

from __future__ import annotations

from typing import Iterable, List, Optional

from app.models.accessibility import AccessibilityTree, ActionCode
from app.services.remediation_planner import RemediationPlan
from app.services.remediators.dispatcher import RemediationDispatcher
from app.services.remediators.add_table_headers_executor import AddTableHeadersExecutor
from app.services.remediators.base import ExecutionResult, ExecutionStatus, RemediationExecutor
from app.services.remediators.flag_for_manual_review_executor import FlagForManualReviewExecutor
from app.services.remediators.fix_list_structure_executor import FixListStructureExecutor
from app.services.remediators.generate_alt_text_executor import GenerateAltTextExecutor
from app.services.remediators.improve_link_text_executor import ImproveLinkTextExecutor
from app.services.remediators.normalize_heading_level_executor import NormalizeHeadingLevelExecutor
from app.services.remediators.remove_decorative_alt_text_executor import RemoveDecorativeAltTextExecutor
from app.services.remediators.resolve_reading_order_executor import ResolveReadingOrderExecutor
from app.services.remediators.set_document_language_executor import SetDocumentLanguageExecutor
from app.services.remediators.set_document_title_executor import SetDocumentTitleExecutor
from app.services.remediators.set_table_header_scope_executor import SetTableHeaderScopeExecutor
from app.services.remediators.tag_pdf_structure_executor import TagPdfStructureExecutor


def get_default_executors() -> List[RemediationExecutor]:
    return [
        GenerateAltTextExecutor(),
        RemoveDecorativeAltTextExecutor(),
        NormalizeHeadingLevelExecutor(),
        AddTableHeadersExecutor(),
        SetTableHeaderScopeExecutor(),
        FixListStructureExecutor(),
        ImproveLinkTextExecutor(),
        SetDocumentLanguageExecutor(),
        SetDocumentTitleExecutor(),
        ResolveReadingOrderExecutor(),
        TagPdfStructureExecutor(),
        FlagForManualReviewExecutor(),
    ]


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
