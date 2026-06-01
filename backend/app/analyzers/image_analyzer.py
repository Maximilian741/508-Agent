"""Image-related analyzers."""

from __future__ import annotations

import re

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    ImageNode,
)


# Image file extensions: alt text that ends in one of these is almost always the
# original filename dumped in as alt (e.g. "image1.png", "DSC_0042.jpg").
_IMG_EXTS = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff",
    ".svg", ".webp", ".emf", ".wmf", ".heic", ".ico",
)

# The whole alt is just a generic word (+ optional trailing number): says nothing.
_PLACEHOLDER_RE = re.compile(
    r"^(image|picture|photo|graphic|img|pic|figure|drawing|object|chart|diagram|"
    r"shape|placeholder|untitled|content placeholder)[\s_\-#]*\d*$",
    re.IGNORECASE,
)

# Camera / screenshot default names: a distinctive prefix followed by digits.
_CAMERA_RE = re.compile(
    r"^(dscn|dscf|dsc|imgp|img|gopr|screen ?shot|screenshot|capture)[\s_\-]?\d+",
    re.IGNORECASE,
)


def is_nondescriptive_alt(alt: str) -> bool:
    """True when ``alt`` is present but provides no real description — a filename
    or a generic placeholder. Conservative: only clear cases, so a genuinely
    short-but-valid description ("Red car") is never flagged."""
    a = (alt or "").strip()
    if not a:
        return False  # empty is the MissingAltText case, not this one
    if a.lower().endswith(_IMG_EXTS):
        return True
    if _PLACEHOLDER_RE.match(a):
        return True
    if _CAMERA_RE.match(a):
        return True
    return False


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


class NonDescriptiveAltTextAnalyzer(Analyzer):
    """Flag meaningful images whose alt text is present but useless — a filename
    ("image1.png") or a generic placeholder ("Picture 1"). These pass the
    missing-alt check yet convey nothing to a screen-reader user (WCAG 1.1.1)."""

    name = "alt_text_not_descriptive"

    def analyze(self, tree: AccessibilityTree) -> None:
        for node in iter_nodes(tree):
            if isinstance(node, ImageNode) and not node.is_decorative:
                alt_text = (node.alt_text or "").strip()
                if alt_text and is_nondescriptive_alt(alt_text):
                    attach_flag(node, AccessibilityFlagCode.ALT_TEXT_NOT_DESCRIPTIVE)
