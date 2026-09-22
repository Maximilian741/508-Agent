"""Pragmatic PDF parser that produces an :class:`AccessibilityTree`.

The parser extracts what is most useful for static 508/WCAG analysis without
attempting a full PDF/UA tagged-tree implementation:

* Document metadata (title, language)
* Page-level text grouped into paragraph nodes
* Headings inferred from font-size heuristics on /Tj operands when available,
  falling back to leading-uppercase / short-line heuristics on extracted text.
* Image XObjects with ``/Alt`` markings (decorative if ``/Alt`` is empty or
  marked ``/Artifact``).
* Hyperlink annotations promoted to :class:`LinkNode` instances.

Anything we cannot reliably infer is left off the tree so the analyzers do not
emit false-positive flags.  The parser is intentionally tolerant of malformed
PDFs — in the worst case we still return a non-empty document with a single
section so the analyzer/remediator pipeline can run end-to-end.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    ContentStream,
    DictionaryObject,
    IndirectObject,
    NameObject,
    NullObject,
    NumberObject,
    TextStringObject,
)


class PdfPasswordProtectedError(ValueError):
    """The PDF needs a password to OPEN (a user password), so it cannot be read.

    Carries a ``user_message`` the API returns verbatim instead of the generic
    "invalid or corrupted file" — which is what a password-protected PDF used
    to be reported as.
    """

    user_message = (
        "This PDF is password-protected, so we can't open it. Remove the password "
        "(in Acrobat: File > Properties > Security > No Security) and upload it again. "
        "You were not charged."
    )

from app.parsers.document_id import derive_document_id
from app.models.accessibility import (
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    HeadingNode,
    ImageNode,
    LinkNode,
    ListItemNode,
    ListNode,
    NodeContent,
    NodeMetadata,
    ParagraphNode,
    ParserResult,
    SectionNode,
    TableCellNode,
    TableCellType,
    TableHeaderScope,
    TableNode,
    TableRowNode,
)


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------

# Max PDF pages analyzed per document — a guard against a single huge upload
# tying up a worker (each page is tokenized several times across parser +
# tagger). Overridable via env for hosts with more headroom.
try:
    _MAX_PDF_PAGES = max(1, int(os.environ.get("MAX_PDF_PAGES", "400")))
except (TypeError, ValueError):
    _MAX_PDF_PAGES = 400

# Decided once per process: is there a vision provider that could ever read
# inlined image bytes? See _extract_image_bytes call site.
try:
    from app.ai.semantic_inference import vision_provider_configured as _vpc

    _WANT_IMAGE_BYTES = bool(_vpc())
except Exception:  # pragma: no cover - never let the AI module break parsing
    _WANT_IMAGE_BYTES = True

_HEADING_RE = re.compile(r"^(?P<num>\d+(?:\.\d+){0,5})\s+(?P<text>.+)$")
_ALL_CAPS_RE = re.compile(r"^[A-Z0-9][A-Z0-9 \-:&,'\".]+$")


def _classify_paragraph(text: str) -> Optional[int]:
    """Return a heading level (1-6) if ``text`` looks like a heading, else None."""

    stripped = text.strip()
    if not stripped:
        return None
    if len(stripped) > 120:
        return None
    if "\n" in stripped:
        return None

    match = _HEADING_RE.match(stripped)
    if match:
        depth = match.group("num").count(".") + 1
        return max(1, min(depth, 6))

    if len(stripped) <= 80 and _ALL_CAPS_RE.match(stripped):
        # Short, mostly upper-case lines look like headings (e.g. "INTRODUCTION").
        return 1

    if len(stripped.split()) <= 8 and stripped.endswith(":"):
        return 2

    return None


def _compact(text: str) -> str:
    """Casefolded text with ALL whitespace removed, for tolerant containment."""
    return re.sub(r"\s+", "", (text or "")).casefold()


def _page_blocks(reader: PdfReader, page_index: int, font_cache: Optional[dict] = None):
    """Decorated text blocks of one page (see ua_tagger._iter_text_blocks)."""
    from app.pdf.ua_tagger import _iter_text_blocks, _page_decoder

    page = reader.pages[page_index]
    try:
        ops = ContentStream(page.get_contents(), reader).operations
    except Exception:
        return None
    return list(_iter_text_blocks(ops, _page_decoder(reader, page, font_cache))), page


# Largest-text lines that name a SECTION or a stamp, never the document.
_GENERIC_TITLE_RE = re.compile(
    r"^\s*(?:(?:table\s+of\s+)?contents|index|introduction|intro|abstract|overview|summary|"
    r"executive\s+summary|preface|foreword|acknowledg(?:e)?ments?|appendix(?:\s+\w{1,3})?|"
    r"(?:chapter|section|part|page)\s+[\divxlc]{1,4}|draft|confidential|sample|copy|void|"
    r"for\s+official\s+use\s+only|internal\s+use\s+only|untitled(?:\s+document)?)\s*[.:]?\s*$"
    # A numbered division of the document ("Chapter 1: Programme Area 1",
    # "Section 2 - Rates", "1. Introduction", "2.1 Rate Structure") names
    # the first section on the page, not the document.
    r"|^\s*(?:chapter|section|part|appendix|annex|article|unit|lesson|module)\s+[\divxlc]{1,4}\b"
    r"|^\s*(?:\d{1,2}(?:\.\d{1,2})*[.)]|\d{1,2}\.\d{1,2}(?:\.\d{1,2})*)\s+",
    re.IGNORECASE,
)


def _title_candidate_from_page(reader: PdfReader, page_index: int = 0) -> Optional[str]:
    """The largest-font text block on ``page_index``, if it reads as a title.

    Reuses the tagger's block helpers (same segmentation the structure tree
    is built from). Conservative: the block must be the strict maximum size
    on the page, at least 1.25x the document's BODY size (so it stands out
    from body text), short (<= 120 chars, one line), and not a page number.
    Anything less and we return None — a wrong title is worse than no title.

    Two ways this used to go wrong, both now closed:

    * Composite (Type0 / Identity-H) fonts. Block text was the raw operand
      bytes — 2-byte glyph ids — and "\\x00:\\x00L\\x00Q..." was written as the
      document /Title and charged. The candidate is now the font-DECODED text,
      must read as human text (no control characters), and must also appear
      in pypdf's own ``extract_text`` of the page. Anything we cannot decode,
      or cannot confirm, is refused.
    * Cover pages. "Body" was the most common size by BLOCK COUNT on page 1,
      so a cover with one 26pt title, one 14pt subtitle and one 10pt footer
      picked 26pt (ties resolve first) as body and refused an unmistakable
      title. Body is now the size carrying the most characters over the
      first few pages — where the prose actually is.
    """
    from app.pdf.text_decode import decode_ops, is_readable_text
    from app.pdf.ua_tagger import (
        _block_font_size,
        _block_text,
        _block_weight,
        _is_page_number,
        _norm_block_text,
        _off_axis,
        _page_axes,
        _page_decoder,
    )

    if page_index >= len(reader.pages):
        return None
    font_cache: dict = {}
    got = _page_blocks(reader, page_index, font_cache)
    if not got:
        return None
    blocks, page = got
    others = []
    for pi in range(page_index + 1, min(len(reader.pages), page_index + 5)):
        more = _page_blocks(reader, pi, font_cache)
        if more:
            others.append(more[0])
    # Text at an angle to the page's own text — a diagonal 72pt "DRAFT"
    # watermark — is the largest thing on the page and was offered as the
    # title (written, and charged). It is never a title. The page's axis is
    # voted by blocks with the document's recurring watermarks left out, so
    # a long stamp on a sparse cover cannot make the real title "off-axis".
    axes, marks = _page_axes([blocks] + others)
    axis = axes[0]
    sized = []
    for b in blocks:
        size = _block_font_size(b)
        if (size and size > 0 and _block_weight(b) > 0 and not _off_axis(b, axis)
                and _norm_block_text(b) not in marks):
            sized.append((round(float(size), 1), b))
    if not sized:
        return None

    # Body size: character-weighted over the first pages (page 1 included).
    from collections import Counter

    volume: Counter = Counter()
    for sz, b in sized:
        volume[sz] += _block_weight(b)
    for more_blocks, more_axis in zip(others, axes[1:]):
        for b in more_blocks:
            size = _block_font_size(b)
            if size and size > 0 and not _off_axis(b, more_axis):
                volume[round(float(size), 1)] += _block_weight(b)
    if len(volume) < 2:
        return None
    top_volume = max(volume.values())
    body = min(s for s, v in volume.items() if v == top_volume)
    top = max(sz for sz, _ in sized)
    if top < body * 1.25:
        return None
    tops = [b for sz, b in sized if sz == top]
    if len(tops) != 1:
        return None  # several equally-large blocks: no single title stands out

    decoder = _page_decoder(reader, page, font_cache)
    if decoder is None:
        return None
    target = tops[0]
    text, _composite, _end = decode_ops(
        target, decoder, getattr(target, "start_font", None), unicode_simple=True
    )
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) > 120 or _is_page_number(text):
        return None
    if not is_readable_text(text):
        return None
    if _GENERIC_TITLE_RE.match(text) or len([t for t in re.findall(r"[^\W\d_]{3,}", text)]) == 0:
        # "Contents", "Introduction", "Chapter 1", "DRAFT": the name of a
        # section or a stamp, not of the document. A wrong title is worse
        # than none — the missing-title finding stays open instead.
        return None
    # The same words on the next pages, set at an angle or as large as here:
    # a stamp or a banner ("DRAFT - NOT FOR DISTRIBUTION", "Internal Review
    # Copy"), not this document's name.
    key = _compact(text)
    for more_blocks, more_axis in zip(others, axes[1:]):
        for b in more_blocks:
            if _compact(_block_text(b)) != key:
                continue
            size = _block_font_size(b)
            if _off_axis(b, more_axis) or (size and float(size) >= 0.8 * top):
                return None
    # Cross-check against pypdf's own decoding of the page. If the page text
    # cannot be extracted at all, we cannot confirm the candidate: refuse.
    try:
        page_text = page.extract_text() or ""
    except Exception:
        return None
    if _compact(text) not in _compact(page_text):
        return None
    return text


# ---------------------------------------------------------------------------
# PDF metadata helpers
# ---------------------------------------------------------------------------


def _safe_text(value: Any) -> str:
    """A PDF text value as a stripped str, or "" when there is no text.

    pypdf represents ``/Title null`` as a ``NullObject`` — which is TRUTHY and
    whose ``str()`` is the literal ``"NullObject"``. That string became the
    document title (Info, XMP ``dc:title``, the viewer's title bar with
    DisplayDocTitle on) on every PyMuPDF-produced PDF, and it suppressed the
    missing-title finding. Only real strings count as text.
    """
    if value is None:
        return ""
    try:
        value = _resolve(value)
        if value is None or isinstance(value, NullObject):
            return ""
        if not isinstance(value, (str, bytes)):
            return ""
        if isinstance(value, bytes):
            value = value.decode("latin-1", "ignore")
        text = str(value).strip()
    except Exception:
        return ""
    if text in ("NullObject", "None", "null"):
        return ""
    return text


def _document_title(reader: PdfReader) -> str:
    metadata = getattr(reader, "metadata", None)
    if metadata is None:
        return ""
    for key in ("/Title", "title"):
        try:
            value = metadata.get(key)
        except Exception:
            value = None
        text = _safe_text(value)
        if text:
            return text
    return ""


def _document_language(reader: PdfReader) -> str:
    try:
        catalog = reader.trailer["/Root"]
    except Exception:
        return ""
    try:
        lang = catalog.get("/Lang")
    except Exception:
        lang = None
    return _safe_text(lang)


def _channels_to_hex(ch) -> str:
    r, g, b = (max(0, min(255, round(float(c) * 255))) for c in ch[:3])
    return f"{r:02X}{g:02X}{b:02X}"


def _cmyk_to_hex(ch) -> str:
    c, m, y, k = (float(x) for x in ch[:4])
    r = round(255 * (1 - c) * (1 - k))
    g = round(255 * (1 - m) * (1 - k))
    b = round(255 * (1 - y) * (1 - k))
    return f"{max(0, min(255, r)):02X}{max(0, min(255, g)):02X}{max(0, min(255, b)):02X}"


# Path-painting operators that FILL (used to spot coloured backgrounds).
_PAINT_FILL_OPS = {b"f", b"F", b"f*", b"b", b"b*", b"B", b"B*"}


def _is_whiteish_hex(hex6: str) -> bool:
    try:
        r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    except (TypeError, ValueError):
        return False
    return r >= 242 and g >= 242 and b >= 242


def _pdf_text_colors(reader: PdfReader) -> List[Dict[str, Any]]:
    """Distinct (colour, size) used by text, scanned from content-stream fill
    colour operators (rg / g / k). Default text colour is black.

    Backgrounds in document PDFs are overwhelmingly white, so the contrast
    analyzer compares these against white. A page that PAINTS a non-white
    fill (coloured rectangle, cell shading, gradient `sh`) breaks that
    assumption — light text on a dark band would be flagged as low-contrast
    even though it reads fine. Such pages are skipped entirely: no guess, no
    false positive (precision over recall).
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for page in reader.pages:
        try:
            ops = ContentStream(page.get_contents(), reader).operations
        except Exception:
            continue
        fill = "000000"
        size = None
        page_hits: List[Dict[str, Any]] = []
        colored_bg = False
        for operands, op in ops:
            try:
                if op == b"rg" and len(operands) >= 3:
                    fill = _channels_to_hex(operands)
                elif op == b"g" and len(operands) >= 1:
                    v = max(0, min(255, round(float(operands[0]) * 255)))
                    fill = f"{v:02X}{v:02X}{v:02X}"
                elif op == b"k" and len(operands) >= 4:
                    fill = _cmyk_to_hex(operands)
                elif op in _PAINT_FILL_OPS:
                    if not _is_whiteish_hex(fill):
                        colored_bg = True
                        break
                elif op == b"sh":  # shading/gradient paint — bg unknowable
                    colored_bg = True
                    break
                elif op == b"Tf" and len(operands) >= 2:
                    size = float(operands[1])
                elif op in (b"Tj", b"TJ", b"'", b'"'):
                    key = (fill, size)
                    if key not in seen:
                        seen.add(key)
                        page_hits.append({"c": fill, "sz": size, "b": False})
            except (TypeError, ValueError):
                continue
        if colored_bg:
            # Undo this page's contribution to the dedupe set so another
            # white-background page can still report the same colour.
            for hit in page_hits:
                seen.discard((hit["c"], hit["sz"]))
            continue
        out.extend(page_hits)
        if len(out) > 64:  # representative sample is plenty
            break
    return out


