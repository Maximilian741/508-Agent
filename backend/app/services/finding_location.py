"""WHERE each finding is in the document — the ``location`` block of the API.

The shared contract (``/pipeline/analyze`` and ``/pipeline/remediate``)::

    location = {
        "kind":      "pdf-region" | "image" | "text" | "table" | "document",
        "page":      int | None,          # 1-based
        "bbox":      [x0, y0, x1, y1] | None,  # PDF user space, origin bottom-left
        "pageSize":  [w, h] | None,
        "snippet":   str | None,          # <= 200 chars of the surrounding text
        "highlight": str | None,          # the exact offending substring OF snippet
        "thumbnail": "data:image/png;base64,..." | None,  # <= 240 px, images only
    }

Rules this module keeps, because a location that points at the wrong thing is
worse than none:

* Every string it returns is text that really is in the document. It never
  stitches pieces together into a sentence the document doesn't contain, and
  ``highlight`` is only set when it is a literal substring of ``snippet``.
* It reads the tree AS FOUND. Callers must build locations BEFORE any executor
  mutates the tree (a rewritten link text would otherwise be shown as the
  problem).
* Geometry comes from what the parser recorded (``properties["bbox"]``,
  ``properties["page_size"]``) or, for PDF objects whose geometry lives in the
  file itself, from the source bytes — never from a guess:
    - link annotations and form-field widgets: their own ``/Rect``;
    - pictures: the transformation matrix in force where the page's content
      stream paints that XObject (``cm`` ... ``/Name Do``);
    - text (headings, paragraphs, typed lists, the words under a link): the
      text-show operators' measured start and advance, and ONLY when the
      finding's text occurs exactly once on its page. Two equal headings on
      one page get no box rather than possibly the wrong one.
  Every PDF box is page-relative (the MediaBox's lower-left corner is the
  origin, as ``pageSize`` is the MediaBox's size) — identical to raw user
  space for the usual ``[0 0 w h]`` MediaBox. On a page with ``/Rotate``,
  ``bbox`` and ``pageSize`` are turned to the page AS A VIEWER SHOWS IT
  (origin still bottom-left; a 90-degree page reports ``[h, w]``), because
  every viewer applies the rotation before anyone looks at the box.
* Thumbnails decode the image bytes the parser kept, or read the same image
  back from the source file by the reference the parser recorded (DOCX
  relationship id, PPTX shape id, PDF XObject name, HTML ``data:`` src). It
  NEVER fetches a URL. Pillow does the work; the count, pixel area and wall
  time are all capped, and any failure is simply ``thumbnail: None``. A PDF
  image is never handed to pypdf's decoder (which inflates a stream without
  limit): ``_pdf_xobject_image`` inflates at most the bytes the image's own
  dictionary declares, so a small upload can't expand into gigabytes.
* Nothing here may fail a request: every finding degrades to
  ``{"kind": "document", ...None}`` on any error.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import struct
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.models.accessibility import (
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    HeadingNode,
    ImageNode,
    LinkNode,
    ListItemNode,
    ListNode,
    ParagraphNode,
    SectionNode,
    TableCellNode,
    TableNode,
    TableRowNode,
)

logger = logging.getLogger(__name__)

SNIPPET_MAX = 200
THUMB_MAX_PX = 240
MAX_THUMBNAILS = 24
# Budget for ALL thumbnails of one response. A scan-like PDF can hold hundreds
# of full-page images; the location block must not become the slow part.
THUMB_BUDGET_SECONDS = 4.0
# Refuse to decode an image bigger than this many pixels (decompression-bomb
# guard, well under Pillow's own warning threshold).
MAX_THUMB_SOURCE_PIXELS = 40_000_000
MAX_THUMB_SOURCE_BYTES = 12 * 1024 * 1024
MAX_THUMB_PNG_BYTES = 160 * 1024
# Budget for reading PDF page geometry (content-stream walks) per response. A
# page is read at most once; past the budget a finding keeps its page number,
# snippet and page size and simply has no box.
GEOMETRY_BUDGET_SECONDS = 3.0
# Glyph extent above / below the baseline, in em, for a text highlight box.
# Horizontal extent and baseline are measured from the content stream; only
# the vertical padding of the box uses these typical font metrics.
_ASCENT_EM = 0.9
_DESCENT_EM = 0.25
# The contrast analyzer's float tolerance (a colour exactly on the threshold passes).
_CONTRAST_TOLERANCE = 0.05

_TABLE_FLAGS = {
    "TABLE_MISSING_HEADERS",
    "TABLE_HEADER_SCOPE_INVALID",
    "TABLE_CAPTION_MISSING",
    "TABLE_COMPLEX_NEEDS_SUMMARY",
    "TABLE_NESTED",
}
_ALT_QUALITY_FLAGS = {"ALT_TEXT_NOT_DESCRIPTIVE", "DECORATIVE_IMAGE_WITH_ALT"}
# Root-level COUNT findings that are really about individual elements; the
# location points at the first one.
_DOCUMENT_LEVEL_ELEMENT_RULES = {
    "FORM_FIELD_UNLABELED",
    "INPUT_AUTOCOMPLETE_MISSING",
    "POSITIVE_TABINDEX",
    "IFRAME_TITLE_MISSING",
    "LABEL_IN_NAME_MISMATCH",
}
# A typed list marker at the start of a line ("- ", "• ", "1. ", "a) ").
_LIST_MARKER_RE = re.compile(r"^\s*((?:[-*•‣◦▪–—·o])|(?:\(?\d{1,3}[.)])|(?:\(?[A-Za-z][.)]))(?=\s)")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def empty_location(kind: str = "document", page: Optional[int] = None) -> Dict[str, Any]:
    return {
        "kind": kind,
        "page": page,
        "bbox": None,
        "pageSize": None,
        "snippet": None,
        "highlight": None,
        "thumbnail": None,
    }


def build_locations(
    tree: AccessibilityTree,
    violations: Iterable[Any],
    source_format: str,
    source_path: Optional[Path] = None,
    *,
    thumbnails: bool = True,
) -> Dict[str, Dict[str, Any]]:
    """``{violation_id: location}`` for every violation. Never raises."""
    fmt = (source_format or "").lower()
    out: Dict[str, Dict[str, Any]] = {}
    try:
        ctx = _Context(tree, fmt, Path(source_path) if source_path else None, thumbnails)
    except Exception:  # pragma: no cover - defensive
        logger.debug("finding_location: context build failed", exc_info=True)
        ctx = None
    for v in violations:
        vid = getattr(v, "violation_id", None)
        if not vid:
            continue
        if ctx is None:
            out[vid] = empty_location()
            continue
        try:
            out[vid] = ctx.locate(v)
        except Exception:
            logger.debug("finding_location: %s failed", vid, exc_info=True)
            out[vid] = empty_location()
    if ctx is not None:
        ctx.close()
    return out


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _norm(text: Optional[str]) -> str:
    return " ".join(str(text or "").split())


def _own_text(node: Any) -> str:
    content = getattr(node, "content", None)
    if content is not None and getattr(content, "kind", None) == ContentKind.TEXT:
        return _norm(getattr(content, "text", None))
    return ""


def _deep_text(node: Any, limit: int = SNIPPET_MAX * 2) -> str:
    """The node's own text, else its descendants' text in reading order."""
    own = _own_text(node)
    if own:
        return own[:limit]
    parts: List[str] = []
    total = 0
    stack = list(reversed(getattr(node, "children", None) or []))
    while stack and total < limit:
        child = stack.pop()
        t = _own_text(child)
        if t:
            parts.append(t)
            total += len(t) + 1
            continue
        stack.extend(reversed(getattr(child, "children", None) or []))
    return " ".join(parts)[:limit]


def _window(text: str, needle: str) -> Tuple[Optional[str], Optional[str]]:
    """``(snippet, highlight)``: <= SNIPPET_MAX chars of ``text`` around ``needle``.

    ``highlight`` is ``needle`` only when it really occurs in the snippet.
    """
    text = _norm(text)
    needle = _norm(needle)
    if not text:
        return None, None
    if not needle:
        return text[:SNIPPET_MAX], None
    if len(needle) >= SNIPPET_MAX:
        cut = needle[:SNIPPET_MAX]
        return cut, cut
    idx = text.find(needle)
    if idx < 0:
        return text[:SNIPPET_MAX], None
    if len(text) <= SNIPPET_MAX:
        return text, needle
    room = SNIPPET_MAX - len(needle)
    start = max(0, idx - room // 2)
    end = start + SNIPPET_MAX
    if end > len(text):
        end = len(text)
        start = max(0, end - SNIPPET_MAX)
    snippet = text[start:end]
    return snippet, (needle if needle in snippet else None)


def _first_row_snippet(table: Any) -> Optional[str]:
    rows = [c for c in (getattr(table, "children", None) or []) if isinstance(c, TableRowNode)]
    if not rows:
        text = _deep_text(table)
        return text[:SNIPPET_MAX] or None
    cells = [c for c in (rows[0].children or []) if isinstance(c, TableCellNode)]
    texts = [_deep_text(c, 80) for c in cells] or [_deep_text(rows[0])]
    joined = " | ".join(t for t in texts if t is not None)
    joined = _norm(joined)
    return joined[:SNIPPET_MAX] or None


# ---------------------------------------------------------------------------
# The per-request context
# ---------------------------------------------------------------------------


class _Context:
    def __init__(self, tree: AccessibilityTree, fmt: str, source: Optional[Path], thumbnails: bool) -> None:
        self.tree = tree
        self.fmt = fmt
        self.source = source if (source is not None and source.exists()) else None
        self.nodes: Dict[str, Any] = {}
        self.parent: Dict[str, Any] = {}
        self.pos: Dict[str, int] = {}  # index of a node among its parent's children
        self._index(tree.root)
        self.thumbs = _Thumbnailer(self.source, fmt) if thumbnails else None
        self._pdf_reader: Any = None
        self._pdf_failed = False
        self._pdf_link_rects: Dict[int, List[Tuple[Optional[str], List[float]]]] = {}
        self._pdf_geo: Dict[int, Optional["_PdfPageGeometry"]] = {}
        self._page_flat: Dict[int, str] = {}
        self._geo_deadline = time.monotonic() + GEOMETRY_BUDGET_SECONDS
        self._html_doc: Any = None

    # -- tree index ----------------------------------------------------------

    def _index(self, root: Any) -> None:
        stack: List[Tuple[Any, Any]] = [(root, None)]
        while stack:
            node, parent = stack.pop()
            nid = getattr(node, "id", None)
            if nid is not None and nid not in self.nodes:
                self.nodes[nid] = node
                if parent is not None:
                    self.parent[nid] = parent
            for i, child in enumerate(getattr(node, "children", None) or []):
                cid = getattr(child, "id", None)
                if cid is not None and cid not in self.pos:
                    self.pos[cid] = i
                stack.append((child, node))

    def _ancestors(self, node: Any) -> Iterable[Any]:
        cur = self.parent.get(getattr(node, "id", None))
        seen = 0
        while cur is not None and seen < 64:
            yield cur
            cur = self.parent.get(getattr(cur, "id", None))
            seen += 1

    def _position(self, node: Any) -> Tuple[Optional[List[Any]], int]:
        parent = self.parent.get(getattr(node, "id", None))
        if parent is None:
            return None, -1
        kids = getattr(parent, "children", None) or []
        i = self.pos.get(node.id, -1)
        if not (0 <= i < len(kids)) or kids[i] is not node:
            return None, -1
        return kids, i

    def _siblings_before(self, node: Any, limit: int = 4) -> List[Any]:
        kids, i = self._position(node)
        if kids is None:
            return []
        return list(reversed(kids[max(0, i - limit):i]))

    def _next_text_after(self, node: Any, limit: int = 6) -> str:
        kids, i = self._position(node)
        if kids is None:
            return ""
        for kid in kids[i + 1:i + 1 + limit]:
            t = _deep_text(kid)
            if t:
                return t
        return ""

    # -- geometry ------------------------------------------------------------

    def _page(self, node: Any) -> Optional[int]:
        for n in [node, *self._ancestors(node)]:
            page = getattr(getattr(n, "metadata", None), "page", None)
            if isinstance(page, int) and page >= 1:
                return page
        return None

    @staticmethod
    def _quad(value: Any) -> Optional[List[float]]:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            nums = [round(float(x), 2) for x in value]
        except (TypeError, ValueError):
            return None
        if nums[2] < nums[0]:
            nums[0], nums[2] = nums[2], nums[0]
        if nums[3] < nums[1]:
            nums[1], nums[3] = nums[3], nums[1]
        return nums

    @staticmethod
    def _pair(value: Any) -> Optional[List[float]]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None
        try:
            w, h = round(float(value[0]), 2), round(float(value[1]), 2)
        except (TypeError, ValueError):
            return None
        return [w, h] if w > 0 and h > 0 else None

    def _page_size(self, node: Any, page: Optional[int]) -> Optional[List[float]]:
        for n in [node, *self._ancestors(node)]:
            props = getattr(getattr(n, "metadata", None), "properties", None) or {}
            size = self._pair(props.get("page_size"))
            if size:
                return size
        root_props = getattr(self.tree.root.metadata, "properties", None) or {}
        sizes = root_props.get("page_sizes")
        if page and isinstance(sizes, list) and 0 < page <= len(sizes):
            size = self._pair(sizes[page - 1])
            if size:
                return size
        if self.fmt == "pdf" and page:
            reader = self._pdf()
            if reader is not None:
                try:
                    box = reader.pages[page - 1].mediabox
                    return self._pair([float(box.width), float(box.height)])
                except Exception:
                    return None
        return None

    def _pdf(self) -> Any:
        if self._pdf_reader is not None or self._pdf_failed or self.source is None or self.fmt != "pdf":
            return self._pdf_reader
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(self.source))
            if getattr(reader, "is_encrypted", False):
                try:
                    reader.decrypt("")
                except Exception:
                    pass
            self._pdf_reader = reader
        except Exception:
            self._pdf_failed = True
            self._pdf_reader = None
        return self._pdf_reader

    def _pdf_link_bbox(self, node: Any, page: Optional[int]) -> Optional[List[float]]:
        """The link annotation's /Rect, matched by order on the page AND target.

        The parser emits one LinkNode per /Link annotation, per page, in
        annotation order. The k-th LinkNode on page P is therefore the k-th
        /Link annotation on page P — and we only accept the match when the
        annotation's URI equals the node's target, so a future change in how
        links are extracted can't silently pin a box on the wrong link.
        """
        if self.fmt != "pdf" or not page:
            return None
        kids, idx = self._position(node)
        if kids is None:
            return None
        ordinal = sum(1 for kid in kids[:idx] if isinstance(kid, LinkNode))
        rects = self._pdf_link_rects.get(page)
        if rects is None:
            rects = []
            reader = self._pdf()
            try:
                if reader is not None:
                    pg = reader.pages[page - 1]
                    for ref in (pg.get("/Annots") or []):
                        annot = ref.get_object() if hasattr(ref, "get_object") else ref
                        if not hasattr(annot, "get") or annot.get("/Subtype") != "/Link":
                            continue
                        uri = None
                        action = annot.get("/A")
                        action = action.get_object() if hasattr(action, "get_object") else action
                        if hasattr(action, "get") and action.get("/URI") is not None:
                            # Same normalisation as the parser's _safe_text.
                            uri = str(action.get("/URI")).strip() or None
                        rects.append((uri, [float(x) for x in annot.get("/Rect") or []]))
            except Exception:
                rects = []
            self._pdf_link_rects[page] = rects
        if ordinal >= len(rects):
            return None
        uri, rect = rects[ordinal]
        target = getattr(node, "target", None)
        if (target or None) != (uri or None):
            return None
        return self._quad(rect)

    def _geometry(self, page: Optional[int]) -> Optional["_PdfPageGeometry"]:
        """The page's measured text runs and picture placements (read once)."""
        if self.fmt != "pdf" or not page:
            return None
        if page in self._pdf_geo:
            return self._pdf_geo[page]
        geo: Optional[_PdfPageGeometry] = None
        if time.monotonic() <= self._geo_deadline:
            reader = self._pdf()
            if reader is not None:
                try:
                    if 1 <= page <= len(reader.pages):
                        geo = _PdfPageGeometry.read(reader.pages[page - 1])
                except Exception:
                    logger.debug("finding_location: page %s geometry failed", page, exc_info=True)
                    geo = None
        self._pdf_geo[page] = geo
        return geo

    def _page_text_occurrences(self, page: int, needle: str) -> int:
        """How often ``needle`` (whitespace-free) occurs in the tree's text for
        ``page`` — 0, 1 or 2 (meaning "more than once")."""
        if not self._page_flat:
            # One pass over the tree for every page (not one pass per page).
            parts: Dict[int, List[str]] = {}
            for n in self.nodes.values():
                p = getattr(getattr(n, "metadata", None), "page", None)
                if isinstance(p, int) and not isinstance(n, SectionNode):
                    parts.setdefault(p, []).append("".join(_own_text(n).split()))
            # chr(31) joins: a separator no document text contains.
            self._page_flat = {p: chr(31).join(texts) for p, texts in parts.items()}
            self._page_flat.setdefault(-1, "")  # marks the index as built
        flat = self._page_flat.get(page, "")
        first = flat.find(needle)
        if first < 0:
            return 0
        return 1 if flat.find(needle, first + 1) < 0 else 2

    def _pdf_text_bbox(self, page: Optional[int], text: str) -> Optional[List[float]]:
        """The box of ``text`` on ``page``, only when it occurs there exactly
        once — both in the parsed text and in the page's own text runs."""
        if self.fmt != "pdf" or not page or not text:
            return None
        needle = "".join(_norm(text).split())
        if len(needle) < 2 or self._page_text_occurrences(page, needle) > 1:
            return None
        geo = self._geometry(page)
        if geo is None:
            return None
        return self._quad(geo.find_text(needle))

    def _pdf_page_box(self, page: Optional[int]) -> Optional[Tuple[float, float, float, float]]:
        if self.fmt != "pdf" or not page:
            return None
        reader = self._pdf()
        if reader is None:
            return None
        try:
            box = reader.pages[page - 1].mediabox
            return float(box.left), float(box.bottom), float(box.width), float(box.height)
        except Exception:
            return None

    def _page_relative(self, bbox: List[float], page: Optional[int]) -> Optional[List[float]]:
        """Shift a user-space box so the MediaBox's lower-left is the origin
        (what ``pageSize`` measures), clipped to the page; None if it is off
        the page entirely. Unchanged when the source can't be read."""
        box = self._pdf_page_box(page)
        if box is None:
            return bbox
        ox, oy, w, h = box
        x0 = min(max(bbox[0] - ox, 0.0), w)
        x1 = min(max(bbox[2] - ox, 0.0), w)
        y0 = min(max(bbox[1] - oy, 0.0), h)
        y1 = min(max(bbox[3] - oy, 0.0), h)
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            return None
        return [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]

    def _pdf_rotation(self, page: int) -> int:
        reader = self._pdf()
        if reader is None:
            return 0
        try:
            rot = int(reader.pages[page - 1].rotation) % 360
        except Exception:
            return 0
        return rot if rot in (90, 180, 270) else 0

    def _as_displayed(self, loc: Dict[str, Any]) -> None:
        """Turn ``bbox``/``pageSize`` to the way a viewer SHOWS a page with
        ``/Rotate`` (origin still bottom-left). A viewer applies /Rotate, so a
        box in unrotated user space would land in the wrong place on a
        90-degree page; for the usual unrotated page nothing changes."""
        rot = self._pdf_rotation(loc["page"])
        if not rot:
            return
        w, h = loc["pageSize"]
        if rot in (90, 270):
            loc["pageSize"] = [h, w]
        box = loc["bbox"]
        if box is None:
            return

        def turn(x: float, y: float) -> Tuple[float, float]:
            if rot == 90:  # clockwise: the left edge becomes the top edge
                return y, w - x
            if rot == 180:
                return w - x, h - y
            return h - y, x  # 270: counter-clockwise

        (ax, ay), (bx, by) = turn(box[0], box[1]), turn(box[2], box[3])
        loc["bbox"] = [round(min(ax, bx), 2), round(min(ay, by), 2), round(max(ax, bx), 2), round(max(ay, by), 2)]

    # -- the location --------------------------------------------------------

    def locate(self, v: Any) -> Dict[str, Any]:
        rule = str(getattr(v, "rule_id", "") or "")
        node_id = getattr(getattr(v, "location", None), "node_id", None)
        node = self.nodes.get(node_id)
        if node is None:
            return empty_location()
        page = self._page(node)
        loc = empty_location(page=page)
        props = getattr(node.metadata, "properties", None) or {}
        bbox = self._quad(props.get("bbox"))

        if isinstance(node, DocumentNode):
            loc["kind"] = "document"
            if rule in _DOCUMENT_LEVEL_ELEMENT_RULES:
                self._document_level(loc, rule)
            elif rule == "LOW_CONTRAST_TEXT" and self.fmt == "pdf":
                self._pdf_contrast(loc, props)
            if loc["page"] is not None and self.fmt == "pdf":
                loc["pageSize"] = self._page_size(node, loc["page"])
        elif isinstance(node, ImageNode):
            loc["kind"] = "image"
            if rule in _ALT_QUALITY_FLAGS and getattr(node, "alt_text", None):
                alt = _norm(node.alt_text)[:SNIPPET_MAX]
                loc["snippet"], loc["highlight"] = alt or None, alt or None
            else:
                caption = _norm(props.get("caption"))[:SNIPPET_MAX]
                loc["snippet"] = caption or None
            if self.thumbs is not None:
                loc["thumbnail"] = self.thumbs.for_image(node, page)
            if bbox is None and self.fmt == "pdf":
                # Where the page paints this picture (its first placement).
                geo = self._geometry(page)
                name = str(props.get("xobject") or "").lstrip("/")
                placements = geo.images.get(name) if (geo is not None and name) else None
                if placements:
                    bbox = self._quad(placements[0])
        elif isinstance(node, (TableNode, TableRowNode, TableCellNode)) or rule in _TABLE_FLAGS:
            loc["kind"] = "table"
            table = node
            if not isinstance(table, TableNode):
                for anc in self._ancestors(node):
                    if isinstance(anc, TableNode):
                        table = anc
                        break
            loc["snippet"] = _first_row_snippet(table)
        elif isinstance(node, LinkNode):
            self._link(node, loc, rule)
            if bbox is None:
                bbox = self._pdf_link_bbox(node, page)
            if bbox is not None and self.fmt == "pdf":
                self._pdf_link_words(loc, page, bbox)
        elif isinstance(node, HeadingNode):
            text = _own_text(node)
            if text:
                loc["snippet"], loc["highlight"] = _window(text, text)
                if bbox is None:
                    bbox = self._pdf_text_bbox(page, text)
            else:
                # An empty heading has no text of its own; show what follows it.
                after = self._next_text_after(node)
                loc["snippet"] = after[:SNIPPET_MAX] or None
            loc["kind"] = "text"
        elif isinstance(node, ParagraphNode) and rule == "LIST_STRUCTURE_INVALID":
            members = self._fake_list(node, loc)
            if bbox is None and self.fmt == "pdf":
                bbox = _union(
                    [self._pdf_text_bbox(self._page(m) or page, _own_text(m)) for m in members[:12]]
                )
        elif isinstance(node, ListNode):
            items = [_deep_text(c, 80) for c in (node.children or [])]
            joined = "\n".join(t for t in items if t)[:SNIPPET_MAX]
            loc["snippet"] = joined or None
            loc["kind"] = "text" if joined else "document"
        elif isinstance(node, SectionNode):
            # A page / slide level finding: show how that page starts.
            first = ""
            for kid in node.children or []:
                first = _deep_text(kid)
                if first:
                    break
            loc["snippet"] = first[:SNIPPET_MAX] or None
            loc["kind"] = "text" if first else "document"
        else:
            text = _deep_text(node)
            if text:
                loc["snippet"], loc["highlight"] = _window(text, text)
                loc["kind"] = "text"
                if bbox is None and _own_text(node):
                    bbox = self._pdf_text_bbox(page, _own_text(node))

        if bbox is not None:
            loc["bbox"] = bbox
        if loc["bbox"] is not None and self.fmt == "pdf":
            loc["bbox"] = self._page_relative(loc["bbox"], loc["page"])
        if loc["page"] is not None and loc["pageSize"] is None and (loc["bbox"] is not None or self.fmt == "pdf"):
            loc["pageSize"] = self._page_size(node, loc["page"])
        if loc["bbox"] is not None and loc["pageSize"] is not None:
            loc["kind"] = "pdf-region"
        elif loc["bbox"] is not None:
            # A box with no page frame to draw it in is no location at all.
            loc["bbox"] = None
        if self.fmt == "pdf" and loc["page"] is not None and loc["pageSize"] is not None:
            self._as_displayed(loc)
        # Contract invariant: highlight is a literal substring of snippet.
        if loc["highlight"] and (not loc["snippet"] or loc["highlight"] not in loc["snippet"]):
            loc["highlight"] = None
        return loc

    def _link(self, node: LinkNode, loc: Dict[str, Any], rule: str) -> None:
        text = _own_text(node) or _deep_text(node)
        if self.fmt == "pdf":
            # PDF link "text" is the URI or a placeholder, not words on the page.
            text = _own_text(node)
            if text in ("(link)", "link"):
                text = ""
        loc["kind"] = "text"
        # The sentence the link sits in, when the tree has it: an enclosing
        # block (HTML), else — PowerPoint only, where a shape's paragraphs
        # precede its links on the same slide — a preceding paragraph that
        # literally contains the link text.
        context = ""
        for anc in self._ancestors(node):
            if isinstance(anc, (ParagraphNode, ListItemNode, HeadingNode, TableCellNode)):
                t = _deep_text(anc, 2000)
                if t and (not text or text in t):
                    context = t
                break
            if isinstance(anc, (SectionNode, DocumentNode)):
                break
        if not context and text and self.fmt == "pptx":
            for sib in self._siblings_before(node):
                if isinstance(sib, LinkNode):
                    continue
                t = _deep_text(sib, 2000)
                if t and text in t:
                    context = t
                    break
        if context:
            loc["snippet"], loc["highlight"] = _window(context, text)
        elif text:
            loc["snippet"], loc["highlight"] = _window(text, text)
        else:
            loc["kind"] = "document" if loc["page"] is None else "text"

    def _pdf_contrast(self, loc: Dict[str, Any], props: Dict[str, Any]) -> None:
        """Point the document-level PDF contrast finding at the first text
        really painted in the flagged colour, at a size where that colour
        fails (a large heading in the same grey may pass), on a page without
        a coloured background — the parser's own rules for this finding."""
        finding = props.get("contrast_finding")
        if not isinstance(finding, dict):
            return
        fg = str(finding.get("fg") or "").upper()
        reader = self._pdf()
        if len(fg) != 6 or reader is None:
            return
        from app.analyzers.contrast import contrast_ratio, required_ratio

        ratio = contrast_ratio(fg, finding.get("bg") or "FFFFFF")
        if ratio is None:
            return
        # Its own slice of the geometry budget: this one document-level finding
        # must not starve every per-page finding of its box.
        own_deadline = min(self._geo_deadline, time.monotonic() + GEOMETRY_BUDGET_SECONDS / 3.0)
        for page in range(1, len(reader.pages) + 1):
            if time.monotonic() > own_deadline:
                return
            geo = self._geometry(page)
            if geo is None or geo.colored_bg:
                continue
            for i, run in enumerate(geo.runs):
                if run.fill != fg or not run.text.strip():
                    continue
                if ratio + _CONTRAST_TOLERANCE >= required_ratio(run.size, False):
                    continue
                # The rest of that line in the same colour and size (a TJ
                # array or a kerned line is several runs).
                parts, boxes, prev = [run.text], [run.box(0, len(run.text))], run
                for nxt in geo.runs[i + 1:i + 40]:
                    if nxt.fill != fg or nxt.size != run.size or abs(nxt.base - run.base) > 0.5 or nxt.x0 < prev.x1 - 0.5:
                        break
                    if nxt.x0 - prev.x1 > 0.15 * run.fh and not parts[-1].endswith(" ") and not nxt.text.startswith(" "):
                        parts.append(" ")
                    parts.append(nxt.text)
                    boxes.append(nxt.box(0, len(nxt.text)))
                    prev = nxt
                text = _norm("".join(parts))
                loc["page"] = page
                loc["bbox"] = self._quad(_union(boxes))
                loc["snippet"], loc["highlight"] = _window(text, text)
                loc["kind"] = "text"
                return

    def _pdf_link_words(self, loc: Dict[str, Any], page: Optional[int], rect: List[float]) -> None:
        """Show the words the link annotation covers on the page, when the
        page's text runs put any under its /Rect. (The tree's PDF link "text"
        is the URI or /Contents — not what a reader sees on the page.)"""
        geo = self._geometry(page)
        if geo is None:
            return
        context, words = geo.words_in(rect)
        if not words:
            return
        loc["snippet"], loc["highlight"] = _window(context, words)
        loc["kind"] = "text"

    def _fake_list(self, node: ParagraphNode, loc: Dict[str, Any]) -> List[Any]:
        props = node.metadata.properties or {}
        run_ids = props.get("fake_list_run_ids")
        members: List[Any] = []
        if isinstance(run_ids, list):
            members = [self.nodes[i] for i in run_ids if i in self.nodes]
        if not members:
            members = [node]
        lines: List[str] = []
        total = 0
        for m in members:
            t = _own_text(m)
            if not t:
                continue
            if total + len(t) > SNIPPET_MAX and lines:
                break
            lines.append(t)
            total += len(t) + 1
        snippet = "\n".join(lines)[:SNIPPET_MAX]
        loc["kind"] = "text"
        loc["snippet"] = snippet or None
        if lines:
            m = _LIST_MARKER_RE.match(lines[0])
            loc["highlight"] = m.group(1) if m else lines[0][:SNIPPET_MAX]
        return members

    # -- document-level findings that are really about ONE element -------------

    def _document_level(self, loc: Dict[str, Any], rule: str) -> None:
        """Point a root-level COUNT finding ("3 form fields have no label") at
        the FIRST offending element, found with the parser's own rules."""
        if self.source is None:
            return
        try:
            if self.fmt == "html":
                el = self._html_first_offender(rule)
                if el is not None:
                    self._html_markup(loc, el)
            elif rule == "FORM_FIELD_UNLABELED" and self.fmt == "pdf":
                self._pdf_form_field(loc)
            elif rule == "FORM_FIELD_UNLABELED" and self.fmt == "docx":
                self._docx_form_field(loc)
        except Exception:
            logger.debug("finding_location: %s lookup failed", rule, exc_info=True)

    def _html_first_offender(self, rule: str) -> Any:
        from app.parsers import html_parser as hp

        if self._html_doc is None:  # parsed once per request, not once per finding
            self._html_doc = hp._parse_document(self.source.read_bytes())
        doc = self._html_doc
        if rule == "FORM_FIELD_UNLABELED":
            labels_for = hp._build_labels_for(doc)
            for ctrl in hp._iter_labelable_controls(doc):
                if not hp._control_has_accessible_name(ctrl, labels_for):
                    return ctrl
        elif rule == "INPUT_AUTOCOMPLETE_MISSING":
            for ctrl, _token in hp.iter_autocomplete_candidates(doc):
                return ctrl
        elif rule == "POSITIVE_TABINDEX":
            for el in hp.iter_positive_tabindex(doc):
                return el
        elif rule == "IFRAME_TITLE_MISSING":
            for tag in ("iframe", "frame"):
                for el in doc.iter(tag):
                    if any((el.get(a) or "").strip() for a in ("title", "aria-label", "aria-labelledby")):
                        continue
                    if (el.get("role") or "").strip().lower() in {"presentation", "none"} or el.get("hidden") is not None:
                        continue
                    return el
        elif rule == "LABEL_IN_NAME_MISMATCH":
            for tag in hp._LABEL_IN_NAME_TAGS:
                for el in doc.iter(tag):
                    label = (el.get("aria-label") or "").strip()
                    visible = hp._speech_normalize(hp._visible_subtree_text(el)) if label else ""
                    if label and len(visible) >= 2 and hp._speech_normalize(label).find(visible) == -1:
                        return el
        return None

    @staticmethod
    def _html_markup(loc: Dict[str, Any], el: Any) -> None:
        """The element's own markup, highlighted inside its parent's markup."""
        from lxml import etree

        tag = _norm(etree.tostring(el, method="html", with_tail=False, encoding="unicode"))
        if len(tag) > SNIPPET_MAX:
            tag = tag[: tag.find(">") + 1] if ">" in tag else tag[:SNIPPET_MAX]
        parent = el.getparent()
        outer = ""
        if parent is not None:
            outer = _norm(etree.tostring(parent, method="html", with_tail=False, encoding="unicode"))
        if tag and outer and tag in outer:
            loc["snippet"], loc["highlight"] = _window(outer, tag)
        elif tag:
            loc["snippet"], loc["highlight"] = _window(tag, tag)
        loc["kind"] = "text"

    def _pdf_form_field(self, loc: Dict[str, Any]) -> None:
        reader = self._pdf()
        if reader is None:
            return
        from app.parsers.pdf_parser import iter_acroform_fields

        root = reader.trailer.get("/Root", {})
        root = root.get_object() if hasattr(root, "get_object") else root
        acro = root.get("/AcroForm") if root else None
        acro = acro.get_object() if hasattr(acro, "get_object") else acro
        if not acro:
            return
        page_of: Dict[int, int] = {}
        for i, pg in enumerate(reader.pages, start=1):
            ref = getattr(pg, "indirect_reference", None)
            if ref is not None:
                page_of[ref.idnum] = i
        for fo in iter_acroform_fields(acro):
            tu = fo.get("/TU")
            if tu and str(tu).strip():
                continue
            name = _norm(str(fo.get("/T") or ""))
            widget = fo
            kids = fo.get("/Kids")
            if "/Rect" not in fo and kids:
                try:
                    first = kids[0]
                    widget = first.get_object() if hasattr(first, "get_object") else first
                except Exception:
                    widget = fo
            if name:
                loc["snippet"], loc["highlight"] = name[:SNIPPET_MAX], name[:SNIPPET_MAX]
                loc["kind"] = "text"
            page_ref = widget.get("/P") if hasattr(widget, "get") else None
            page = page_of.get(getattr(page_ref, "idnum", None)) if page_ref is not None else None
            if page is None:
                wref = getattr(widget, "indirect_reference", None)
                for i, pg in enumerate(reader.pages, start=1):
                    for a in pg.get("/Annots") or []:
                        if wref is not None and getattr(a, "idnum", None) == wref.idnum:
                            page = i
                            break
                    if page is not None:
                        break
            rect = self._quad(list(widget.get("/Rect") or [])) if hasattr(widget, "get") else None
            if page is not None:
                loc["page"] = page
                if rect is not None:
                    loc["bbox"] = rect
            return

    def _docx_form_field(self, loc: Dict[str, Any]) -> None:
        from lxml import etree

        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        with zipfile.ZipFile(self.source) as zf:
            info = zf.getinfo("word/document.xml")
            if info.file_size > 64 * 1024 * 1024:
                return
            root = etree.fromstring(zf.read(info), parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))
        body = root.find(f"{ns}body")
        if body is None:
            return
        for sdt in body.iter(f"{ns}sdt"):
            alias = sdt.find(f"{ns}sdtPr/{ns}alias")
            val = alias.get(f"{ns}val") if alias is not None else None
            if val and str(val).strip():
                continue
            content = sdt.find(f"{ns}sdtContent")
            ctrl_text = _norm("".join(t.text or "" for t in (content if content is not None else sdt).iter(f"{ns}t")))
            para = sdt.getparent()
            while para is not None and para.tag != f"{ns}p":
                para = para.getparent()
                if para is not None and para.tag == f"{ns}body":
                    para = None
                    break
            context = _norm("".join(t.text or "" for t in para.iter(f"{ns}t"))) if para is not None else ""
            if context and ctrl_text and ctrl_text in context:
                loc["snippet"], loc["highlight"] = _window(context, ctrl_text)
            elif ctrl_text:
                loc["snippet"], loc["highlight"] = _window(ctrl_text, ctrl_text)
            elif context:
                loc["snippet"] = context[:SNIPPET_MAX]
            loc["kind"] = "text" if loc["snippet"] else "document"
            return

    def close(self) -> None:
        if self.thumbs is not None:
            self.thumbs.close()


