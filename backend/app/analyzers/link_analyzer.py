"""Link text analyzers."""

from __future__ import annotations

import re

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    LinkNode,
)


NON_DESCRIPTIVE_LINK_TEXT = {
    "click here",
    "here",
    "read more",
    "more",
    "link",
    "this",
    "learn more",
    "details",
}


def _normalize_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]+", "", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


class LinkTextAnalyzer(Analyzer):
    name = "link_text_non_descriptive"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, LinkNode) and node.content.kind == ContentKind.TEXT:
                normalized = _normalize_text(node.content.text or "")
                if not normalized or normalized in NON_DESCRIPTIVE_LINK_TEXT:
                    attach_flag(node, AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE)
