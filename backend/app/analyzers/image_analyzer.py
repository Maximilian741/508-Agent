"""Image-related analyzers."""

from __future__ import annotations

import re
from typing import Dict, Set

from app.analyzers.base import Analyzer
from app.analyzers.helpers import attach_flag, iter_nodes
from app.models.accessibility import (
    AccessibilityFlagCode,
    AccessibilityTree,
    DocumentNode,
    ImageNode,
    SectionNode,
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
# "Image page-3-img2 shown in page 3." / "Image html-img-1 shown in the
# document." / "Picture slide 4" — names WHERE the image is, never WHAT it
# shows. This is exactly the string our own heuristic alt provider emits when
# it has no caption or nearby text to work from (the HTML variant says "in the
# document" because there is no page), so recognizing it here is what lets us
# refuse to ship it and lets a re-audit of our own output stay honest.
# "Image of the document signing ceremony" is NOT matched: no "shown in".
_LOCATION_ONLY_RE = re.compile(
    r"^(?:[a-z]+\s+)?(?:image|picture|figure|graphic|photo|img)\b.*?"
    r"\bshown\s+(?:in|on)\s+(?:the\s+)?(?:page|slide|sheet|document|deck|file|image)(?:\s*\d+)?\.?$"
    r"|^(?:image|picture|figure|graphic|photo|img)\b.*?\b(?:page|slide|sheet)\s*\d+\.?$",
    re.IGNORECASE,
)
# Our own old heuristic output: "Image html-img-1 — Home About Contact Login",
# "Image docx-img-1 — Manager email: Click or tap here…", "Image page-3-img2 —
# …". It led with an internal node id (an ordinal counter, meaningless to a
# reader) and then pasted whatever text sat near the picture. No person writes
# "Image slide-2-img-1", so recognizing the id is enough — and it lets a
# re-audit of a file we remediated before this fix flag what we wrote.
_NODE_ID_ALT_RE = re.compile(
    r"^(?:image|picture|figure|graphic|photo|img)\s+[a-z0-9]+(?:-[a-z0-9]+)*-?img-?\d+\b",
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
    if _NODE_ID_ALT_RE.match(a):
        return True
    if _CAMERA_RE.match(a):
        return True
    return False


def scanned_page_numbers(tree: AccessibilityTree) -> Set[int]:
    """Pages of a SCANNED PDF whose only content is the picture of the page.

    Mirrors ScannedDocumentAnalyzer's document test (>= 80% of pages
    image-only and < 50 extracted characters per page on average), then keeps
    the pages that carry an image and < 50 characters of text in the tree
    AND whose pictures cover at least half of the page. A scan is a picture
    OF the page; a one-page flyer with a 200 pt photo and a one-line heading
    also has < 50 characters, but its photo is a figure that needs alt text,
    and dropping that finding would hide the one fix the file needs.
    A picture whose box the parser could not measure counts as covering its
    page (the scan reading is the one we already had for it).
    Empty for anything that is not a scanned PDF.
    """
    root = tree.root
    if (root.metadata.source_format or "").lower() != "pdf":
        return set()
    props = root.metadata.properties or {}
    try:
        pages = int(props.get("page_count") or 0)
        image_pages = int(props.get("image_only_pages") or 0)
        total_chars = int(props.get("total_text_chars") or 0)
    except (TypeError, ValueError):
        return set()
    if pages == 0 or not (image_pages >= pages * 0.8 and total_chars / pages < 50):
        return set()
    text_chars: Dict[int, int] = {}
    image_on: Set[int] = set()
    covered: Dict[int, float] = {}
    for node in iter_nodes(tree):
        page = getattr(node.metadata, "page", None)
        if not isinstance(page, int):
            continue
        if isinstance(node, ImageNode):
            image_on.add(page)
            covered[page] = covered.get(page, 0.0) + _page_share(node)
        elif not isinstance(node, (DocumentNode, SectionNode)) and node.content and node.content.text:
            text_chars[page] = text_chars.get(page, 0) + len(node.content.text.strip())
    return {p for p in image_on if text_chars.get(p, 0) < 50 and covered.get(p, 0.0) >= 0.5}


def _page_share(node: ImageNode) -> float:
    """The share of its page a picture covers (1.0 when it can't be told)."""
    props = node.metadata.properties or {}
    box, size = props.get("bbox"), props.get("page_size")
    try:
        x0, y0, x1, y1 = (float(v) for v in list(box)[:4])
        w, h = (float(v) for v in list(size)[:2])
    except (TypeError, ValueError):
        return 1.0
    if w <= 0 or h <= 0:
        return 1.0
    return max(0.0, abs(x1 - x0) * abs(y1 - y0)) / (w * h)


class MissingAltTextAnalyzer(Analyzer):
    name = "missing_alt_text"

    def analyze(self, tree: AccessibilityTree) -> None:
        # A scanned PDF is ONE problem (no text; it needs OCR), reported once
        # as SCANNED_DOCUMENT_NO_TEXT. The picture of each page is not a
        # figure that wants a description — describing a scan in alt text is
        # not the remedy — so those page images do not also each raise
        # MISSING_ALT_TEXT (a 12-page scan used to report 13 issues for one
        # root cause, 12 of them asking a person to "describe" a page).
        scan_pages = scanned_page_numbers(tree)
        for node in iter_nodes(tree):
            if isinstance(node, ImageNode) and not node.is_decorative:
                alt_text = (node.alt_text or "").strip()
                if not alt_text:
                    if scan_pages and getattr(node.metadata, "page", None) in scan_pages:
                        continue
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