# ---------------------------------------------------------------------------
# PDF page geometry, measured from the content stream
# ---------------------------------------------------------------------------


def _union(boxes: Iterable[Optional[List[float]]]) -> Optional[List[float]]:
    real = [b for b in boxes if b]
    if not real:
        return None
    return [min(b[0] for b in real), min(b[1] for b in real), max(b[2] for b in real), max(b[3] for b in real)]


def _mat_mult(m: Tuple[float, ...], n: Tuple[float, ...]) -> Tuple[float, ...]:
    """``m x n`` for PDF matrices ``[a b c d e f]`` (row-vector convention)."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (
        a * A + b * C,
        a * B + b * D,
        c * A + d * C,
        c * B + d * D,
        e * A + f * C + E,
        e * B + f * D + F,
    )


def _image_placements(ops: List[Tuple[Any, bytes]]) -> Dict[str, List[List[float]]]:
    """``{xobject name: [bbox, ...]}`` for every ``Do`` in a page's own content
    stream: the unit square mapped through the CTM in force at that ``Do``."""
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    ctm = identity
    stack: List[Tuple[float, ...]] = []
    out: Dict[str, List[List[float]]] = {}
    for operands, op in ops:
        if op == b"q":
            stack.append(ctm)
        elif op == b"Q":
            ctm = stack.pop() if stack else identity
        elif op == b"cm" and len(operands) == 6:
            try:
                ctm = _mat_mult(tuple(float(x) for x in operands), ctm)
            except (TypeError, ValueError):
                continue
        elif op == b"Do" and operands:
            a, b, c, d, e, f = ctm
            xs = [e, a + e, c + e, a + c + e]
            ys = [f, b + f, d + f, b + d + f]
            name = str(operands[0]).lstrip("/")
            out.setdefault(name, []).append([min(xs), min(ys), max(xs), max(ys)])
    return out


class _PdfRun:
    """One string shown by a text operator: its decoded text, the x of every
    character boundary, its baseline and font height (all user space), and
    the fill colour it was painted with (None when not a plain rg/g/k)."""

    __slots__ = ("text", "xs", "x0", "x1", "base", "fh", "size", "fill")

    def __init__(self, text: str, xs: List[float], base: float, fh: float, size: float, fill: Optional[str]) -> None:
        self.text = text
        self.xs = xs  # len(text) + 1 boundaries, left to right
        self.x0 = xs[0]
        self.x1 = xs[-1]
        self.base = base
        self.fh = fh
        self.size = size
        self.fill = fill

    def x_at(self, i: int) -> float:
        """x where character ``i`` starts (``len(text)`` = where the run ends)."""
        return self.xs[max(0, min(i, len(self.xs) - 1))]

    def box(self, i: int, j: int) -> List[float]:
        xa, xb = self.x_at(i), self.x_at(j)
        return [min(xa, xb), self.base - _DESCENT_EM * self.fh, max(xa, xb), self.base + _ASCENT_EM * self.fh]


# Fill-colour reading mirrors pdf_parser._pdf_text_colors (rg / g / k, and a
# page that paints a non-white fill is a "coloured background" page), except
# that q/Q restore the colour as a renderer does and any other colour-setting
# operator makes it unknown — so a run is only ever tied to a colour it was
# really painted with.
_PAINT_FILL_OPS = {b"f", b"F", b"f*", b"b", b"b*", b"B", b"B*"}
_UNKNOWN_FILL_OPS = {b"sc", b"scn", b"cs"}


def _hex_rgb(channels: Any) -> str:
    r, g, b = (max(0, min(255, round(float(c) * 255))) for c in channels[:3])
    return f"{r:02X}{g:02X}{b:02X}"


def _hex_cmyk(channels: Any) -> str:
    c, m, y, k = (float(x) for x in channels[:4])
    vals = (round(255 * (1 - c) * (1 - k)), round(255 * (1 - m) * (1 - k)), round(255 * (1 - y) * (1 - k)))
    return "".join(f"{max(0, min(255, v)):02X}" for v in vals)


def _whiteish(hex6: Optional[str]) -> bool:
    try:
        return all(int(hex6[i:i + 2], 16) >= 242 for i in (0, 2, 4))  # type: ignore[index]
    except (TypeError, ValueError):
        return False


def _obj(value: Any) -> Any:
    return value.get_object() if hasattr(value, "get_object") else value


class _PageFont:
    __slots__ = ("encoding", "char_map", "font_dictionary")

    def __init__(self, encoding: Any, char_map: Any, font_dictionary: Any) -> None:
        self.encoding = encoding
        self.char_map = char_map
        self.font_dictionary = font_dictionary


def _page_fonts(page: Any) -> Dict[Any, _PageFont]:
    """``{resource name: decoding maps + font dict}`` for the page (and its
    inherited resources), ONE font at a time: pypdf's own page-level helper
    gives up on the whole page when a single font's ToUnicode map is one it
    can't parse, and a font we can't decode is simply not measured."""
    from pypdf._cmap import build_char_map_from_dict

    fonts: Dict[Any, _PageFont] = {}
    node: Any = page
    hops = 0
    while node is not None and hops < 32:
        hops += 1
        try:
            resources = _obj(node.get("/Resources"))
            font_dict = _obj(resources.get("/Font")) if hasattr(resources, "get") else None
        except Exception:
            font_dict = None
        if hasattr(font_dict, "items"):
            for name, ref in font_dict.items():
                if name in fonts:
                    continue
                try:
                    ft = _obj(ref)
                    _subtype, _half_space, encoding, char_map = build_char_map_from_dict(200.0, ft)
                    fonts[name] = _PageFont(encoding, char_map, ft)
                except Exception:
                    continue
        try:
            node = _obj(node.get("/Parent"))
        except Exception:
            node = None
    return fonts


class _FontMeasure:
    """Glyph advances for one font, read from the font itself — never guessed.

    * simple fonts (Type1 / TrueType / MMType1): ``/Widths`` indexed by the
      character CODE (``/FirstChar``), with ``/MissingWidth`` from the font
      descriptor; a non-embedded standard-14 font without ``/Widths`` uses the
      Core14 AFM metrics (app.services.pdf_core14_widths);
    * Type0 with Identity-H: ``/W`` by CID, ``/DW`` otherwise (spec default
      1000);
    * anything else (Type3, other CMaps, vertical writing): not measurable.

    Text comes from pypdf's decoding (the font's encoding, then its ToUnicode
    map), one piece per code, so every character boundary is a glyph edge.
    """

    def __init__(self, font: Any) -> None:
        self.font = font
        fd = getattr(font, "font_dictionary", None) or {}
        self.subtype = str(_obj(fd.get("/Subtype")) or "")
        self.ok = False
        self.code_widths: Optional[Dict[int, float]] = None
        self.missing: Optional[float] = None
        self.core: Optional[Dict[str, int]] = None
        self.cid_widths: Dict[int, float] = {}
        self.dw = 1000.0
        try:
            if self.subtype == "/Type0":
                self._read_cid_widths(fd)
            elif self.subtype in ("/Type1", "/TrueType", "/MMType1"):
                widths = _obj(fd.get("/Widths"))
                if widths is not None:
                    first = int(_obj(fd.get("/FirstChar")) or 0)
                    self.code_widths = {first + k: float(_obj(v)) for k, v in enumerate(widths)}
                    desc = _obj(fd.get("/FontDescriptor"))
                    if hasattr(desc, "get") and desc.get("/MissingWidth") is not None:
                        self.missing = float(_obj(desc.get("/MissingWidth")))
                    self.ok = True
                else:
                    from app.services.pdf_core14_widths import core14_widths

                    self.core = core14_widths(str(_obj(fd.get("/BaseFont")) or ""))
                    self.ok = self.core is not None
        except Exception:
            self.ok = False

    def _read_cid_widths(self, fd: Any) -> None:
        if str(_obj(fd.get("/Encoding")) or "") != "/Identity-H":
            return
        kids = _obj(fd.get("/DescendantFonts")) or []
        desc = _obj(kids[0]) if len(kids) else None
        if not hasattr(desc, "get"):
            return
        if desc.get("/DW") is not None:
            self.dw = float(_obj(desc.get("/DW")))
        w = [_obj(x) for x in (_obj(desc.get("/W")) or [])]
        i = 0
        while i + 1 < len(w):
            first = int(w[i])
            nxt = w[i + 1]
            if isinstance(nxt, (list, tuple)) or (hasattr(nxt, "__iter__") and not isinstance(nxt, (str, bytes))):
                for k, width in enumerate(nxt):
                    self.cid_widths[first + k] = float(_obj(width))
                i += 2
            else:
                if i + 2 >= len(w):
                    return
                last, width = int(w[i + 1]), float(w[i + 2])
                for cid in range(first, min(last, first + 65535) + 1):
                    self.cid_widths[cid] = width
                i += 3
        self.ok = True

    def glyphs(self, raw: Any) -> Optional[List[Tuple[str, float, bool]]]:
        """``[(text, width in 1/1000 em, is single-byte code 32)]`` per glyph,
        or None when any glyph can't be both decoded and measured."""
        if not self.ok:
            return None
        if isinstance(raw, str):
            original = getattr(raw, "original_bytes", None)
            raw = original if isinstance(original, (bytes, bytearray)) else raw.encode("latin-1", "replace")
        data = bytes(raw)
        font = self.font
        char_map = font.char_map if isinstance(font.char_map, dict) else {}
        out: List[Tuple[str, float, bool]] = []
        if self.subtype == "/Type0":
            if len(data) % 2:
                return None
            for k in range(0, len(data), 2):
                cid = (data[k] << 8) | data[k + 1]
                text = char_map.get(chr(cid))
                if not isinstance(text, str) or not text:
                    return None
                out.append((text, self.cid_widths.get(cid, self.dw), False))
            return out
        encoding = font.encoding
        for code in data:
            if isinstance(encoding, dict):
                ch = encoding.get(code, chr(code))
            else:
                try:
                    ch = bytes((code,)).decode(encoding or "latin-1")
                except (LookupError, UnicodeDecodeError):
                    return None
            text = char_map.get(ch, ch)
            if not isinstance(text, str):
                text = ch
            if self.code_widths is not None:
                width = self.code_widths.get(code, self.missing)
            else:
                width = self.core.get(ch) if self.core is not None else None
            if width is None:
                return None
            out.append((text, float(width), code == 32))
        return out