# Whole words (or, in the numbered check, word stems) that mark an
# auto-generated field name. Includes a few common non-English auto stems
# (champ/feld/campo …) — these only fire as a label-killer below, never accept.
_PDF_WIDGET_WORDS = {
    "text", "field", "fld", "form", "input", "box", "check", "checkbox",
    "cb", "radio", "button", "btn", "combo", "list", "listbox", "dropdown",
    "choice", "control", "element", "entry", "txt", "textbox", "textfield",
    "champ", "zone", "texte", "feld", "textfeld", "campo", "casilla",
}
# Suffix roots: a word that ENDS with one of these is a widget type, not a
# label ("DateField"→field, "fillText"→text, "TextBox"/"PO Box"→box). Field
# /T names are short label-like tokens, so substring/suffix matching here is
# safe (no real label is "Springfield"); we err toward leaving fields manual.
_PDF_WIDGET_SUFFIXES = (
    "textbox", "textfield", "checkbox", "combobox", "listbox", "dropdown",
    "text", "field", "box", "button", "radio", "combo", "menu",
)
# Bare auto-names with no number that still name nothing.
_PDF_JUNK_NAMES = {
    "untitled", "undefined", "field", "text", "form", "fld", "button",
    "btn", "checkbox", "check box", "radio", "radio button", "box", "control",
    "champ", "feld", "campo", "zone",
}
# Checkbox/radio export/state VALUES that are sometimes used as the /T — these
# are answers, not field labels, so they must never become an accessible name.
_PDF_VALUE_TOKENS = {"yes", "no", "on", "off", "true", "false", "none", "n/a", "na"}


def _pdf_word_kind(w: str) -> str:
    has_alpha = any(c.isalpha() for c in w)
    has_digit = any(c.isdigit() for c in w)
    if has_alpha and has_digit:
        return "mixed"
    if has_digit:
        return "number"
    if has_alpha:
        return "alpha"
    return "other"


def _clean_pdf_field_name(raw: object) -> Optional[str]:
    """A human-readable label from an AcroForm field's ``/T``, or None.

    The partial field name is frequently descriptive in real fillable PDFs
    ("First Name", "Date of Birth") and is exactly what an accessible name
    should say — but just as often it is an auto-generated placeholder
    ("Text1", "Text Field 2", "DateField"), an XFA path
    ("topmostSubform[0].Page1[0].f1_01[0]"), an opaque id ("a8f3c2d1"), or a
    code ("Q1a"). We accept only clean human labels; everything ambiguous stays
    manual, because a wrong accessible name is worse than none.
    """
    t = str(raw or "").strip()
    # Strip XFA index brackets ([0]) and #subform sigils before anything else.
    t = re.sub(r"\[\d+\]", "", t).replace("#", " ")
    if "." in t:  # fully-qualified name — the field's own terminal segment
        t = t.split(".")[-1].strip()
    t = re.sub(r"\s+", " ", t.replace("_", " ")).strip()
    if not t or len(t) > 60 or len(t) == 1:
        return None
    if not any(c.isalpha() for c in t):
        return None
    low = t.lower()
    if low in _PDF_JUNK_NAMES or low in _PDF_VALUE_TOKENS:
        return None
    words = low.split(" ")
    # Any token that mixes letters and digits is a code/id/auto-name, not a
    # label ("Text1", "Q1a", "Item3b", "f1", "a8f3c2d1", "TextField1").
    if any(_pdf_word_kind(w) == "mixed" for w in words):
        return None
    # Hex/GUID-shaped single token ("ffff" with a digit, "a8f3...") — belt and
    # braces; most are already caught as "mixed".
    if " " not in t and any(c.isdigit() for c in t) and re.fullmatch(r"[0-9a-fA-F]+", t):
        return None
    # A widget-type word ("DateField", "TextBox", "fillText", "Field", "Champ").
    for w in words:
        stem = w.rstrip("0123456789")
        if not stem:
            continue
        if stem in _PDF_WIDGET_WORDS or stem.endswith(_PDF_WIDGET_SUFFIXES):
            return None
    return t


