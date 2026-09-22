"""Deterministic orchestration over analyzers, planners, and executors.

The :class:`RemediationEngine` is the primary entry point used by API routes
and integration tests.  It glues the existing pieces together:

    parser → analyzers → planner → dispatcher (executors)

It exposes three steps:

* :meth:`detect_violations` — runs the registered analyzer suite and returns
  the resulting :class:`Violation` records derived from the flagged tree.
* :meth:`plan_actions` — turns the flagged tree into :class:`RemediationPlan`
  objects under a configurable :class:`RemediationPolicy`.  The convenience
  return value here is the flat list of :class:`RemediationAction` objects so
  the report can list them, but the structured plans remain available via the
  :attr:`last_plans` attribute for callers that need the policy verdict.
* :meth:`build_report` — wraps the violations and actions in a
  :class:`RemediationReport` with timestamped log entries.

The engine is intentionally stateful between invocations on a single instance
so callers can do ``engine.detect_violations(tree)`` then immediately
``engine.plan_actions(...)`` without re-walking the tree.  All state is
plain Python attributes so concurrent users should each construct their own
engine.
"""

from __future__ import annotations

from collections import deque

from datetime import datetime, timezone
from typing import Iterable, List, Optional, Sequence

from app.analyzers.base import Analyzer
from app.analyzers.registry import get_default_analyzers, run_analyzers
from app.models.accessibility import (
    AccessibilityFlag,
    AccessibilityFlagCode,
    AccessibilityTree,
    NodeLocation,
    RemediationAction,
    RemediationLogEntry,
    RemediationReport,
    Violation,
    iter_reading_order,
)
from app.services.remediation_planner import (
    RemediationPlan,
    RemediationPolicy,
    plan_remediations,
)
from app.services.remediators.base import ExecutionResult, ExecutionStatus
from app.services.remediators.dispatcher import RemediationDispatcher


