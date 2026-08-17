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

# Generic words that name the *kind* of image but not its content. As the WHOLE
# alt (optionally with a trailing number) they describe nothing — a content
# image still needs a real description, so these warrant regeneration. We
# deliberately EXCLUDE decorative-hint words (spacer/divider/separator) because
# those signal a different remediation (mark the image decorative, empty alt)
# rather than "write a better description".
_PLACEHOLDER_WORDS = (
    "image", "picture", "photo", "photograph", "graphic", "graphics", "img",
    "pic", "figure", "drawing", "object", "chart", "diagram", "shape",
    "placeholder", "untitled", "content placeholder", "logo", "banner", "icon",
    "avatar", "thumbnail", "illustration", "artwork", "clipart", "clip art",
    "screenshot", "screen shot", "headshot", "snapshot", "image file",
)
# The whole alt is just one of those words (+ optional separators/number).
_PLACEHOLDER_RE = re.compile(
    r"^(?:" + "|".join(re.escape(w) for w in _PLACEHOLDER_WORDS) + r")[\s_\-#:]*\d*$",
    re.IGNORECASE,
)
# "Image page-3-img2 shown in page 3." / "Picture slide 4" — names WHERE the
# image is, never WHAT it shows. This is exactly the string our own heuristic
# alt provider emits when it has no caption or nearby text to work from, so
# recognizing it here is what lets us refuse to ship it and lets a re-audit of
# our own output stay honest.
_LOCATION_ONLY_RE = re.compile(
    r"^(?:image|picture|figure|graphic|photo|img)\b.*?"
    r"\b(?:shown\s+(?:in|on)\s+)?(?:page|slide|sheet)\s*\d+\.?$",
    re.IGNORECASE,
)

# Camera / screenshot default names: a distinctive prefix followed by digits.
_CAMERA_RE = re.compile(
    r"^(dscn|dscf|dsc|imgp|img|gopr|screen ?shot|screenshot|capture)[\s_\-]?\d+",
    re.IGNORECASE,
)

# Whole-alt filler that conveys nothing (matched case-insensitively, exact).
_JUNK_ALTS = frozenset({
    "n/a", "na", "n.a.", "none", "null", "nil", "blank", "empty", "tbd",
    "todo", "to do", "test", "temp", "tmp", "asdf", "xxx", "...", "--", "—",
    "no alt", "no alt text", "no description", "alt", "alt text", "alttext",
    "description", "desc", "caption", "title", "name",
})

# A URL / data-URI / file path dropped in as alt instead of a description.
_URL_RE = re.compile(r"^(https?://|www\.|ftp://|file:|data:image/)", re.IGNORECASE)
# A filesystem-ish path: starts with a drive/slash and has NO spaces (real
# descriptions have spaces; paths don't).
_PATH_RE = re.compile(r"^([a-zA-Z]:[\\/]|\.{0,2}[\\/])\S+$")
# Just a pixel dimension, e.g. "1024x768", "300 x 250", "640X480 px".
_DIMENSIONS_RE = re.compile(r"^\d{2,5}\s*[x×*]\s*\d{2,5}(\s*px)?$", re.IGNORECASE)


def is_nondescriptive_alt(alt: str) -> bool:
    """True when ``alt`` is present but provides no real description — a filename,
    URL/path, raw dimensions, generic filler, or a word that names the image's
    kind but not its content. Conservative by design: a genuinely short-but-valid
    description ("Red car", "Map of Europe", "Acme Corp logo") is never flagged,
    because a false positive here would cause good alt text to be regenerated."""
    a = (alt or "").strip()
    if not a:
        return False  # empty is the MissingAltText case, not this one
    low = a.lower()
    if low in _JUNK_ALTS:
        return True
    if low.endswith(_IMG_EXTS):
        return True
    if _URL_RE.match(a) or _PATH_RE.match(a):
        return True
    if _DIMENSIONS_RE.match(a):
        return True
    if _PLACEHOLDER_RE.match(a):
        return True
    if _LOCATION_ONLY_RE.match(a):
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