def derive_pdf_field_label(fo: object, labeler: "Optional[FieldLabeler]" = None) -> Optional[str]:
    """Confident accessible label for one unlabeled AcroForm field, or None.

    Shared by the parser (to count how many are derivable) and the writer (to
    write exactly those), so the credited count always equals what is written.

    1. ``/Tx`` (text) and ``/Ch`` (choice) fields are labeled from a clean,
       human ``/T``. ``/Btn`` ``/T`` is frequently the export VALUE ("Yes",
       "Male") rather than a label, so it is never used.
    2. Otherwise, when a :class:`FieldLabeler` is supplied, the words PRINTED
       next to the field on the page ("Date of birth:" to its left, "I certify
       ..." to a checkbox's right). The labeler refuses anything ambiguous.
    """
    try:
        tu = fo.get("/TU")
        if tu and str(tu).strip():
            return None  # already has an accessible name
        if _field_type(fo) != "/Btn":
            name = _clean_pdf_field_name(fo.get("/T"))
            if name:
                return name
        if labeler is not None:
            return labeler.label_for(fo)
        return None
    except Exception:
        return None


def _field_type(fo: Any) -> str:
    """``/FT`` of a field, inherited from its parents when absent."""
    node = fo
    for _ in range(16):
        if node is None:
            return ""
        try:
            ft = node.get("/FT")
        except Exception:
            return ""
        if ft is not None:
            return str(ft)
        node = _resolve(node.get("/Parent")) if "/Parent" in node else None
    return ""


def _field_flags(fo: Any) -> int:
    node = fo
    for _ in range(16):
        if node is None:
            return 0
        try:
            ff = node.get("/Ff")
            if ff is not None:
                return int(ff)
        except Exception:
            return 0
        node = _resolve(node.get("/Parent")) if "/Parent" in node else None
    return 0


def _field_widgets(fo: Any) -> List[Any]:
    """The widget annotation dicts of one field (itself, or its /Kids)."""
    try:
        if str(fo.get("/Subtype") or "") == "/Widget" or "/Rect" in fo:
            return [fo]
        kids = _resolve(fo.get("/Kids")) if "/Kids" in fo else None
        out = []
        for k in kids or []:
            kr = _resolve(k)
            if isinstance(kr, DictionaryObject) and (
                str(kr.get("/Subtype") or "") == "/Widget" or "/Rect" in kr
            ):
                out.append(kr)
        return out
    except Exception:
        return []


def _rect_key(rect: Any) -> Optional[Tuple[float, ...]]:
    try:
        vals = [float(v) for v in list(rect)[:4]]
        x0, y0, x1, y1 = vals
        return (round(min(x0, x1), 1), round(min(y0, y1), 1), round(max(x0, x1), 1), round(max(y0, y1), 1))
    except Exception:
        return None


class FieldLabeler:
    """Labels printed next to AcroForm widgets, derived from page geometry.

    Built once per document from the SAME content the writer later edits, so
    the parser's derivable count and the writer's /TU writes come from one
    computation. Keys are ``(page_index, rect, /T)`` — stable across pypdf's
    reader and the writer's clone, whose object numbers differ.

    Refusals (the field stays manual): radio buttons and push buttons, fields
    with several widgets, no label found, a label claimed by two widgets,
    anything unreadable.
    """

    def __init__(self, pdf: Any, max_pages: Optional[int] = None) -> None:
        self.labels: Dict[tuple, str] = {}
        self.page_of_widget: Dict[int, int] = {}
        self.boxes: Dict[tuple, Tuple[int, Tuple[float, ...]]] = {}
        try:
            self._build(pdf, max_pages)
        except Exception:
            logger.debug("FieldLabeler failed", exc_info=True)
            self.labels = {}

    @staticmethod
    def _key(page_index: int, rect: Tuple[float, ...], fo: Any) -> tuple:
        try:
            t = str(fo.get("/T") or "")
        except Exception:
            t = ""
        return (page_index, rect, t)

    def _build(self, pdf: Any, max_pages: Optional[int]) -> None:
        from app.pdf.text_geometry import all_words, label_for_widget, page_spans

        pages = list(pdf.pages)
        if max_pages is not None:
            pages = pages[:max_pages]
        for pi, page in enumerate(pages):
            annots = _resolve(page.get("/Annots")) if "/Annots" in page else None
            if not isinstance(annots, (list, ArrayObject)):
                continue
            widgets = []
            for ref in annots:
                a = _resolve(ref)
                if not isinstance(a, DictionaryObject) or str(a.get("/Subtype") or "") != "/Widget":
                    continue
                if isinstance(ref, IndirectObject):
                    self.page_of_widget[ref.idnum] = pi
                rk = _rect_key(a.get("/Rect"))
                if rk is None:
                    continue
                widgets.append((a, rk))
            if not widgets:
                continue
            spans = page_spans(page, pdf)
            if spans is None:
                continue
            words = all_words(spans)
            rects = [rk for _a, rk in widgets]
            claims: Dict[tuple, List[tuple]] = {}
            found: Dict[tuple, str] = {}
            for a, rk in widgets:
                field = a if "/T" in a else _resolve(a.get("/Parent"))
                if not isinstance(field, DictionaryObject):
                    continue
                if len(_field_widgets(field)) != 1:
                    continue  # radio groups / mirrored widgets: ambiguous
                ft = _field_type(field)
                ff = _field_flags(field)
                if ft in ("/Tx", "/Ch"):
                    kind = "text"
                elif ft == "/Btn" and not (ff & (1 << 15)) and not (ff & (1 << 16)):
                    kind = "check"
                else:
                    continue
                key = self._key(pi, rk, field)
                self.boxes[key] = (pi, rk)
                got = label_for_widget(rk, words, rects, kind=kind)
                if not got:
                    continue
                label, word_keys = got
                found[key] = label
                for wk in word_keys:
                    claims.setdefault(wk, []).append(key)
            # A printed word may label ONE field. Shared -> both stay manual.
            contested = {k for keys in claims.values() if len(keys) > 1 for k in keys}
            for key, label in found.items():
                if key not in contested:
                    self.labels[key] = label

    def _field_key(self, fo: Any) -> Optional[tuple]:
        ws = _field_widgets(fo)
        if len(ws) != 1:
            return None
        w = ws[0]
        rk = _rect_key(w.get("/Rect"))
        if rk is None:
            return None
        pi = None
        ref = getattr(w, "indirect_reference", None)
        if ref is not None:
            pi = self.page_of_widget.get(ref.idnum)
        if pi is None:
            return None
        return self._key(pi, rk, fo)

    def label_for(self, fo: Any) -> Optional[str]:
        key = self._field_key(fo)
        return self.labels.get(key) if key is not None else None

    def box_for(self, fo: Any) -> Optional[Tuple[int, Tuple[float, ...]]]:
        key = self._field_key(fo)
        if key is None:
            return None
        return self.boxes.get(key) or (key[0], key[1])


def iter_acroform_fields(acro: object):
    """Yield the resolved top-level AcroForm field dicts — the exact set the
    label count and the writer both operate on, so they stay in lockstep."""
    fields = acro.get("/Fields") or []
    fields = fields.get_object() if hasattr(fields, "get_object") else fields
    for field in fields:
        try:
            yield field.get_object() if hasattr(field, "get_object") else field
        except Exception:
            continue


