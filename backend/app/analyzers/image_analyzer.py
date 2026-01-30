"""Image-related analyzers."""

from __future__ import annotations

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ImageNode,
)


class MissingAltTextAnalyzer(Analyzer):
    name = "missing_alt_text"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, ImageNode) and not node.is_decorative:
                alt_text = (node.alt_text or "").strip()
                if not alt_text:
                    attach_flag(node, AccessibilityFlagCode.MISSING_ALT_TEXT)


class DecorativeImageAltAnalyzer(Analyzer):
    name = "decorative_image_with_alt"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, ImageNode) and node.is_decorative:
                alt_text = (node.alt_text or "").strip()
                if alt_text:
                    attach_flag(node, AccessibilityFlagCode.DECORATIVE_IMAGE_WITH_ALT)