def _text_runs(page: Any, ops: List[Tuple[Any, bytes]]) -> Tuple[List[_PdfRun], bool]:
    """The page's upright text runs, measured with the PDF text model, and
    whether the page paints a coloured background.

    Our own small text-state machine (Tm/Tlm, Td/TD/T*/'/", Tc/Tw/Tz/TL/Ts,
    cm with q/Q, TJ kerning, and the advance after EVERY shown string — which
    pypdf 4.2's layout mode omits, putting consecutive Tj runs all at the line
    start). Glyph widths come from the font itself (:class:`_FontMeasure`;
    pypdf's layout-mode Font is used only for its decoding maps). A string holding a
    glyph whose width the font doesn't give is NOT measured, and nothing else
    on that line is either until the text position is set again (Td, Tm, T*
    ...): a box is measured or it isn't produced. Rotated or mirrored text is
    left out.
    """
    fonts = _page_fonts(page)
    identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    ctm = identity
    tm = tlm = identity
    measure: Optional[_FontMeasure] = None
    measures: Dict[Any, _FontMeasure] = {}
    tfs = tc = tw = ts = tl = 0.0
    tz = 100.0
    fill: Optional[str] = "000000"
    stack: List[Tuple[Any, ...]] = []
    lost = False  # the current x on this line is unknown (an unmeasured string)
    colored_bg = False
    runs: List[_PdfRun] = []

    def move(tx: float, ty: float) -> None:
        nonlocal tm, tlm, lost
        tlm = _mat_mult((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
        tm = tlm
        lost = False

    def show(raw: Any) -> None:
        nonlocal tm, lost
        glyphs = measure.glyphs(raw) if (measure is not None and not lost) else None
        if glyphs is None:
            lost = True  # an unmeasured glyph: no box, and the line's x is now unknown
            return
        th = tz / 100.0
        pieces: List[str] = []
        advances = [0.0]
        x = 0.0
        for text_piece, width, is_space in glyphs:
            step = ((width / 1000.0) * tfs + tc + (tw if is_space else 0.0)) * th
            # A glyph that maps to several characters (a ligature) spans them evenly.
            for k in range(1, len(text_piece) + 1):
                advances.append(x + step * k / len(text_piece))
            pieces.append(text_piece)
            x += step
        text = "".join(pieces)
        m = _mat_mult(tm, ctm)
        a, b, c, d, e, f = m
        if abs(b) < 1e-6 and abs(c) < 1e-6 and a > 0 and d > 0 and text.strip():
            fh = abs(tfs) * d
            if fh > 0:
                xs = [a * t + e for t in advances]
                runs.append(_PdfRun(text, xs, d * ts + f, fh, tfs, fill))
        tm = _mat_mult((1.0, 0.0, 0.0, 1.0, x, 0.0), tm)

    for operands, op in ops:
        try:
            if op == b"q":
                stack.append((ctm, fill, measure, tfs, tc, tw, tz, tl, ts))
            elif op == b"Q":
                if stack:
                    ctm, fill, measure, tfs, tc, tw, tz, tl, ts = stack.pop()
            elif op == b"cm" and len(operands) == 6:
                ctm = _mat_mult(tuple(float(v) for v in operands), ctm)
            elif op == b"BT":
                tm = tlm = identity
                lost = False
            elif op == b"Tf" and len(operands) >= 2:
                name = operands[0]
                if name not in measures and name in fonts:
                    measures[name] = _FontMeasure(fonts[name])
                measure = measures.get(name)
                tfs = float(operands[1])
            elif op == b"Tc" and operands:
                tc = float(operands[0])
            elif op == b"Tw" and operands:
                tw = float(operands[0])
            elif op == b"Tz" and operands:
                tz = float(operands[0])
            elif op == b"TL" and operands:
                tl = float(operands[0])
            elif op == b"Ts" and operands:
                ts = float(operands[0])
            elif op == b"Td" and len(operands) >= 2:
                move(float(operands[0]), float(operands[1]))
            elif op == b"TD" and len(operands) >= 2:
                tl = -float(operands[1])
                move(float(operands[0]), float(operands[1]))
            elif op == b"Tm" and len(operands) == 6:
                tm = tlm = tuple(float(v) for v in operands)
                lost = False
            elif op == b"T*":
                move(0.0, -tl)
            elif op == b"Tj" and operands:
                show(operands[0])
            elif op == b"'" and operands:
                move(0.0, -tl)
                show(operands[0])
            elif op == b'"' and len(operands) >= 3:
                tw, tc = float(operands[0]), float(operands[1])
                move(0.0, -tl)
                show(operands[2])
            elif op == b"TJ" and operands:
                for part in operands[0]:
                    if isinstance(part, (int, float)) or type(part).__name__ in ("NumberObject", "FloatObject"):
                        tm = _mat_mult((1.0, 0.0, 0.0, 1.0, -float(part) / 1000.0 * tfs * tz / 100.0, 0.0), tm)
                    else:
                        show(part)
            elif op == b"rg" and len(operands) >= 3:
                fill = _hex_rgb(operands)
            elif op == b"g" and len(operands) >= 1:
                fill = _hex_rgb([operands[0]] * 3)
            elif op == b"k" and len(operands) >= 4:
                fill = _hex_cmyk(operands)
            elif op in _UNKNOWN_FILL_OPS:
                fill = None
            elif op in _PAINT_FILL_OPS and fill is not None and not _whiteish(fill):
                colored_bg = True
        except (TypeError, ValueError, KeyError, IndexError, UnicodeDecodeError):
            lost = True
    return runs, colored_bg


class _PdfPageGeometry:
    def __init__(
        self, runs: List[_PdfRun], images: Dict[str, List[List[float]]], colored_bg: bool = False
    ) -> None:
        self.runs = runs
        self.images = images
        self.colored_bg = colored_bg
        chars: List[str] = []
        where: List[Tuple[int, int]] = []
        for ri, run in enumerate(runs):
            for ci, ch in enumerate(run.text):
                if not ch.isspace():
                    chars.append(ch)
                    where.append((ri, ci))
        self._flat = "".join(chars)
        self._where = where

    @classmethod
    def read(cls, page: Any) -> "_PdfPageGeometry":
        from pypdf.generic import ContentStream

        if page.get("/Contents") is None:
            return cls([], {})
        ops = ContentStream(page["/Contents"].get_object(), page.pdf, "bytes").operations
        images = _image_placements(ops)
        try:
            runs, colored_bg = _text_runs(page, ops)
        except Exception:
            # An unusual font or operator: pictures keep their boxes; text
            # findings on this page simply get none.
            logger.debug("finding_location: text runs unavailable", exc_info=True)
            runs, colored_bg = [], True
        return cls(runs, images, colored_bg)

    def find_text(self, needle: str) -> Optional[List[float]]:
        """Box of the ONE place ``needle`` (whitespace-free) is drawn, else None."""
        if not needle:
            return None
        first = self._flat.find(needle)
        if first < 0 or self._flat.find(needle, first + 1) >= 0:
            return None
        spans: Dict[int, Tuple[int, int]] = {}
        for ri, ci in self._where[first:first + len(needle)]:
            lo, hi = spans.get(ri, (ci, ci))
            spans[ri] = (min(lo, ci), max(hi, ci))
        return _union(self.runs[ri].box(lo, hi + 1) for ri, (lo, hi) in spans.items())

    def words_in(self, rect: List[float]) -> Tuple[str, str]:
        """``(context, words)``: the characters whose centre is inside ``rect``,
        and the printed line they sit on (or, when that can't be rebuilt
        cleanly, the full text of the runs they belong to)."""
        x0, y0, x1, y1 = rect
        picked: List[Tuple[int, int, int]] = []
        for ri, run in enumerate(self.runs):
            mid = run.base + (_ASCENT_EM - _DESCENT_EM) / 2.0 * run.fh
            if not (y0 <= mid <= y1) or run.x1 < x0 or run.x0 > x1 or not run.text.strip():
                continue
            lo = hi = -1
            prev = run.x_at(0)
            for ci in range(len(run.text)):
                nxt = run.x_at(ci + 1)
                if x0 <= (prev + nxt) / 2.0 <= x1:
                    if lo < 0:
                        lo = ci
                    hi = ci
                prev = nxt
            if lo >= 0:
                picked.append((ri, lo, hi))
        if not picked:
            return "", ""
        line = self._line_of(picked)
        if line is not None:
            return line
        words = _norm(" ".join(self.runs[ri].text[lo:hi + 1] for ri, lo, hi in picked))
        context = _norm(" ".join(self.runs[ri].text for ri, _lo, _hi in picked))
        return context, words

    def _line_of(self, picked: List[Tuple[int, int, int]]) -> Optional[Tuple[str, str]]:
        """The printed line around the picked characters — a link is often its
        own text-show operation, and "click here" alone says little. Runs on
        the picked run's baseline, left to right, but only as far as the text
        is continuous: a gap wider than an em is a column gutter or a tab
        stop, and text across it is a different sentence (two columns share
        baselines). A space where the gap is wider than a sliver. None when
        the picked runs aren't all on one such stretch (wrapped link,
        overprinted text)."""
        anchor = self.runs[picked[0][0]]
        tol = 0.3 * max(anchor.fh, 1.0)
        same = sorted(
            (ri for ri, r in enumerate(self.runs) if abs(r.base - anchor.base) <= tol),
            key=lambda ri: self.runs[ri].x0,
        )
        # Continuous stretches of that baseline, overprints dropped.
        stretches: List[List[int]] = []
        prev: Optional[_PdfRun] = None
        for ri in same:
            r = self.runs[ri]
            if prev is not None and r.x0 < prev.x1 - 0.5:
                continue  # overlaps the previous run (overprint / fake bold)
            if prev is None or r.x0 - prev.x1 > 1.0 * max(r.fh, prev.fh):
                stretches.append([])
            stretches[-1].append(ri)
            prev = r
        wanted = {ri for ri, _lo, _hi in picked}
        home = [s for s in stretches if wanted & set(s)]
        if len(home) != 1 or not wanted <= set(home[0]):
            return None
        parts: List[str] = []
        offset: Dict[int, int] = {}
        pos = 0
        prev = None
        for ri in home[0]:
            r = self.runs[ri]
            if prev is not None and r.x0 - prev.x1 > 0.15 * r.fh and parts and not parts[-1].endswith(" ") and not r.text.startswith(" "):
                parts.append(" ")
                pos += 1
            offset[ri] = pos
            parts.append(r.text)
            pos += len(r.text)
            prev = r
        context = "".join(parts)
        a = min(offset[ri] + lo for ri, lo, _hi in picked)
        b = max(offset[ri] + hi + 1 for ri, _lo, hi in picked)
        words = _norm(context[a:b])
        return (_norm(context), words) if words else None


# ---------------------------------------------------------------------------
# Thumbnails
# ---------------------------------------------------------------------------


def png_thumbnail_data_uri(data: Any) -> Optional[str]:
    """``data:image/png;base64,...`` no wider/taller than THUMB_MAX_PX, or None.

    ``data`` is encoded image bytes or a PIL image. Never raises.
    """
    try:
        from PIL import Image

        if isinstance(data, (bytes, bytearray)):
            if not data or len(data) > MAX_THUMB_SOURCE_BYTES:
                return None
            im = Image.open(io.BytesIO(bytes(data)))
        elif hasattr(data, "size") and hasattr(data, "convert"):
            im = data
        else:
            return None
        w, h = im.size
        if w <= 0 or h <= 0 or w * h > MAX_THUMB_SOURCE_PIXELS:
            return None
        try:
            im.draft("RGB", (THUMB_MAX_PX * 2, THUMB_MAX_PX * 2))  # fast path for JPEG
        except Exception:
            pass
        if getattr(im, "n_frames", 1) > 1:
            try:
                im.seek(0)
            except Exception:
                pass
        if im.mode not in ("RGB", "RGBA", "L", "LA"):
            im = im.convert("RGBA" if ("A" in im.mode or im.mode == "P") else "RGB")
        im = im.copy()
        im.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        png = buf.getvalue()
        if len(png) > MAX_THUMB_PNG_BYTES:
            buf = io.BytesIO()
            im.convert("RGB").quantize(colors=128).save(buf, format="PNG", optimize=True)
            png = buf.getvalue()
            if len(png) > MAX_THUMB_PNG_BYTES:
                return None
        return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    except Exception:
        return None


class _Thumbnailer:
    def __init__(self, source: Optional[Path], fmt: str) -> None:
        self.source = source
        self.fmt = fmt
        self.count = 0
        self.deadline = time.monotonic() + THUMB_BUDGET_SECONDS
        self._docx_rels: Optional[Dict[str, str]] = None
        self._zip: Optional[zipfile.ZipFile] = None
        self._pptx: Any = None
        self._pdf: Any = None
        self._html: Any = None
        self._failed: set = set()

    def close(self) -> None:
        try:
            if self._zip is not None:
                self._zip.close()
        except Exception:
            pass

    def for_image(self, node: ImageNode, page: Optional[int]) -> Optional[str]:
        if self.count >= MAX_THUMBNAILS or time.monotonic() > self.deadline:
            return None
        props = node.metadata.properties or {}
        uri: Optional[str] = None
        # PDF image_b64 is the XObject's DECODED sample stream for Flate images
        # (not a PNG file), so for PDFs go to the source first.
        if self.fmt != "pdf":
            uri = self._from_b64(props.get("image_b64"))
        if uri is None and self.source is not None:
            try:
                data = self._from_source(props, page)
            except Exception:
                data = None
            if data is not None:
                uri = png_thumbnail_data_uri(data)
        if uri is None and self.fmt == "pdf":
            uri = self._from_b64(props.get("image_b64"))
        if uri is not None:
            self.count += 1
        return uri

    @staticmethod
    def _from_b64(b64: Any) -> Optional[str]:
        if not isinstance(b64, str) or not b64:
            return None
        if len(b64) > MAX_THUMB_SOURCE_BYTES * 4 // 3 + 4:
            return None
        try:
            raw = base64.b64decode(b64, validate=False)
        except Exception:
            return None
        return png_thumbnail_data_uri(raw)

    def _from_source(self, props: Dict[str, Any], page: Optional[int]) -> Any:
        if self.fmt in self._failed:
            return None
        try:
            if self.fmt == "docx":
                return self._docx(props.get("image_rid"))
            if self.fmt == "pptx":
                return self._pptx_image(props.get("shape_id"), page)
            if self.fmt == "pdf":
                return self._pdf_image(props.get("xobject"), page)
            if self.fmt == "html":
                return self._html_image(props.get("__xpath"))
        except (OSError, zipfile.BadZipFile):
            self._failed.add(self.fmt)
        return None

    # DOCX: the relationship id -> word/media/* bytes, straight from the zip.
    def _docx(self, rid: Any) -> Optional[bytes]:
        if not rid:
            return None
        if self._docx_rels is None:
            from lxml import etree

            self._zip = zipfile.ZipFile(self.source)
            rels: Dict[str, str] = {}
            try:
                xml = self._zip.read("word/_rels/document.xml.rels")
                root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
                for rel in root:
                    if (rel.get("TargetMode") or "").lower() == "external":
                        continue  # a linked picture: never fetched
                    rid_ = rel.get("Id")
                    target = rel.get("Target") or ""
                    if rid_ and target:
                        rels[rid_] = target
            except KeyError:
                pass
            self._docx_rels = rels
        target = self._docx_rels.get(str(rid))
        if not target:
            return None
        name = target.lstrip("/") if target.startswith("/") else f"word/{target}"
        # Normalise "word/../media/x.png" style paths.
        parts: List[str] = []
        for part in name.split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part and part != ".":
                parts.append(part)
        name = "/".join(parts)
        try:
            info = self._zip.getinfo(name)
        except KeyError:
            return None
        if info.file_size > MAX_THUMB_SOURCE_BYTES:
            return None
        return self._zip.read(info)

    # PPTX: the picture shape by id on its slide (group shapes included).
    def _pptx_image(self, shape_id: Any, page: Optional[int]) -> Optional[bytes]:
        if shape_id is None or not page:
            return None
        if self._pptx is None:
            from pptx import Presentation

            self._pptx = Presentation(str(self.source))
        slides = self._pptx.slides
        if page < 1 or page > len(slides):
            return None
        stack = list(slides[page - 1].shapes)
        while stack:
            shape = stack.pop()
            if getattr(shape, "shape_id", None) == shape_id:
                try:
                    blob = shape.image.blob
                except Exception:
                    return None
                return blob if blob and len(blob) <= MAX_THUMB_SOURCE_BYTES else None
            inner = getattr(shape, "shapes", None)
            if inner is not None:
                try:
                    stack.extend(list(inner))
                except Exception:
                    pass
        return None

    # PDF: the page's image XObject by name, decoded by _pdf_xobject_image —
    # NEVER by pypdf (``page.images`` / ``get_data()`` inflate without limit).
    def _pdf_image(self, name: Any, page: Optional[int]) -> Any:
        if not name or not page:
            return None
        if self._pdf is None:
            from pypdf import PdfReader

            self._pdf = PdfReader(str(self.source))
            if getattr(self._pdf, "is_encrypted", False):
                try:
                    self._pdf.decrypt("")
                except Exception:
                    return None
        pages = self._pdf.pages
        if page < 1 or page > len(pages):
            return None
        pg = pages[page - 1]
        key = "/" + str(name).lstrip("/")
        try:
            xo = _obj(_obj(_obj(pg["/Resources"])["/XObject"])[key])
        except Exception:
            return None
        return _pdf_xobject_image(xo)


    # HTML: only a data: URI src is ever decoded. Remote images are NEVER fetched.
    def _html_image(self, xpath: Any) -> Optional[bytes]:
        if not xpath:
            return None
        if self._html is None:
            from app.parsers import html_parser as hp

            self._html = hp._parse_document(self.source.read_bytes())
        found = self._html.getroottree().xpath(str(xpath))
        if not found:
            return None
        src = (found[0].get("src") or "").strip()
        if not src.lower().startswith("data:image/"):
            return None
        header, _, payload = src.partition(",")
        if ";base64" not in header.lower() or not payload:
            return None
        if len(payload) > MAX_THUMB_SOURCE_BYTES * 4 // 3 + 4:
            return None
        try:
            return base64.b64decode(payload, validate=False)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# PDF image XObjects, decoded with a hard memory bound
# ---------------------------------------------------------------------------
#
# A PDF image declares its size (/Width, /Height, colour space, bits per
# component), so the number of sample bytes it can MEAN is known before a
# single byte is inflated. pypdf 4.2 ignores that: ``page.images[...]`` and
# ``stream.get_data()`` inflate the whole Flate stream, and a ~1 MB upload that
# inflates to gigabytes took the server down through the thumbnail. Here every
# stream is inflated to at most the bytes its own dictionary says it holds (the
# rest is never inflated — a viewer ignores it too), a stream whose ENCODED size
# is over MAX_THUMB_SOURCE_BYTES is not read, anything bigger than
# MAX_THUMB_DECODED_BYTES decoded is not attempted, and a filter or colour
# space we can't bound is simply "no thumbnail".

# Decoded sample bytes one PDF thumbnail may produce (image, and separately its
# soft mask). A 300-dpi colour page (A4 or Letter) is ~26 MB.
MAX_THUMB_DECODED_BYTES = 32 * 1024 * 1024

_FLATE = {"/FlateDecode", "/Fl"}
_ASCII_HEX = {"/ASCIIHexDecode", "/AHx"}
_ASCII_85 = {"/ASCII85Decode", "/A85"}
_DCT = {"/DCTDecode", "/DCT"}
_JPX = {"/JPXDecode"}
_CCITT = {"/CCITTFaxDecode", "/CCF"}


def _bounded_inflate(data: bytes, limit: int) -> Optional[bytes]:
    """At most ``limit`` bytes of the zlib stream ``data``; the rest is never
    inflated. None if the stream is not zlib at all."""
    if limit <= 0:
        return b""
    try:
        return zlib.decompressobj().decompress(data, limit)
    except zlib.error:
        # A raw deflate stream without the zlib header (seen in the wild).
        try:
            return zlib.decompressobj(-zlib.MAX_WBITS).decompress(data, limit)
        except zlib.error:
            return None


def _pdf_filters(stream: Any) -> Optional[List[Tuple[str, Any]]]:
    """``[(filter name, its DecodeParms or None), ...]`` in decode order."""
    try:
        filters = _obj(stream.get("/Filter"))
        parms = _obj(stream.get("/DecodeParms", stream.get("/DP")))
        if filters is None:
            return []
        if not isinstance(filters, list):
            filters = [filters]
            parms = [parms]
        elif not isinstance(parms, list):
            parms = [parms] * len(filters) if parms is not None else []
        out: List[Tuple[str, Any]] = []
        for i, f in enumerate(filters):
            p = _obj(parms[i]) if i < len(parms) else None
            out.append((str(_obj(f)), p if hasattr(p, "get") else None))
        return out
    except Exception:
        return None


def _pdf_stream_bytes(stream: Any, limit: int) -> Optional[Tuple[bytes, str, Any]]:
    """``(bytes, codec, codec parms)`` for a PDF stream, never inflating more
    than ``limit`` bytes.

    ``codec`` is "" when ``bytes`` are the decoded samples; otherwise it is the
    image codec left to apply (DCT / JPX / CCITT, or "png" for a Flate stream
    with a PNG predictor, whose ``bytes`` are still the zlib data). None =
    unsupported (LZW, RunLength, JBIG2, a TIFF predictor...) or unreadable.
    """
    raw = getattr(stream, "_data", None)  # the bytes as stored: never get_data()
    if isinstance(raw, str):
        raw = raw.encode("latin-1", "replace")
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_THUMB_SOURCE_BYTES:
        return None
    filters = _pdf_filters(stream)
    if filters is None:
        return None
    data = bytes(raw)
    for i, (name, parms) in enumerate(filters):
        last = i == len(filters) - 1
        if name in _ASCII_HEX or name in _ASCII_85:
            from pypdf.filters import ASCII85Decode, ASCIIHexDecode

            try:  # at most 4x the input (ASCII85's "z"), which is capped above
                data = (ASCIIHexDecode if name in _ASCII_HEX else ASCII85Decode).decode(data)
            except Exception:
                return None
            if isinstance(data, str):
                data = data.encode("latin-1", "replace")
        elif name in _FLATE:
            try:
                predictor = int(_obj(parms.get("/Predictor", 1))) if parms is not None else 1
            except (TypeError, ValueError):
                return None
            if predictor >= 10 and last:
                return data, "png", parms
            if predictor > 1:
                return None  # a TIFF predictor, or PNG rows fed to another filter
            inflated = _bounded_inflate(data, limit)
            if inflated is None:
                return None
            data = inflated
        elif last and (name in _DCT or name in _JPX or name in _CCITT):
            return data, name, parms
        else:
            return None
    return data, "", None


def _pdf_colour_space(cs: Any, depth: int = 0) -> Optional[Tuple[str, int, Any]]:
    """``(kind, components, palette)`` — kind is gray | rgb | cmyk | indexed |
    separation; ``palette`` is the RGB lookup bytes of an Indexed space."""
    cs = _obj(cs)
    if depth > 3 or cs is None:
        return None
    if isinstance(cs, list):
        if not cs:
            return None
        head = str(_obj(cs[0]))
        if len(cs) == 1:
            return _pdf_colour_space(cs[0], depth + 1)
        if head == "/ICCBased":
            try:
                n = int(_obj(_obj(cs[1]).get("/N")))
            except Exception:
                return None
            return {1: ("gray", 1, None), 3: ("rgb", 3, None), 4: ("cmyk", 4, None)}.get(n)
        if head == "/CalRGB":
            return ("rgb", 3, None)
        if head == "/CalGray":
            return ("gray", 1, None)
        if head == "/Separation":
            return ("separation", 1, None)
        if head in ("/Indexed", "/I") and len(cs) >= 4:
            base = _pdf_colour_space(cs[1], depth + 1)
            if base is None or base[0] not in ("gray", "rgb", "cmyk"):
                return None
            try:
                hival = int(_obj(cs[2]))
            except (TypeError, ValueError):
                return None
            if not 0 <= hival <= 255:
                return None
            n = base[1]
            want = (hival + 1) * n
            lookup = _obj(cs[3])
            if hasattr(lookup, "get") and hasattr(lookup, "get_data"):  # a stream
                got = _pdf_stream_bytes(lookup, want)
                if got is None or got[1]:
                    return None
                table = got[0]
            else:
                table = getattr(lookup, "original_bytes", None)
                if table is None:
                    table = bytes(lookup) if isinstance(lookup, (bytes, bytearray)) else str(lookup).encode("latin-1", "replace")
            if len(table) < want:
                return None
            table = table[:want]
            if n == 1:
                rgb = b"".join(bytes((v, v, v)) for v in table)
            elif n == 3:
                rgb = bytes(table)
            else:  # CMYK entries: the naive conversion is plenty for a thumbnail
                out = bytearray()
                for j in range(0, want, 4):
                    c, m, y, k = table[j], table[j + 1], table[j + 2], table[j + 3]
                    out += bytes(int((255 - v) * (255 - k) / 255) for v in (c, m, y))
                rgb = bytes(out)
            return ("indexed", 1, rgb)
        return None
    name = str(cs)
    if name in ("/DeviceGray", "/G", "/CalGray"):
        return ("gray", 1, None)
    if name in ("/DeviceRGB", "/RGB", "/CalRGB"):
        return ("rgb", 3, None)
    if name in ("/DeviceCMYK", "/CMYK"):
        return ("cmyk", 4, None)
    return None


def _png_wrap(w: int, h: int, depth: int, colour_type: int, idat: bytes, plte: Optional[bytes]) -> bytes:
    """A PNG file around a PDF Flate stream that uses a PNG predictor — the
    same filtered-rows-in-zlib layout — so Pillow's C decoder un-filters it."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    head = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, colour_type, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + head + (chunk(b"PLTE", plte) if plte else b"") + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _pdf_xobject_image(xo: Any, soft_mask: bool = False) -> Any:
    """A PIL image (already thumbnail-sized) of a PDF image XObject, with alpha
    from its /SMask — or None.

    Memory is bounded by the image's OWN declared size, never by what its
    stream inflates to (see the section comment above). Never raises.
    """
    try:
        from PIL import Image, ImageOps

        if not hasattr(xo, "get") or xo.get("/Subtype") != "/Image":
            return None
        w = int(_obj(xo.get("/Width")) or 0)
        h = int(_obj(xo.get("/Height")) or 0)
        if w <= 0 or h <= 0 or w * h > MAX_THUMB_SOURCE_PIXELS:
            return None
        if bool(_obj(xo.get("/ImageMask", False))):  # a stencil: 1 bit, 0 = painted
            kind, ncomp, palette, bpc = "gray", 1, None, 1
        else:
            cs = _pdf_colour_space(xo.get("/ColorSpace"))
            kind, ncomp, palette = cs if cs is not None else ("", 0, None)
            bpc = int(_obj(xo.get("/BitsPerComponent", 8)) or 8)
        if soft_mask and kind != "gray":
            return None
        filters = _pdf_filters(xo)
        if filters is None:
            return None
        terminal = filters[-1][0] if filters else ""
        image_codec = terminal in _DCT or terminal in _JPX or terminal in _CCITT
        # The samples the dictionary declares. Raw / Flate images decode to
        # exactly this; an image codec (JPEG, JPEG 2000, fax) is always smaller.
        expected = ((w * max(ncomp, 1) * bpc + 7) // 8) * h
        if not image_codec and (not kind or expected > MAX_THUMB_DECODED_BYTES):
            return None
        got = _pdf_stream_bytes(xo, min(expected, MAX_THUMB_DECODED_BYTES))
        if got is None:
            return None
        data, codec, parms = got

        if codec in _DCT or codec in _JPX:
            img = Image.open(io.BytesIO(data))
            iw, ih = img.size
            if iw * ih > MAX_THUMB_SOURCE_PIXELS:
                return None
            if codec in _JPX and iw * ih * 4 > MAX_THUMB_DECODED_BYTES:
                return None  # JPEG 2000 decodes at full size
            try:
                img.draft("RGB", (THUMB_MAX_PX * 2, THUMB_MAX_PX * 2))  # JPEG: decode at 1/2..1/8 scale
            except Exception:
                pass
            img.load()
        elif codec in _CCITT:
            from pypdf.filters import CCITTFaxDecode

            # A TIFF header in front of the fax data; libtiff fills w x h and stops.
            img = Image.open(io.BytesIO(CCITTFaxDecode.decode(data, parms, h)), formats=("TIFF",))
            if img.size != (w, h):
                return None
            img.load()
        elif bpc not in (1, 2, 4, 8) or (kind in ("rgb", "cmyk") and bpc != 8):
            return None
        elif codec == "png":
            try:
                declared = (
                    int(_obj(parms.get("/Colors", 1))),
                    int(_obj(parms.get("/BitsPerComponent", 8))),
                    int(_obj(parms.get("/Columns", 1))),
                )
            except (TypeError, ValueError):
                return None
            if declared != (ncomp, bpc, w):
                return None
            plte = None
            if kind == "indexed":
                colour_type, plte = 3, palette[: 3 * (1 << bpc)]
            elif kind in ("gray", "separation"):
                colour_type = 0
            elif kind == "rgb":
                colour_type = 2
            else:  # CMYK: four bytes a pixel, un-filtered exactly like RGBA
                colour_type = 6
            img = Image.open(io.BytesIO(_png_wrap(w, h, bpc, colour_type, data, plte)), formats=("PNG",))
            # Pillow inflates only until the IHDR's rows are full: the same
            # bound as _bounded_inflate, in C.
            img.load()
            if kind == "cmyk":
                img = Image.frombytes("CMYK", img.size, img.tobytes())
        else:
            if len(data) < expected:
                return None  # truncated: never pad a picture with invented pixels
            if kind == "indexed":
                img = Image.frombytes("P", (w, h), data, "raw", "P" if bpc == 8 else f"P;{bpc}")
                img.putpalette(palette)
            elif kind in ("gray", "separation"):
                raw_mode = {1: "1", 2: "L;2", 4: "L;4", 8: "L"}[bpc]
                img = Image.frombytes("1" if bpc == 1 else "L", (w, h), data, "raw", raw_mode)
            else:
                img = Image.frombytes("RGB" if kind == "rgb" else "CMYK", (w, h), data)
        del data

        # /Decode: only the plain inversion [1 0] of a one-channel image is
        # honoured; any other remapping would show colours the page doesn't.
        invert = kind == "separation"  # tint 1.0 = full ink = dark
        decode = _obj(xo.get("/Decode"))
        if decode is not None and codec not in _DCT and codec not in _JPX:
            try:
                pairs = [float(_obj(v)) for v in decode]
            except (TypeError, ValueError):
                return None
            top = float((1 << bpc) - 1) if kind == "indexed" else 1.0
            if pairs and all(pairs[i] == (0.0 if i % 2 == 0 else top) for i in range(len(pairs))):
                pass
            elif kind in ("gray", "separation") and pairs[:2] == [1.0, 0.0]:
                invert = not invert
            else:
                return None

        # Shrink BEFORE any widening conversion (a palette image made RGBA
        # at full size would be 4 bytes a pixel).
        if img.mode == "1":
            img = img.convert("L")
        elif img.mode == "CMYK":
            img = img.convert("RGB")
        img.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
        if img.mode not in ("RGB", "RGBA", "L", "LA"):
            img = img.convert("RGBA" if ("A" in img.mode or "transparency" in img.info) else "RGB")
        if invert:
            img = ImageOps.invert(img.convert("L"))

        smask = None if soft_mask else _obj(xo.get("/SMask"))
        if smask is not None and hasattr(smask, "get"):
            alpha = _pdf_xobject_image(smask, soft_mask=True)
            if alpha is None:
                return None  # a mask we can't read: no half-drawn picture
            img = img.convert("RGBA")
            img.putalpha(alpha.convert("L").resize(img.size))
        return img
    except Exception:
        logger.debug("finding_location: PDF image thumbnail failed", exc_info=True)
        return None


__all__ = ["build_locations", "empty_location", "png_thumbnail_data_uri"]