def _form_field_label_counts(
    reader: PdfReader, locations: Optional[List[Dict[str, Any]]] = None
) -> "tuple[int, int, int]":
    """Return ``(total, unlabeled, derivable)`` AcroForm fields.

    "Unlabeled" means no ``/TU`` (the field's accessible label/tooltip — what AT
    announces). ``derivable`` is how many unlabeled fields have a confident
    label we can auto-write — from a clean ``/T`` or from the words printed
    next to the field (the rest stay manual). When ``locations`` is given, one
    ``{page, bbox, name, derivable}`` entry per unlabeled field is appended so
    a finding can show WHERE the field is.
    """
    total = 0
    unlabeled = 0
    derivable = 0
    try:
        root = reader.trailer.get("/Root", {})
        root = root.get_object() if hasattr(root, "get_object") else root
        acro = root.get("/AcroForm") if root else None
        acro = acro.get_object() if hasattr(acro, "get_object") else acro
        if not acro:
            return (0, 0, 0)
        labeler = FieldLabeler(reader, max_pages=_MAX_PDF_PAGES)
        for fo in iter_acroform_fields(acro):
            try:
                # Pushbuttons are counted (conservative) but never auto-labeled.
                total += 1
                tu = fo.get("/TU")
                if not tu or not str(tu).strip():
                    unlabeled += 1
                    ok = bool(derive_pdf_field_label(fo, labeler))
                    if ok:
                        derivable += 1
                    if locations is not None and len(locations) < 200:
                        box = labeler.box_for(fo)
                        if box is not None:
                            locations.append({
                                "page": box[0] + 1,
                                "bbox": list(box[1]),
                                "name": _safe_text(fo.get("/T"))[:80],
                                "derivable": ok,
                            })
            except Exception:
                continue
    except Exception:
        return (total, unlabeled, derivable)
    return (total, unlabeled, derivable)


# ---------------------------------------------------------------------------
# Image / annotation extraction
# ---------------------------------------------------------------------------


def _resolve(obj: Any) -> Any:
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


def _names_drawn_on_page(page: Any, reader: Any) -> Optional[set]:
    """XObject names actually painted by the page's content stream (`Do` ops).

    Returns ``None`` when the content stream can't be parsed — callers should
    then fall back to resource listing. Restricting images to names that are
    really DRAWN avoids a classic false-positive multiplier: merged/optimized
    PDFs often share one resource dictionary across every page, which would
    otherwise yield one MISSING_ALT_TEXT per page per listed-but-unused image.
    """
    try:
        contents = page.get_contents()
        if contents is None:
            return set()
        ops = ContentStream(contents, reader).operations
    except Exception:
        return None
    drawn: set = set()
    for operands, op in ops:
        if op == b"Do" and operands:
            drawn.add(str(operands[0]).lstrip("/"))
    return drawn


def _iter_image_xobjects(page: Any, reader: Any = None) -> List[Tuple[str, Dict[str, Any]]]:
    """Return ``(name, xobject_dict)`` pairs for image XObjects DRAWN on ``page``."""

    results: List[Tuple[str, Dict[str, Any]]] = []
    try:
        resources = _resolve(page.get("/Resources"))
    except Exception:
        resources = None
    if not isinstance(resources, DictionaryObject):
        return results
    xobjects = _resolve(resources.get("/XObject")) if "/XObject" in resources else None
    if not isinstance(xobjects, DictionaryObject):
        return results
    drawn = _names_drawn_on_page(page, reader) if reader is not None else None
    for name, ref in xobjects.items():
        obj = _resolve(ref)
        if not isinstance(obj, DictionaryObject):
            continue
        if obj.get("/Subtype") != "/Image":
            continue
        if drawn is not None and str(name).lstrip("/") not in drawn:
            continue  # listed in resources but never painted on this page
        results.append((str(name), obj))
    return results


def _alt_for_xobject(xobject: Dict[str, Any]) -> Tuple[Optional[str], bool]:
    """Return ``(alt_text, decorative)``.

    A PDF marks decorative images with ``/Artifact`` or an empty ``/Alt``.
    """

    alt_obj = xobject.get("/Alt") if "/Alt" in xobject else None
    if isinstance(_resolve(alt_obj), NullObject):
        # PDF 32000 7.3.9: a key whose value is null is the same as an absent
        # key. "/Alt null" is NO alt — not the empty /Alt that declares an
        # image decorative (which would hide it from assistive technology).
        alt_obj = None
    raw_alt = _safe_text(alt_obj)
    decorative = False
    structure = xobject.get("/StructParent") or xobject.get("/StructParents")
    artifact = False
    actual = xobject.get("/ActualText")
    actual_text = _safe_text(actual)
    if actual_text and not raw_alt:
        raw_alt = actual_text
    if "/Artifact" in xobject:
        artifact = True
    if artifact:
        decorative = True
        return None, True
    if alt_obj is not None and not raw_alt:
        # An explicit empty /Alt declares the image decorative.
        return None, True
    if structure is None and not raw_alt:
        # No structural parent and no alt — treat as decorative to avoid
        # generating false-positive flags for tagged decorative images.
        decorative = False
    return (raw_alt or None), decorative


_FILTER_TO_MIME = {
    "/DCTDecode": "image/jpeg",
    "/JPXDecode": "image/jp2",
    "/CCITTFaxDecode": "image/tiff",
    "/FlateDecode": "image/png",
}