class RemediationEngine:
    """High-level orchestrator wiring analyzers, planner, and dispatcher."""

    def __init__(
        self,
        analyzers: Optional[Sequence[Analyzer]] = None,
        dispatcher: Optional[RemediationDispatcher] = None,
        policy: Optional[RemediationPolicy] = None,
    ) -> None:
        self._analyzers: List[Analyzer] = list(analyzers) if analyzers is not None else get_default_analyzers()
        self._dispatcher: RemediationDispatcher = dispatcher or RemediationDispatcher()
        self.policy: RemediationPolicy = policy or RemediationPolicy()

        # Cached results — refreshed whenever ``detect_violations`` runs.
        self.last_tree: Optional[AccessibilityTree] = None
        self.last_violations: List[Violation] = []
        self.last_plans: List[RemediationPlan] = []
        self.last_results: List[ExecutionResult] = []

    # ------------------------------------------------------------------
    # Step 1: detect violations
    # ------------------------------------------------------------------

    def detect_violations(self, tree: AccessibilityTree) -> List[Violation]:
        """Run analyzers over ``tree`` and return :class:`Violation` records.

        Each violation is deterministically derived from the flag the analyzer
        attached.  The same analyzer/flag combo on the same node always yields
        the same ``violation_id`` so callers can diff repeat runs.
        """

        run_analyzers(tree, self._analyzers)
        self.last_tree = tree

        violations: List[Violation] = []
        for node, path in _iter_with_path(tree):
            if not node.accessibility_flags:
                continue
            for flag in node.accessibility_flags:
                violation = Violation(
                    violation_id=_violation_id(node.id, flag.code),
                    rule_id=flag.code.value,
                    severity=flag.severity.value,
                    description=flag.message,
                    location=NodeLocation(node_id=node.id, path=list(path)),
                    evidence=_collect_evidence(node, flag),
                )
                violations.append(violation)

        self.last_violations = violations
        return violations

    # ------------------------------------------------------------------
    # Step 2: plan actions
    # ------------------------------------------------------------------

    def plan_actions(
        self,
        violations: Optional[List[Violation]] = None,
        *,
        tree: Optional[AccessibilityTree] = None,
        policy: Optional[RemediationPolicy] = None,
    ) -> List[RemediationAction]:
        """Build a flat list of recommended :class:`RemediationAction`.

        Internally this delegates to :func:`plan_remediations` which returns the
        full :class:`RemediationPlan` objects (kept on :attr:`last_plans`).  The
        flat action list is what most callers need — for example, when building
        a :class:`RemediationReport`.
        """

        active_tree = tree or self.last_tree
        if active_tree is None:
            self.last_plans = []
            return []
        active_policy = policy or self.policy
        plans = plan_remediations(active_tree, active_policy)
        self.last_plans = plans

        # If the caller passed in a specific list of violations we only emit
        # actions for plans whose flag matches one of those violations.
        wanted_keys: Optional[set[tuple[str, str]]] = None
        if violations is not None:
            wanted_keys = {(_violation_target_node(v), v.rule_id) for v in violations}

        actions: List[RemediationAction] = []
        for plan in plans:
            if wanted_keys is not None:
                key = (plan.target_node_id, plan.flag.code.value)
                if key not in wanted_keys:
                    continue
            actions.extend(plan.actions)
        return actions

    # ------------------------------------------------------------------
    # Step 3: build report
    # ------------------------------------------------------------------

    def build_report(
        self,
        document_id: str,
        violations: List[Violation],
        actions: List[RemediationAction],
        *,
        results: Optional[Iterable[ExecutionResult]] = None,
    ) -> RemediationReport:
        """Assemble a :class:`RemediationReport` from the engine's state.

        The optional ``results`` argument is folded into ``logs`` so callers
        running execution can include per-action outcomes alongside the static
        plan.  When ``results`` is omitted, a single ``planned`` log entry per
        action is emitted.
        """

        now = datetime.now(timezone.utc)
        logs: List[RemediationLogEntry] = []

        if results is not None:
            for result in results:
                logs.append(
                    RemediationLogEntry(
                        timestamp=now,
                        action_id=result.action_code.value,
                        status=result.status.value,
                        details={
                            "target_node_id": result.target_node_id,
                            "notes": result.notes,
                        },
                    )
                )
        else:
            for action in actions:
                logs.append(
                    RemediationLogEntry(
                        timestamp=now,
                        action_id=action.action_code.value,
                        status="planned",
                        details={
                            "description": action.description,
                            "requires_ai": action.requires_ai,
                            "requires_human_review": action.requires_human_review,
                            "is_auto_applicable": action.is_auto_applicable,
                            "related_flag_code": action.related_flag_code.value,
                        },
                    )
                )

        return RemediationReport(
            document_id=document_id,
            violations=violations,
            actions=actions,
            logs=logs,
        )

    # ------------------------------------------------------------------
    # Step 4 (optional): execute
    # ------------------------------------------------------------------

    def execute(
        self,
        tree: Optional[AccessibilityTree] = None,
        plans: Optional[List[RemediationPlan]] = None,
    ) -> List[ExecutionResult]:
        """Convenience wrapper around the dispatcher.

        Useful for callers that have already produced a tree and just want the
        engine to drive the rest of the pipeline.
        """

        active_tree = tree or self.last_tree
        if active_tree is None:
            self.last_results = []
            return []
        active_plans = plans if plans is not None else self.last_plans
        if not active_plans:
            active_plans = plan_remediations(active_tree, self.policy)
            self.last_plans = active_plans

        from app.services.remediators.registry import execute_plans  # local import: avoid cycle

        results = execute_plans(active_tree, active_plans, self._dispatcher)
        self.last_results = results
        return results

    def run(
        self,
        tree: AccessibilityTree,
        document_id: str,
        *,
        execute: bool = False,
    ) -> RemediationReport:
        """One-shot convenience: detect → plan → (optional execute) → report."""

        violations = self.detect_violations(tree)
        actions = self.plan_actions(violations)
        results: Optional[List[ExecutionResult]] = None
        if execute:
            results = self.execute(tree)
        return self.build_report(document_id, violations, actions, results=results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _violation_id(node_id: str, flag_code: AccessibilityFlagCode) -> str:
    return f"vio-{node_id}-{flag_code.value.lower()}"


def _violation_target_node(violation: Violation) -> str:
    return violation.location.node_id


def _iter_with_path(tree: AccessibilityTree):
    """Iterate over nodes yielding ``(node, path_ids)`` in reading order.

    The path is the ordered list of ancestor ids leading to the node.
    """

    # deque: list.pop(0) is O(n), which made this walk O(n^2) on flat bodies.
    stack = deque([(tree.root, [tree.root.id])])
    while stack:
        node, path = stack.popleft()
        yield node, path
        for child in node.children:
            stack.append((child, path + [child.id]))


def _collect_evidence(node, flag: AccessibilityFlag) -> dict:
    evidence: dict = {
        "node_type": node.node_type.value,
    }
    page = getattr(node.metadata, "page", None)
    if page is not None:
        evidence["page"] = page

    # Flag-specific evidence.
    if flag.code == AccessibilityFlagCode.MISSING_ALT_TEXT and getattr(node, "alt_text", None) is not None:
        evidence["alt_text"] = node.alt_text
    if flag.code == AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT:
        evidence["alt_text"] = getattr(node, "alt_text", None)
        evidence["decorative"] = getattr(node, "is_decorative", False)
    if flag.code in {
        AccessibilityFlagCode.HEADING_LEVEL_JUMP,
        AccessibilityFlagCode.SKIPPED_HEADING_LEVEL,
    }:
        evidence["level"] = getattr(node, "level", None)
    if flag.code == AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE:
        if node.content and node.content.text:
            evidence["text"] = node.content.text
        evidence["target"] = getattr(node, "target", None)
    if flag.code in {
        AccessibilityFlagCode.DOCUMENT_LANGUAGE_MISSING,
        AccessibilityFlagCode.DOCUMENT_TITLE_MISSING,
    }:
        evidence["language"] = getattr(node.metadata, "language", None)
        evidence["title"] = node.metadata.properties.get("title") if node.metadata.properties else None
    if flag.code == AccessibilityFlagCode.LOW_CONTRAST_TEXT:
        cf = (node.metadata.properties or {}).get("contrast_finding")
        if isinstance(cf, dict):
            # Surface the measured colours/ratio AND the suggested accessible
            # text colour so the UI/report can show the exact fix.
            for key in ("fg", "bg", "ratio", "required", "suggested_fg", "suggested_ratio"):
                if key in cf:
                    evidence[key] = cf[key]
    return evidence


__all__ = ["RemediationEngine"]
