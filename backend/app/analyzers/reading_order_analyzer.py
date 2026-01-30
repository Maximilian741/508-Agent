"""Reading order analyzers."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
)


AMBIGUOUS_READING_ORDER_VALUES = {"ambiguous", "unknown"}


class ReadingOrderAnalyzer(Analyzer):
    name = "reading_order_ambiguous"

    def analyze(self, tree: AccessibilityTree) -> None:
        properties = tree.root.metadata.properties
        reading_order = properties.get("reading_order")
        confidence = properties.get("reading_order_confidence")
        if isinstance(reading_order, str) and reading_order.lower() in AMBIGUOUS_READING_ORDER_VALUES:
            attach_flag(tree.root, AccessibilityFlagCode.READING_ORDER_AMBIGUOUS)
            return
        if isinstance(confidence, (int, float)) and confidence < 1.0:
            attach_flag(tree.root, AccessibilityFlagCode.READING_ORDER_AMBIGUOUS)
