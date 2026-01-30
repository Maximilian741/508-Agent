"""Deterministic remediation engine stub."""

from app.models.accessibility import (
    AccessibilityTree,
    RemediationAction,
    RemediationReport,
    Violation,
)


class RemediationEngine:
    def detect_violations(self, tree: AccessibilityTree) -> list[Violation]:
        raise NotImplementedError("Violation detection is not yet implemented.")

    def plan_actions(self, violations: list[Violation]) -> list[RemediationAction]:
        raise NotImplementedError("Remediation planning is not yet implemented.")

    def build_report(
        self,
        document_id: str,
        violations: list[Violation],
        actions: list[RemediationAction],
    ) -> RemediationReport:
        raise NotImplementedError("Report generation is not yet implemented.")