def _extract_image_bytes(xobject: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort raw image extraction.

    Returns ``(base64-payload, mime-type)`` or ``(None, None)`` if extraction
    is unsupported or the image is too large to inline (>2MB raw).
    """

    try:
        raw = xobject.get_data()
    except Exception:
        return None, None
    if not raw:
        return None, None
    if len(raw) > 2_000_000:
        return None, None
    filt = xobject.get("/Filter")
    mime = "image/png"
    if isinstance(filt, list) or isinstance(filt, ArrayObject):
        for f in filt:
            mime = _FILTER_TO_MIME.get(str(f), mime)
    elif filt is not None:
        mime = _FILTER_TO_MIME.get(str(filt), mime)
    try:
        encoded = base64.b64encode(raw).decode("ascii")
    except Exception:
        return None, None
    return encoded, mime


class _DestResolver:
    """Resolve internal link destinations to ``#page-N`` targets.

    A PDF link is internal when it carries ``/Dest`` or a ``/GoTo`` action —
    the table of contents of nearly every long report. The parser used to read
    ``/A /URI`` only, so every TOC entry became a link with NO target (flagged
    LINK_TARGET_BROKEN) and the text "(link)" (flagged non-descriptive): ten
    manual-review items for a five-line contents page that works perfectly.
    A destination that genuinely does not resolve still reads as broken.
    """

    def __init__(self, reader: Any) -> None:
        self._reader = reader
        self._page_ids: Optional[Dict[int, int]] = None
        self._named: Optional[Dict[str, Any]] = None

    def _pages(self) -> Dict[int, int]:
        if self._page_ids is None:
            self._page_ids = {}
            try:
                for i, p in enumerate(self._reader.pages):
                    ref = getattr(p, "indirect_reference", None)
                    if ref is not None:
                        self._page_ids[ref.idnum] = i
            except Exception:
                pass
        return self._page_ids

    def _names(self) -> Dict[str, Any]:
        if self._named is None:
            try:
                self._named = dict(self._reader.named_destinations or {})
            except Exception:
                self._named = {}
        return self._named

    def resolve(self, dest: Any) -> str:
        dest = _resolve(dest)
        if isinstance(dest, DictionaryObject) and "/D" in dest:
            dest = _resolve(dest.get("/D"))
        if isinstance(dest, (list, ArrayObject)) and dest:
            first = dest[0]
            if isinstance(first, IndirectObject):
                idx = self._pages().get(first.idnum)
                if idx is not None:
                    return f"#page-{idx + 1}"
                return ""
            try:  # remote-style integer page index
                return f"#page-{int(first) + 1}"
            except Exception:
                return ""
        name = _safe_text(dest) if isinstance(dest, (str, bytes)) else ""
        if name:
            named = self._names().get(name) or self._names().get("/" + name.lstrip("/"))
            if named is not None:
                try:
                    pg = getattr(named, "page", None)
                    if isinstance(pg, IndirectObject):
                        idx = self._pages().get(pg.idnum)
                        if idx is not None:
                            return f"#page-{idx + 1}"
                    elif isinstance(pg, int) and not isinstance(pg, bool):
                        return f"#page-{int(pg) + 1}"
                except Exception:
                    pass
                return "#" + name.lstrip("/")
        return ""


def _link_target(annot: DictionaryObject, dests: Optional[_DestResolver]) -> str:
    action = _resolve(annot.get("/A")) if "/A" in annot else None
    if isinstance(action, DictionaryObject):
        kind = str(action.get("/S") or "")
        if kind == "/URI" or action.get("/URI") is not None:
            return _safe_text(action.get("/URI"))
        if kind == "/GoTo":
            return dests.resolve(action.get("/D")) if dests else ""
        if kind in ("/GoToR", "/Launch", "/GoToE"):
            f = _resolve(action.get("/F"))
            if isinstance(f, DictionaryObject):
                f = f.get("/UF") or f.get("/F")
            fname = _safe_text(f)
            return fname
        if kind == "/Named":
            n = _safe_text(action.get("/N")).lstrip("/")
            return f"#{n}" if n else ""
        if kind == "/JavaScript":
            return "javascript:"
        return ""
    if "/Dest" in annot:
        return dests.resolve(annot.get("/Dest")) if dests else ""
    return ""


def _link_annotations(page: Any, dests: Optional[_DestResolver] = None) -> List[Dict[str, Any]]:
    annots_obj = _resolve(page.get("/Annots")) if "/Annots" in page else None
    if not isinstance(annots_obj, (list, ArrayObject)):
        return []
    links: List[Dict[str, Any]] = []
    for ref in annots_obj:
        annot = _resolve(ref)
        if not isinstance(annot, DictionaryObject):
            continue
        if annot.get("/Subtype") != "/Link":
            continue
        try:
            target = _link_target(annot, dests)
        except Exception:
            target = ""
        contents = _safe_text(annot.get("/Contents"))
        links.append({"target": target, "contents": contents, "rect": _rect_key(annot.get("/Rect"))})
    return links


def _image_placements(ops) -> Dict[str, Tuple[float, float, float, float]]:
    """``{xobject_name: bbox}`` — where each image is FIRST painted, from the
    CTM in force at its ``Do`` (images map the unit square through the CTM)."""
    from app.pdf.ua_tagger import _IDENTITY_CTM, _ctm_concat, _unit_square_bbox

    out: Dict[str, Tuple[float, float, float, float]] = {}
    ctm = _IDENTITY_CTM
    stack: List[tuple] = []
    in_text = False
    for operands, op in ops:
        if op == b"BT":
            in_text = True
        elif op == b"ET":
            in_text = False
        elif in_text:
            continue
        elif op == b"q":
            stack.append(ctm)
        elif op == b"Q":
            if stack:
                ctm = stack.pop()
        elif op == b"cm" and len(operands) >= 6:
            try:
                ctm = _ctm_concat(tuple(float(o) for o in operands[:6]), ctm)
            except Exception:
                pass
        elif op == b"Do" and operands:
            name = str(operands[0]).lstrip("/")
            if name not in out:
                bb = _unit_square_bbox(ctm)
                if bb is not None:
                    out[name] = bb
    return out


# A figure caption is authored text that NAMES the figure it sits under.
# "Table N" is deliberately absent: that captions a table, not an image.
_FIGURE_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.?|chart|graph|photo(?:graph)?|image|map|diagram|exhibit|illustration|plate|infographic)"
    r"\s*(?:\d+(?:[.\-]\d+)*[a-z]?|[ivxlc]{1,6})\s*[.:)\-–—]?\s+\S",
    re.IGNORECASE,
)


def _text_segments(words) -> List[List[Any]]:
    """Words grouped into line SEGMENTS in reading order: one baseline, split
    wherever a horizontal gap is wider than ~1.5 em. Two captions set side by
    side under two charts share a baseline but are two segments."""
    from app.pdf.text_geometry import _reading_sorted

    lines: List[List[Any]] = []
    for w in _reading_sorted(words):
        if lines and abs(lines[-1][0].y - w.y) <= max(2.0, 0.3 * w.size):
            lines[-1].append(w)
        else:
            lines.append([w])
    segs: List[List[Any]] = []
    for line in lines:
        cur = [line[0]]
        for w in line[1:]:
            if w.x0 - cur[-1].x1 > max(12.0, 1.5 * max(w.size, cur[-1].size)):
                segs.append(cur)
                cur = [w]
            else:
                cur.append(w)
        segs.append(cur)
    return segs


def _caption_for_image(bbox, words, other_boxes=()) -> Optional[str]:
    """The "Figure N. ..." caption printed directly under (or over) an image.

    Evidence only: a line segment that STARTS with a figure label, whose
    baseline is within ~2.5 line-heights of the image's bottom (or 2 above
    its top), and which overlaps the image horizontally. Wrapped
    continuation lines (same left edge, same size, one line-pitch down) are
    taken, up to three lines. Refused, so nothing is returned:

    * a caption printed directly UNDER another image (``other_boxes``) is
      that image's caption, never this one's "caption above" — stacked
      figures used to hand figure 1's caption to figure 2;
    * two candidates equally close and equally overlapping (whose is it?).

    Side-by-side captions on one baseline are separate segments, so each
    chart gets its own. Anything else — body text, a heading, a caption two
    paragraphs away — is not a caption.
    """
    if not bbox or not words:
        return None
    x0, y0, x1, y1 = bbox
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    segs = _text_segments(words)

    def span(seg) -> Tuple[float, float]:
        return min(w.x0 for w in seg), max(w.x1 for w in seg)

    def overlap(seg, bx0: float, bx1: float) -> float:
        sx0, sx1 = span(seg)
        return max(0.0, min(sx1, bx1) - max(sx0, bx0))

    def below_of(seg, box) -> bool:
        bx0, by0, bx1, _by1 = box
        size = max(w.size for w in seg)
        y = seg[0].y
        return overlap(seg, bx0, bx1) > 1 and y < by0 and (by0 - y) <= 2.5 * size + 2

    others = [tuple(b) for b in other_boxes if b and tuple(b) != tuple(bbox)]
    cands: List[Tuple[float, float, str]] = []
    for i, seg in enumerate(segs):
        text = " ".join(w.text for w in seg).strip()
        ov = overlap(seg, x0, x1)
        if not _FIGURE_CAPTION_RE.match(text) or ov <= 1:
            continue
        size = max(w.size for w in seg)
        y = seg[0].y
        below = below_of(seg, bbox)
        above = y > y1 and (y - y1) <= 2.0 * size + 2
        if not (below or above):
            continue
        if above and any(below_of(seg, ob) for ob in others):
            continue  # it sits under another image: that image's caption
        parts = [text]
        sx0 = span(seg)[0]
        prev = seg
        for nxt in segs[i + 1 : i + 6]:
            if len(parts) >= 3:
                break
            if nxt[0].y >= prev[0].y - 0.5:
                continue  # another segment on the same line
            if abs(span(nxt)[0] - sx0) > 6 or abs(max(w.size for w in nxt) - size) > 0.5:
                break
            if not (0 < prev[0].y - nxt[0].y <= 1.6 * size):
                break
            nt = " ".join(w.text for w in nxt).strip()
            if _FIGURE_CAPTION_RE.match(nt):
                break
            parts.append(nt)
            prev = nxt
        cand = re.sub(r"\s+", " ", " ".join(parts)).strip()
        dist = (y0 - y) if below else (y - y1)
        cands.append((dist, ov / max(1.0, span(seg)[1] - span(seg)[0]), cand))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], -c[1]))
    best = cands[0]
    if len(cands) > 1:
        nxt = cands[1]
        if abs(nxt[0] - best[0]) <= 2.0 and abs(nxt[1] - best[1]) < 0.1 and nxt[2] != best[2]:
            return None  # two equally good captions: whose is it?
    from app.pdf.text_decode import is_readable_text

    cap = best[2]
    if len(cap) > 400 or not is_readable_text(cap):
        return None
    return cap


def _paragraph_boxes(
    paragraphs: List[str], words, max_skip: Optional[int] = 400
) -> List[Optional[Tuple[float, float, float, float]]]:
    """Locate each extracted paragraph on the page (union of its word boxes).

    ``extract_text`` and the positioned words come from the same content
    stream in the same order, so a whitespace-insensitive sequential match
    finds each paragraph; one that cannot be matched gets None (no location
    rather than a wrong one).
    """
    from app.pdf.text_geometry import union_bbox, word_bbox

    out: List[Optional[Tuple[float, float, float, float]]] = []
    if not words:
        return [None] * len(paragraphs)
    stream = []
    owner: List[int] = []
    for wi, w in enumerate(words):
        c = _compact(w.text)
        stream.append(c)
        owner.extend([wi] * len(c))
    flat = "".join(stream)
    cursor = 0
    for para in paragraphs:
        needle = _compact(para)
        if not needle:
            out.append(None)
            continue
        at = flat.find(needle, cursor)
        if at < 0 or (max_skip is not None and at - cursor > max_skip):
            out.append(None)
            continue
        idxs = sorted(set(owner[at : at + len(needle)]))
        out.append(union_bbox(word_bbox(words[i]) for i in idxs))
        cursor = at + len(needle)
    return out


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------


# A tagged table's / list's text must be at least this long (whitespace
# removed) before a single contiguous match of it on the page is trusted as
# the element's location. Two short cells ("A", "B") could match anywhere.
_MIN_LOCATE_CHARS = 12


def _struct_text_box(text: Optional[str], words_fn) -> Optional[Tuple[float, float, float, float]]:
    """Where a tagged container (table / list) sits, from its own text.

    ``text`` is the element's text in content-stream order (tag_reader). It
    is located as ONE contiguous, whitespace-insensitive run in the page's
    positioned words — specific enough that a match IS the element. Short
    text, unreadable text or no match: None (no location, never a guess).
    """
    if not text or len(_compact(text)) < _MIN_LOCATE_CHARS:
        return None
    try:
        words = words_fn()
    except Exception:
        return None
    if not words:
        return None
    return _paragraph_boxes([text], words, max_skip=None)[0]


def _node_metadata(page_index: Optional[int] = None, **properties: Any) -> NodeMetadata:
    return NodeMetadata(
        page=page_index,
        source_format="pdf",
        properties={k: v for k, v in properties.items() if v is not None},
    )


def _split_paragraphs(text: str) -> List[str]:
    if not text:
        return []
    chunks = re.split(r"\n\s*\n+", text)
    out: List[str] = []
    for chunk in chunks:
        cleaned = re.sub(r"[ \t]+", " ", chunk).strip()
        if cleaned:
            out.append(cleaned)
    return out


class PDFParser:
    """Pragmatic v1 PDF parser → :class:`ParserResult`."""

    def parse(self, file_path: str) -> ParserResult:
        path = Path(file_path)
        document_id = derive_document_id(path)
        reader = PdfReader(str(path))

        # Encryption. pypdf silently opens an owner-password-only PDF (empty
        # user password); a PDF that needs a password to OPEN cannot be read at
        # all, and used to surface as "invalid, uncorrupted PDF" — say what it
        # is instead.
        encrypted = False
        try:
            encrypted = bool(reader.is_encrypted)
        except Exception:
            encrypted = False
        if encrypted:
            try:
                from pypdf import PasswordType

                opened = reader.decrypt("")
            except Exception:
                opened = None
            if not opened or opened == PasswordType.NOT_DECRYPTED:
                raise PdfPasswordProtectedError("pdf requires a user password")

        title = _document_title(reader)
        language = _document_language(reader)

        properties: Dict[str, Any] = {}
        if title:
            properties["title"] = title
        if encrypted:
            properties["pdf_encrypted"] = True
        field_locations: List[Dict[str, Any]] = []
        ff_total, ff_unlabeled, ff_derivable = _form_field_label_counts(reader, field_locations)
        if ff_total:
            properties["form_fields_total"] = ff_total
            properties["form_fields_unlabeled"] = ff_unlabeled
            # Unlabeled fields with a confident label (a clean /T, or the words
            # printed next to the field) → auto-write /TU. The writer
            # re-derives with the SAME helpers so credit == what's written.
            properties["form_fields_derivable"] = ff_derivable
            if field_locations:
                # Where each unlabeled field is (page, PDF user-space bbox,
                # origin bottom-left) — the form finding is document-level,
                # so its locations ride on the root.
                properties["form_fields_unlabeled_locations"] = field_locations
        # A title CANDIDATE for SetDocumentTitleExecutor: the largest-font
        # text block on page 1, when it is short and clearly larger than the
        # page's body size. The heading classifier here is text-shape only
        # (numbering / ALL CAPS / trailing colon), so an 18pt "Annual Report
        # 2025" produced no HeadingNode and the executor fell through to
        # "Untitled Document" — a placeholder our own analyzer flags —
        # wrote it as the /Title, and credited it.
        try:
            cand = _title_candidate_from_page(reader)
            if cand:
                properties["title_candidate"] = cand
        except Exception:
            pass
        # Text colours from the content stream → contrast analysis (vs white).
        text_colors = _pdf_text_colors(reader)
        if text_colors:
            properties["explicit_text_colors"] = text_colors
            properties["bg_color"] = "FFFFFF"

        root = DocumentNode(
            id="doc-1",
            content=NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(
                language=language or None,
                source_format="pdf",
                properties=properties,
            ),
            children=[],
            accessibility_flags=[],
        )

        page_count = len(reader.pages)
        next_id = _IdCounter()

        # Tagged-PDF structure info: headings + tables read from the EXISTING
        # tree (RoleMap-resolved), and Figure /Alt mapped back to the drawn
        # XObject — fixing the false positive where a properly tagged figure
        # (alt on the StructElem, the standards-correct place) was flagged as
        # missing alt because only the XObject dictionary was consulted.
        struct_info = None
        try:
            from app.pdf.tag_reader import read_struct_info

            struct_info = read_struct_info(reader)
        except Exception:
            struct_info = None
        # Disclose a structure tree we could not fully read. Downstream, the
        # UntaggedPdfAnalyzer must NOT flag this document as untagged (it is
        # tagged; we just could not read all of it), and the report should
        # say the tags were only partly consulted.
        # (root.metadata.properties, not the local dict — NodeMetadata copied
        # the dict when the root was built above; see the pages_truncated fix.)
        if struct_info and (struct_info.get("struct_tree_unreadable") or struct_info.get("struct_tree_truncated")):
            root.metadata.properties["struct_tree_partial"] = True
            if struct_info.get("struct_tree_unreadable"):
                root.metadata.properties["struct_tree_unreadable"] = True
        tree_alt_by_xobject = (struct_info or {}).get("figure_alt_by_xobject", {})
        struct_headings = (struct_info or {}).get("headings", [])
        struct_tables = (struct_info or {}).get("tables", [])
        struct_lists = (struct_info or {}).get("lists", [])
        # When the tree declares headings, the tags are authoritative — the
        # text-shape heuristic stays for untagged documents only.
        use_tag_headings = bool(struct_headings)

        # Aggregate signals for the "scanned PDF / image-only" detector.
        total_text_chars = 0
        image_only_pages = 0  # pages with image XObject(s) and <50 chars of text

        # Page cap: real-world government PDFs can be hundreds of pages, and
        # every page is tokenized multiple times (text colours, struct tree,
        # text extract, then the writer's tagger re-tokenizes). Without a
        # ceiling one huge upload ties up a worker for minutes and can blow
        # the request timeout. Analyze the first N pages and disclose the
        # truncation honestly rather than hanging.
        pages_to_process = min(page_count, _MAX_PDF_PAGES)
        if page_count > _MAX_PDF_PAGES:
            # Write to root.metadata.properties, NOT the local `properties`
            # dict: NodeMetadata is a pydantic model and COPIES the dict at
            # construction (line ~590), so the two diverged the moment the
            # root was built. Writing to the local here recorded the
            # truncation into a dict nothing ever read again — the disclosure
            # was dead on arrival for every over-cap document.
            root.metadata.properties["pages_truncated"] = True
            root.metadata.properties["pages_processed"] = pages_to_process

        dests = _DestResolver(reader)
        font_cache: Dict[Any, Any] = {}
        # Small previews of undescribed images so a finding can SHOW which
        # picture it means. Capped per document: a scan-heavy PDF must not
        # decode hundreds of page images just to draw thumbnails.
        _thumbnail_budget = {"left": _MAX_THUMBNAILS}
        extraction_failed_pages: List[int] = []
        undecodable_pages: List[int] = []

        for page_index in range(pages_to_process):
            page = reader.pages[page_index]
            page_label = f"page-{page_index + 1}"
            # Page geometry for finding locations: bboxes are PDF user-space
            # points relative to the visible page box's bottom-left corner.
            geo = _page_geometry(page)
            section = SectionNode(
                id=next_id(f"{page_label}-section"),
                content=NodeContent(kind=ContentKind.TEXT, text=f"Page {page_index + 1}"),
                metadata=_node_metadata(page_index=page_index + 1, page_size=geo.get("page_size"), page_rotate=geo.get("rotate")),
                children=[],
                accessibility_flags=[],
            )
            _spans_cache: Dict[str, Any] = {}

            def _words():
                if "w" not in _spans_cache:
                    from app.pdf.text_geometry import all_words, page_spans

                    spans = page_spans(page, reader)
                    _spans_cache["w"] = all_words(spans) if spans is not None else None
                return _spans_cache["w"]

            # --- Text → headings + paragraphs -------------------------------
            extract_failed = False
            try:
                raw_text = page.extract_text() or ""
            except Exception:
                # A decode failure is NOT "no text". pypdf 4.2 raises on some
                # producers' ToUnicode CMaps (MuPDF's 5-hex-digit bfrange
                # destinations); treating that as an empty page hid
                # PDF_UNTAGGED and reported a broken document as nearly clean.
                raw_text = ""
                extract_failed = True
            page_text_chars = len((raw_text or "").strip())
            if extract_failed or _page_uses_composite_font(page):
                op_chars, undecodable_share = _text_op_census(reader, page, font_cache)
            else:
                op_chars, undecodable_share = 0, 0.0
            if extract_failed and op_chars > 0:
                extraction_failed_pages.append(page_index + 1)
                page_text_chars = op_chars
            elif undecodable_share >= 0.5 and op_chars > 0:
                # Composite fonts without a ToUnicode map: extract_text
                # "succeeds" into glyph-id nonsense. Count the text (it exists
                # and needs structure) but do not analyse the nonsense.
                undecodable_pages.append(page_index + 1)
                raw_text = ""
                page_text_chars = max(page_text_chars, op_chars)
            total_text_chars += page_text_chars
            paragraphs = _split_paragraphs(raw_text)
            words = _words() if paragraphs else None
            boxes = _paragraph_boxes(paragraphs, words) if words else [None] * len(paragraphs)
            for para_index, paragraph in enumerate(paragraphs, start=1):
                heading_level = None if use_tag_headings else _classify_paragraph(paragraph)
                bbox = _rel_box(boxes[para_index - 1], geo)
                if heading_level is not None:
                    section.children.append(
                        HeadingNode(
                            id=next_id(f"{page_label}-h{para_index}"),
                            level=heading_level,
                            content=NodeContent(kind=ContentKind.TEXT, text=paragraph),
                            metadata=_node_metadata(
                                page_index=page_index + 1, bbox=bbox, page_size=geo.get("page_size"), page_rotate=geo.get("rotate")
                            ),
                            children=[],
                            accessibility_flags=[],
                        )
                    )
                else:
                    section.children.append(
                        ParagraphNode(
                            id=next_id(f"{page_label}-p{para_index}"),
                            content=NodeContent(kind=ContentKind.TEXT, text=paragraph),
                            metadata=_node_metadata(
                                page_index=page_index + 1, bbox=bbox, page_size=geo.get("page_size"), page_rotate=geo.get("rotate")
                            ),
                            children=[],
                            accessibility_flags=[],
                        )
                    )

            # --- Headings from the EXISTING structure tree (tagged PDFs) ----
            if use_tag_headings:
                for h_idx, h in enumerate(
                    (h for h in struct_headings if h.get("page") == page_index), start=1
                ):
                    htext = h.get("text") or ""
                    hbox = None
                    if htext.strip():
                        ws = _words()
                        if ws:
                            hbox = _rel_box(_paragraph_boxes([htext], ws, max_skip=None)[0], geo)
                    section.children.append(
                        HeadingNode(
                            id=next_id(f"{page_label}-th{h_idx}"),
                            level=max(1, min(6, int(h.get("level") or 1))),
                            content=NodeContent(kind=ContentKind.TEXT, text=htext),
                            metadata=_node_metadata(
                                page_index=page_index + 1, from_tags=True, bbox=hbox,
                                page_size=geo.get("page_size"), page_rotate=geo.get("rotate"),
                            ),
                            children=[],
                            accessibility_flags=[],
                        )
                    )

            # --- Tables from the EXISTING structure tree (tagged PDFs) ------
            for t_idx, t in enumerate(
                (t for t in struct_tables if (t.get("page") or 0) == page_index), start=1
            ):
                rows: List[TableRowNode] = []
                for cell_tags in t.get("rows", []):
                    cells = [
                        TableCellNode(
                            id=next_id(f"{page_label}-tcell"),
                            cell_type=TableCellType.HEADER if s == "TH" else TableCellType.DATA,
                            header_scope=TableHeaderScope.COLUMN if s == "TH" else TableHeaderScope.NONE,
                            content=NodeContent(kind=ContentKind.TEXT, text=" "),
                            metadata=_node_metadata(page_index=page_index + 1),
                            children=[],
                            accessibility_flags=[],
                        )
                        for s in cell_tags
                    ]
                    rows.append(
                        TableRowNode(
                            id=next_id(f"{page_label}-trow"),
                            content=NodeContent(kind=ContentKind.NONE),
                            metadata=_node_metadata(page_index=page_index + 1),
                            children=cells,
                            accessibility_flags=[],
                        )
                    )
                if rows:
                    section.children.append(
                        TableNode(
                            id=next_id(f"{page_label}-ttable"),
                            content=NodeContent(kind=ContentKind.NONE),
                            metadata=_node_metadata(
                                page_index=page_index + 1, from_tags=True, page_size=geo.get("page_size"), page_rotate=geo.get("rotate"),
                                bbox=_rel_box(_struct_text_box(t.get("text"), _words), geo),
                            ),
                            children=rows,
                            accessibility_flags=[],
                        )
                    )

            # --- Lists from the EXISTING structure tree (tagged PDFs) -------
            # A tagged /L whose kids aren't /LI is a classic bad-remediation
            # artifact; ListStructureAnalyzer flags exactly that shape.
            for l in (l for l in struct_lists if (l.get("page") or 0) == page_index):
                kid_nodes: List[Any] = []
                for kid_s in l.get("kids", []):
                    if kid_s == "LI":
                        kid_nodes.append(
                            ListItemNode(
                                id=next_id(f"{page_label}-tli"),
                                content=NodeContent(kind=ContentKind.TEXT, text=" "),
                                metadata=_node_metadata(page_index=page_index + 1),
                                children=[],
                                accessibility_flags=[],
                            )
                        )
                    else:
                        kid_nodes.append(
                            ParagraphNode(
                                id=next_id(f"{page_label}-tlp"),
                                content=NodeContent(kind=ContentKind.TEXT, text=" "),
                                metadata=_node_metadata(page_index=page_index + 1),
                                children=[],
                                accessibility_flags=[],
                            )
                        )
                if kid_nodes:
                    section.children.append(
                        ListNode(
                            id=next_id(f"{page_label}-tlist"),
                            content=NodeContent(kind=ContentKind.NONE),
                            metadata=_node_metadata(
                                page_index=page_index + 1, from_tags=True, page_size=geo.get("page_size"), page_rotate=geo.get("rotate"),
                                bbox=_rel_box(_struct_text_box(l.get("text"), _words), geo),
                            ),
                            children=kid_nodes,
                            accessibility_flags=[],
                        )
                    )

            # --- Images ------------------------------------------------------
            page_image_count = 0
            page_images = _iter_image_xobjects(page, reader)
            placements: Dict[str, Tuple[float, float, float, float]] = {}
            if page_images:
                try:
                    placements = _image_placements(ContentStream(page.get_contents(), reader).operations)
                except Exception:
                    placements = {}
            for image_idx, (name, xobject) in enumerate(page_images, start=1):
                page_image_count += 1
                alt_text, decorative = _alt_for_xobject(xobject)
                if alt_text is None and not decorative:
                    # Properly tagged PDFs keep alt on the Figure StructElem —
                    # honour it instead of false-flagging the image.
                    tree_alt = tree_alt_by_xobject.get(str(name).lstrip("/"))
                    if tree_alt:
                        alt_text = tree_alt
                # Only inline image bytes when a vision provider could read
                # them. Under the heuristic provider (no AI key, or the free
                # analyze path in most deployments) nothing consumes them, and
                # base64-inflating up to 2 MB per image into the tree cost
                # +94 MB RSS per request on a scan-like PDF.
                if _WANT_IMAGE_BYTES:
                    image_b64, image_mime = _extract_image_bytes(xobject)
                else:
                    image_b64, image_mime = None, None
                raw_box = placements.get(str(name).lstrip("/"))
                caption = None
                if raw_box is not None and not alt_text and not decorative:
                    ws = _words()
                    caption = _caption_for_image(raw_box, ws, placements.values()) if ws else None
                extra: Dict[str, Any] = {
                    "bbox": _rel_box(raw_box, geo),
                    "page_size": geo.get("page_size"),
                    "page_rotate": geo.get("rotate"),
                }
                if caption:
                    # The figure's own printed caption — authored words on the
                    # page, next to the image. The alt-text step reads
                    # properties["caption"], exactly as it does for a DOCX
                    # Caption paragraph or an HTML <figcaption>.
                    extra["caption"] = caption
                    extra["caption_source"] = "figure_label"
                if not alt_text and not decorative and _thumbnail_budget["left"] > 0:
                    thumb = _image_thumbnail(xobject)
                    if thumb:
                        _thumbnail_budget["left"] -= 1
                        extra["thumbnail"] = thumb
                section.children.append(
                    _build_image_node(
                        node_id=next_id(f"{page_label}-img{image_idx}"),
                        page=page_index + 1,
                        alt_text=alt_text,
                        decorative=decorative,
                        xobject_name=name,
                        image_b64=image_b64,
                        image_mime=image_mime,
                        extra=extra,
                    )
                )
            # A page with image content but essentially no extractable text is
            # almost certainly a scanned page (or a poster/infographic). Used
            # downstream to flag SCANNED_DOCUMENT_NO_TEXT. Only when the
            # pictures cover the page: a short page with a line of real text
            # and a small photo is not "pictures of text" and does not need OCR.
            if page_image_count > 0 and page_text_chars < 50 and _images_cover_page(page_images, placements, geo):
                image_only_pages += 1

            # --- Links -------------------------------------------------------
            # The link's name is the words PRINTED under its rectangle — what a
            # sighted reader sees and what the tagger nests in /Link. Then the
            # author's /Contents. An external URI is a last resort (it is what
            # AT falls back to, and the analyzer rightly flags it); an internal
            # "#page-N" target is never a name.
            for link_idx, link in enumerate(_link_annotations(page, dests), start=1):
                visible = None
                if link.get("rect") is not None:
                    ws = _words()
                    if ws:
                        from app.pdf.text_geometry import text_in_rect

                        visible = text_in_rect(ws, link["rect"])
                target = link["target"] or ""
                if visible:
                    text, source = visible, "page"
                elif link["contents"]:
                    text, source = link["contents"], "contents"
                elif target and not target.startswith("#"):
                    text, source = target, "uri"
                else:
                    text, source = "", "none"
                link_meta = _node_metadata(
                    page_index=page_index + 1,
                    bbox=_rel_box(link.get("rect"), geo),
                    page_size=geo.get("page_size"), page_rotate=geo.get("rotate"),
                    link_text_source=source,
                )
                if not text:
                    # Nothing printed under it, no /Contents, no URI: the link
                    # has no accessible name at all (LINK_NAME_MISSING), which
                    # is what it is — not "(link)" text passed off as a name.
                    link_meta.properties["__link_nameless"] = True
                section.children.append(
                    LinkNode(
                        id=next_id(f"{page_label}-link{link_idx}"),
                        target=target or None,
                        content=(
                            NodeContent(kind=ContentKind.TEXT, text=text)
                            if text
                            else NodeContent(kind=ContentKind.NONE)
                        ),
                        metadata=link_meta,
                        children=[],
                        accessibility_flags=[],
                    )
                )

            root.children.append(section)

        # Stash scan-detection signals on the root for ScannedDocumentAnalyzer.
        root.metadata.properties["page_count"] = page_count
        root.metadata.properties["total_text_chars"] = total_text_chars
        root.metadata.properties["image_only_pages"] = image_only_pages
        if extraction_failed_pages:
            # Pages whose text exists (show-text operators paint it) but could
            # not be decoded to characters. Disclosed, and nothing derived from
            # those pages' bytes — a title, a header row — is claimed.
            root.metadata.properties["text_extraction_failed_pages"] = extraction_failed_pages[:200]
        if undecodable_pages:
            root.metadata.properties["text_undecodable_pages"] = undecodable_pages[:200]

        # Is this a TAGGED PDF (has a structure tree)? Untagged PDFs are the
        # single most common real-world accessibility failure — and the thing
        # our remediation genuinely fixes (the writer reconstructs a full
        # struct tree). UntaggedPdfAnalyzer flags it; DocumentHeadingsAnalyzer
        # also uses this to avoid contradicting our own tagged output.
        try:
            catalog = reader.trailer["/Root"]
            struct = catalog.get("/StructTreeRoot")
            root.metadata.properties["pdf_tagged"] = bool(
                struct.get_object() if hasattr(struct, "get_object") else struct
            )
        except Exception:
            root.metadata.properties["pdf_tagged"] = False

        raw_metadata: Dict[str, Any] = {
            "page_count": page_count,
            "title": title,
            "language": language,
        }
        tree = AccessibilityTree(root=root, metadata=raw_metadata)
        return ParserResult(
            document_id=document_id,
            format="pdf",
            tree=tree,
            raw_metadata=raw_metadata,
        )


_MAX_THUMBNAILS = 30
_THUMB_MAX_W = 240
# Never decode an image bigger than this just to preview it (pixels).
_THUMB_MAX_SOURCE_PIXELS = 4_000_000  # full-page 300-dpi scans (~8.4 MP) are skipped


def _page_geometry(page: Any) -> Dict[str, Any]:
    """The visible page box: ``{"page_size": [w, h], "origin": (x, y)}``.

    Uses the CropBox (what a viewer shows; pypdf defaults it to the
    MediaBox). Finding bboxes are reported relative to its bottom-left corner
    so ``[0, 0, w, h]`` is always the whole visible page.
    """
    try:
        box = page.cropbox
        x0, y0 = float(box.left), float(box.bottom)
        w, h = float(box.width), float(box.height)
        if w <= 0 or h <= 0:
            return {}
        out: Dict[str, Any] = {"page_size": [round(w, 2), round(h, 2)], "origin": (x0, y0)}
        try:
            rot = int(page.get("/Rotate", 0) or 0) % 360
        except Exception:
            rot = 0
        if rot:
            out["rotate"] = rot
        return out
    except Exception:
        return {}


def _images_cover_page(page_images: Any, placements: Dict[str, Any], geo: Dict[str, Any]) -> bool:
    """Do a page's pictures cover at least half of it (a scan's picture of the
    page does)? A picture whose placement was not measured counts as covering
    the page, which is the reading the page had before placements existed."""
    size = (geo or {}).get("page_size")
    try:
        w, h = float(size[0]), float(size[1])
    except (TypeError, ValueError, IndexError):
        return True
    if w <= 0 or h <= 0:
        return True
    total = 0.0
    for name, _xobject in page_images:
        box = placements.get(str(name).lstrip("/"))
        if box is None:
            return True
        x0, y0, x1, y1 = (float(v) for v in box)
        total += abs(x1 - x0) * abs(y1 - y0)
    return total >= 0.5 * w * h


def _rel_box(box: Any, geo: Dict[str, Any]) -> Optional[List[float]]:
    """``box`` (user space) relative to the page box origin, clamped to it."""
    if not box or not geo or "page_size" not in geo:
        return None
    try:
        ox, oy = geo.get("origin", (0.0, 0.0))
        w, h = geo["page_size"]
        x0, y0, x1, y1 = (float(v) for v in list(box)[:4])
        x0, x1 = sorted((x0 - ox, x1 - ox))
        y0, y1 = sorted((y0 - oy, y1 - oy))
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(float(w), x1), min(float(h), y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]
    except Exception:
        return None


def _page_uses_composite_font(page: Any) -> bool:
    """Does the page's font resource dict hold a /Type0 font? (cheap check)"""
    from app.pdf.text_decode import page_fonts

    try:
        fonts = page_fonts(page)
        if not isinstance(fonts, DictionaryObject):
            return False
        return any(
            str((_resolve(ref) or {}).get("/Subtype") or "") == "/Type0" for _n, ref in fonts.items()
        )
    except Exception:
        return True  # unsure: take the careful path


