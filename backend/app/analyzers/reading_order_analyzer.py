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
    """Flag content whose machine reading order contradicts the visual order.

    The real signal today is the PPTX parser's per-slide geometry check:
    a slide whose substantial text shapes are stacked vertically but appear
    in REVERSE tree order is read bottom-then-top by screen readers
    (WCAG 1.3.2). The parser marks such slides' SectionNodes with
    ``reading_order_inverted``; the flag lands on the slide, so the issue
    count reflects how many slides read out of order.

    The legacy document-level ``reading_order`` / ``reading_order_confidence``
    root properties are still honoured for forward compatibility.
    """

    name = "reading_order_ambiguous"

    def analyze(self, tree: AccessibilityTree) -> None:
        for section in tree.root.children:
            props = section.metadata.properties or {}
            if props.get("reading_order_inverted"):
                attach_flag(section, AccessibilityFlagCode.READING_ORDER_AMBIGUOUS)

        properties = tree.root.metadata.properties
        reading_order = properties.get("reading_order")
        confidence = properties.get("reading_order_confidence")
        if isinstance(reading_order, str) and reading_order.lower() in AMBIGUOUS_READING_ORDER_VALUES:
            attach_flag(tree.root, AccessibilityFlagCode.READING_ORDER_AMBIGUOUS)
            return
        if isinstance(confidence, (int, float)) and confidence < 1.0:
            attach_flag(tree.root, AccessibilityFlagCode.READING_ORDER_AMBIGUOUS)
