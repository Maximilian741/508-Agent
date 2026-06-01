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
    "click",
    "more info",
    "go here",
    "this page",
    "url",
    "link here",
    "continue reading",
    "see more",
    "view more",
    "website",
    "web site",
    "read this",
    "full story",
}


# A link whose visible text IS the bare URL/domain (e.g. "https://example.com/a"
# or "www.example.com") is non-descriptive: a screen reader reads the whole URL
# character by character. Match only when the ENTIRE trimmed text is the URL, so
# prose that merely mentions a domain ("Visit example.com today") is not flagged.
_URL_TEXT_RE = re.compile(
    r"^(?:https?://\S+"
    r"|www\.\S+"
    r"|[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z0-9\-]+)*"
    r"\.(?:com|org|net|edu|gov|mil|int|io|co|us|uk|ca|au|de|fr|jp|cn|info|biz|app|dev|ai|gov\.uk)"
    r"(?:/\S*)?)$",
    re.IGNORECASE,
)


def _normalize_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]+", "", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _looks_like_url(raw: str) -> bool:
    return bool(_URL_TEXT_RE.match((raw or "").strip()))


class LinkTextAnalyzer(Analyzer):
    name = "link_text_non_descriptive"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, LinkNode) and node.content.kind == ContentKind.TEXT:
                raw = node.content.text or ""
                normalized = _normalize_text(raw)
                if not normalized or normalized in NON_DESCRIPTIVE_LINK_TEXT or _looks_like_url(raw):
                    attach_flag(node, AccessibilityFlagCode.LINK_TEXT_NON_DESCRIPTIVE)