def _text_op_census(reader: Any, page: Any, font_cache: Dict[Any, Any]) -> Tuple[int, float]:
    """``(chars, undecodable_share)`` from the page's show-text operators.

    Independent of ``extract_text``: counts what the content stream PAINTS
    (about one character per byte for simple fonts, per 2 bytes for
    composite fonts), and what share of it is in a composite font we cannot
    map to Unicode.
    """
    from app.pdf.ua_tagger import _block_weight, _iter_text_blocks, _page_decoder

    try:
        ops = ContentStream(page.get_contents(), reader).operations
    except Exception:
        return 0, 0.0
    total = 0
    bad = 0
    for b in _iter_text_blocks(ops, _page_decoder(reader, page, font_cache)):
        w = _block_weight(b)
        total += w
        if getattr(b, "undecodable", False):
            bad += w
    return total, (bad / total if total else 0.0)


def _image_thumbnail(xobject: Any) -> Optional[str]:
    """A small PNG preview (<= 240 px on its longer side) as a data URI, or None.

    Decoded by the finding-location thumbnailer, whose memory is bounded by
    the image's DECLARED size, never by what its stream inflates to. pypdf's
    own decoder inflates the whole stream first: a 255 KB PDF whose 1000 x 1000
    picture inflates to 256 MB took one anonymous analyze to ~560 MB.
    Anything huge, exotic or failing is simply skipped — a missing preview is
    fine, a stalled request is not.
    """
    try:
        w = int(xobject.get("/Width") or 0)
        h = int(xobject.get("/Height") or 0)
        if w <= 0 or h <= 0 or w * h > _THUMB_MAX_SOURCE_PIXELS:
            return None
        from app.services.finding_location import _pdf_xobject_image, png_thumbnail_data_uri

        img = _pdf_xobject_image(xobject)
        if img is None:
            return None
        uri = png_thumbnail_data_uri(img)
        # Keep each preview small: a report can carry dozens of them.
        if uri is None or len(uri) > 80_000:
            return None
        return uri
    except Exception:
        return None


def _build_image_node(
    *,
    node_id: str,
    page: int,
    alt_text: Optional[str],
    decorative: bool,
    xobject_name: str,
    image_b64: Optional[str] = None,
    image_mime: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> ImageNode:
    properties: Dict[str, Any] = {"xobject": xobject_name}
    if image_b64:
        properties["image_b64"] = image_b64
        properties["image_mime"] = image_mime or "image/png"
    for k, v in (extra or {}).items():
        if v is not None:
            properties[k] = v
    metadata = NodeMetadata(
        page=page,
        source_format="pdf",
        properties=properties,
    )
    if decorative and alt_text:
        # Cannot construct a normal decorative ImageNode if alt_text non-empty —
        # use ``model_construct`` to bypass the validator so the analyzer can
        # flag DECORATIVE_IMAGE_WITH_ALT and the remediator can clean it up.
        return ImageNode.model_construct(
            id=node_id,
            node_type=ImageNode.type_value(),
            content=NodeContent(kind=ContentKind.NONE),
            metadata=metadata,
            children=[],
            accessibility_flags=[],
            is_decorative=True,
            alt_text=alt_text,
        )
    return ImageNode(
        id=node_id,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=metadata,
        children=[],
        accessibility_flags=[],
        is_decorative=decorative,
        alt_text=alt_text,
    )


class _IdCounter:
    """Generate stable, unique node ids while preserving a hint."""

    def __init__(self) -> None:
        self._seen: Dict[str, int] = {}

    def __call__(self, hint: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", hint).strip("-") or "node"
        count = self._seen.get(cleaned, 0)
        self._seen[cleaned] = count + 1
        if count == 0:
            return cleaned
        return f"{cleaned}-{count}"

# alias for the public `parse_to_tree` convention used elsewhere
PDFParser.parse_to_tree = PDFParser.parse  # type: ignore[attr-defined]
