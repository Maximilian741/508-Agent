"""Add a real (basic) PDF/UA structure to a pypdf ``PdfWriter``.

This turns an *untagged* PDF into a *tagged* one without rewriting the page
content bytes (so the original rendering is preserved). It applies the
document-level PDF/UA essentials that are unambiguously correct, plus a linked
structure tree:

Document-level (always):
* ``/Lang`` on the catalog                                  (WCAG 3.1.1)
* ``/ViewerPreferences << /DisplayDocTitle true >>``        (WCAG 2.4.2 / PDF-UA 7.1)
* an XMP ``/Metadata`` stream carrying ``dc:title`` + ``pdfuaid:part 1``

Structure (best-effort, never corrupts):
* page content is segmented into text blocks + images and each gets its own
  marked-content sequence (``BDC … EMC``) with an MCID — re-serialized with a
  hard guard that the visible text is byte-identical (any anomaly falls back to a
  single page-level ``/P`` wrap, original bytes untouched).
* a linked ``/StructTreeRoot`` → ``/Document`` is built with a correct
  ``/ParentTree``; the catalog is marked ``/MarkInfo << /Marked true >>``.

Per-element structure reconstructed from the flat content stream:
* headings ``/H1``..``/H6`` by relative font size;
* images ``/Figure`` with ``/Alt`` (from the analysis tree);
* LISTS ``/L`` → ``/LI`` → ``/LBody`` from runs of bullet/ordinal text blocks;
* TABLES ``/Table`` → ``/TR`` → ``/TH``/``/TD`` from positioned grids of text.

Table detection is heuristic and HIGH-PRECISION by design: it only tags a grid
when it is unambiguous (>=3 rows, aligned columns, short data-like cells, no
form-label/filler columns, no interrupting figure, split on large row gaps). It
intentionally MISSES ambiguous grids (borderless / form-like / TOC-like) rather
than mis-tag a non-table — verified against an adversarial review (forms, TOCs,
two-column prose stay plain text). We never claim full PDF/UA conformance.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NullObject,
    NumberObject,
    TextStringObject,
)

from app.models.accessibility import AccessibilityTree, DocumentNode

logger = logging.getLogger(__name__)


def _resolve(obj: Any) -> Any:
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


def _xmp_packet(title: Optional[str], language: Optional[str], pdfua_part: Optional[int] = None) -> bytes:
    """Build a minimal, valid XMP packet with dc:title (+ pdfuaid:part).

    ``pdfuaid:part`` asserts that the file CONFORMS to PDF/UA. It is included
    only when the caller has a basis for that claim (see :func:`tag_pdf`)."""
    def esc(s: str) -> str:
        return (
            s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    title_block = ""
    if title:
        title_block = (
            "<dc:title><rdf:Alt>"
            f'<rdf:li xml:lang="x-default">{esc(str(title))}</rdf:li>'
            "</rdf:Alt></dc:title>"
        )
    lang_attr = f' xml:lang="{esc(str(language))}"' if language else ""
    part_block = f"<pdfuaid:part>{int(pdfua_part)}</pdfuaid:part>" if pdfua_part else ""
    ua_ns = ' xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/"' if pdfua_part else ""
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        f'<rdf:Description rdf:about=""{lang_attr} '
        f'xmlns:dc="http://purl.org/dc/elements/1.1/"{ua_ns}>'
        f"{title_block}"
        f"{part_block}"
        "</rdf:Description>"
        "</rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


def _wrap_page_marked_content(writer: PdfWriter, page: Any, mcid: int = 0) -> bool:
    """Wrap a page's content in a single ``/P <</MCID n>> BDC … EMC`` sequence.

    Implemented by prepending/appending NEW content streams so the original
    content streams are kept untouched (fidelity-preserving). Returns True on
    success.
    """
    prefix = DecodedStreamObject()
    prefix.set_data(("/P <</MCID %d>> BDC\n" % mcid).encode("latin-1"))
    suffix = DecodedStreamObject()
    suffix.set_data(b"\nEMC\n")
    pref_ref = writer._add_object(prefix)  # noqa: SLF001 - pypdf object table
    suf_ref = writer._add_object(suffix)  # noqa: SLF001

    contents = page.raw_get("/Contents") if "/Contents" in page else None
    if contents is None:
        page[NameObject("/Contents")] = ArrayObject([pref_ref, suf_ref])
        return True

    resolved = _resolve(contents)
    if isinstance(resolved, ArrayObject):
        # Keep the existing (indirect) stream refs; just bracket them.
        new_arr = ArrayObject([pref_ref] + list(resolved) + [suf_ref])
    else:
        # Single content stream: `contents` is the indirect ref to it.
        new_arr = ArrayObject([pref_ref, contents, suf_ref])
    page[NameObject("/Contents")] = new_arr
    return True


def _page_content_bytes(page: Any) -> bytes:
    """Best-effort decoded content bytes for a page (single or array streams)."""
    try:
        c = page.raw_get("/Contents") if "/Contents" in page else None
    except Exception:
        return b""
    c = _resolve(c)
    if c is None:
        return b""
    try:
        if isinstance(c, ArrayObject):
            parts = []
            for item in c:
                obj = _resolve(item)
                if obj is not None:
                    parts.append(obj.get_data())
            return b"\n".join(parts)
        return c.get_data()
    except Exception:
        return b""


def _is_already_tagged(catalog: DictionaryObject) -> bool:
    """True if the PDF already carries a structure tree / is marked Tagged.

    We must NOT touch already-tagged PDFs: overwriting an existing
    ``/StructTreeRoot`` destroys real structure, and prepending another
    marked-content sequence collides MCIDs. For those we apply only safe,
    additive document metadata and leave the structure alone.
    """
    try:
        if _resolve(catalog.get("/StructTreeRoot")) is not None:
            return True
        mi = _resolve(catalog.get("/MarkInfo"))
        if isinstance(mi, DictionaryObject) and bool(_resolve(mi.get("/Marked"))):
            return True
    except Exception:
        return True  # if unsure, treat as tagged (never risk corruption)
    return False


def _is_taggable_page(pdf, page: Any) -> bool:
    """A page we can safely wrap: has content and no existing marked content.

    The marked-content check is done at the OPERATOR level (via the tokenized
    content stream), not a raw-bytes substring — so a page whose *visible text*
    contains an acronym like "BDC" or "BMC" is not wrongly excluded.
    """
    if "/Contents" not in page:
        return False
    try:
        contents = page.get_contents()
        if contents is None:
            return False
        ops = ContentStream(contents, pdf).operations
    except Exception:
        return False
    if not ops:
        return False
    # Already tagged / has marked content or artifacts → leave it alone.
    if any(op in (b"BDC", b"BMC", b"EMC") for _, op in ops):
        return False
    # Require some actual text/drawing content to wrap.
    return any(op in (b"Tj", b"TJ", b"'", b'"', b"Do") for _, op in ops)


def _build_alt_by_xobject(tree: AccessibilityTree) -> Dict[int, Dict[str, str]]:
    """Map 1-based page number -> {XObject name -> alt text}, for /Figure tagging.

    Keyed by PAGE first. XObject names are per-page resource keys and
    producers reuse them — every page of a scanned PDF is /Im0 — so a flat
    {name: alt} map kept whichever page's node came last and stamped that
    page's description onto every other page's figure. The ImageNode records
    its 1-based page; the tagger passes each page its own slice.
    """
    out: Dict[int, Dict[str, str]] = {}
    try:
        from app.models.accessibility import ImageNode, iter_reading_order

        for node in iter_reading_order(tree.root):
            if isinstance(node, ImageNode) and not node.is_decorative and node.alt_text:
                xname = (node.metadata.properties or {}).get("xobject")
                pg = getattr(node.metadata, "page", None)
                if isinstance(xname, str) and xname and isinstance(pg, int):
                    out.setdefault(pg, {})[xname.lstrip("/")] = str(node.alt_text)
    except Exception:  # pragma: no cover - defensive
        pass
    return out


def _build_decorative_by_xobject(tree: AccessibilityTree) -> Dict[int, set]:
    """1-based page -> XObject names the analysis tree marks DECORATIVE.

    Those images are wrapped ``/Artifact`` (kept out of the reading order, the
    PDF/UA way to say "decoration") instead of being tagged as figures."""
    out: Dict[int, set] = {}
    try:
        from app.models.accessibility import ImageNode, iter_reading_order

        for node in iter_reading_order(tree.root):
            if isinstance(node, ImageNode) and node.is_decorative and not node.alt_text:
                xname = (node.metadata.properties or {}).get("xobject")
                pg = getattr(node.metadata, "page", None)
                if isinstance(xname, str) and xname and isinstance(pg, int):
                    out.setdefault(pg, set()).add(xname.lstrip("/"))
    except Exception:  # pragma: no cover - defensive
        pass
    return out


def _count_text_ops(ops) -> int:
    return sum(1 for _, opc in ops if opc in (b"Tj", b"TJ", b"'", b'"'))


def _block_font_size(block_ops) -> float:
    """Largest EFFECTIVE font size shown inside a BT..ET block (0 if no Tf).

    Effective = the ``Tf`` size times the text matrix's vertical scale. Most
    producers put the size in ``Tf`` and leave ``Tm`` unscaled, but Cairo and
    Skia write ``1 Tf`` and carry the size in ``Tm`` ("12 0 0 12 x y Tm"): read
    as Tf alone every block was "1pt", so such documents got no headings and
    no title at all. (A uniform CTM scale cancels out: every size decision
    here is a ratio to body text.)
    """
    import math

    size = 0.0
    tf = None
    tm_scale = 1.0
    for operands, op in block_ops:
        try:
            if op == b"Tf" and len(operands) >= 2:
                tf = float(operands[1])
            elif op == b"Tm" and len(operands) >= 6:
                c, d = float(operands[2]), float(operands[3])
                s = math.hypot(c, d)
                tm_scale = s if s > 1e-6 else 1.0
            elif op in (b"Tj", b"TJ", b"'", b'"') and tf is not None:
                size = max(size, abs(tf) * tm_scale)
        except (TypeError, ValueError):
            continue
    if size == 0.0 and tf is not None:
        size = abs(tf) * tm_scale  # a Tf with no show op after it
    return round(size, 2)


def _heading_levels(sizes: List[float], weights: Optional[List[int]] = None) -> Dict[float, int]:
    """Map block font-size -> heading level (1..6); body-text sizes are absent.

    Body text is the size that carries the most TEXT (sum of characters), and
    sizes meaningfully larger than body are headings (largest -> H1). Returns
    {} when there is no clear body size (so a page with one size yields only
    /P, never spurious headings).

    Weighted by characters, not by block count, on purpose. Counting blocks
    let a data table decide what "body" was: an 8x10 grid is eighty tiny 8pt
    blocks against ten 12pt body sentences, so 8pt "won", every 12pt paragraph
    became /H3, and the output had zero /P — a fabricated outline, credited as
    a fix. Characters measure where the prose actually is; eighty three-digit
    cells are ~240 characters against ~800 of body text.

    ``weights[i]`` is the character count of block ``i``; when omitted every
    block weighs 1 (block-count behaviour, for callers that have no text).
    """
    real: List[Tuple[float, int]] = []
    for i, s in enumerate(sizes):
        if s and s > 0:
            w = 1
            if weights is not None and i < len(weights):
                w = max(1, int(weights[i] or 0))
            real.append((round(s, 1), w))
    if len(real) < 2:
        return {}
    volume: Counter = Counter()
    for sz, w in real:
        volume[sz] += w
    top = max(volume.values())
    # Body text = the size carrying the most characters; on a tie (e.g. one
    # title + one body line of equal length) prefer the SMALLER size as body
    # so the larger is recognised as a heading.
    body = min(s for s, v in volume.items() if v == top)
    heading_sizes = sorted({s for s, _w in real if s > body * 1.15}, reverse=True)
    return {s: min(i + 1, 6) for i, s in enumerate(heading_sizes)}


# Unambiguous bullet glyphs (always a list marker) and weak ones (only when
# followed by whitespace, so "-5C" or a stray asterisk isn't mistaken for a list).
# Content-stream text is in the font's encoding, not Unicode — for single-byte
# fonts (the common case) a bullet arrives as the raw WinAnsi/MacRoman byte
# (latin-1 decoded), so include those byte chars alongside the Unicode glyphs.
_STRONG_BULLETS = set("•‣◦▪▫●○■□⁃") | {"\x95", "\xa5"}  # WinAnsi/MacRoman bullet
# Hyphen / asterisk / middot are real list markers; en/em dashes are excluded —
# consecutive em-dash lines are far more often dialogue/quotes than a list.
_WEAK_BULLETS = set("-*·")
# Ordinal markers: "1.", "1)", "(1)", "a.", "iv)", etc. — a number/letter/roman
# numeral then a . or ) then whitespace and more text.
_ORDINAL_RE = re.compile(
    r"^\(?\s*(?:\d{1,3}|[ivxlcdmIVXLCDM]{1,7}|[A-Za-z])\s*[.)]\s+\S"
)


def _operand_str(obj: Any) -> str:
    """Best-effort text of a single show-text operand for MARKER detection.

    We prefer the RAW operand bytes (latin-1) over pypdf's decoded string: list
    markers are font-encoded single bytes (e.g. a WinAnsi bullet is 0x95), and
    pypdf decodes string operands with PDFDocEncoding, which maps 0x95 to 'Ł' —
    so the decoded text loses the bullet. The raw byte value is what our bullet
    set is keyed on, and it gives a correct length for the table prose guard.
    """
    try:
        raw = getattr(obj, "original_bytes", None)
        if raw is not None:
            return bytes(raw).decode("latin-1", "ignore")
        if isinstance(obj, (bytes, bytearray)):
            return bytes(obj).decode("latin-1", "ignore")
        return str(obj)
    except Exception:
        return ""


class _TextBlock(list):
    """A BT..ET op list that also knows what its text SAYS.

    Behaves exactly like the plain op list every helper already takes (it is
    re-emitted verbatim into the content stream), plus:

    * ``text_override`` — the decoded text when the block shows glyphs in a
      composite (``/Type0``) font, where the raw operand bytes are 2-byte glyph
      ids, not characters. ``""`` when that text could not be decoded: every
      text test then sees nothing and makes no claim (fail closed). ``None``
      for simple fonts, whose raw bytes remain the source of truth.
    * ``undecodable`` — True when a composite font had no usable ToUnicode.
    * ``fonts`` — the font resource names this block paints with.
    """

    __slots__ = ("text_override", "undecodable", "fonts", "raw_len", "start_font", "angle")

    def __init__(self, *args: Any) -> None:
        super().__init__(*args)
        self.text_override: Optional[str] = None
        self.undecodable = False
        self.fonts: List[str] = []
        self.raw_len = 0
        self.start_font: Optional[str] = None
        # Baseline direction on the page, degrees (0 = ordinary horizontal
        # text), from the text matrix composed with the CTM at BT.
        self.angle = 0


def _block_angle(block_ops, ctm) -> int:
    """Baseline angle of a block in whole degrees, snapped to 5 (0..355).

    Text space maps through ``Tm x CTM`` as they stand at the block's FIRST
    show op; the baseline direction is the image of the x axis. ``ctm`` is
    the CTM at BT. A ``cm`` inside BT is part of it: PyMuPDF's
    ``insert_text(morph=...)`` writes a diagonal stamp's rotation there, and
    reading the CTM at BT alone called that stamp upright (it became the
    title and the H1 of every page)."""
    import math

    m = _IDENTITY_CTM
    cur = ctm
    for operands, op in block_ops:
        if op == b"cm" and len(operands) >= 6:
            try:
                cur = _ctm_concat(tuple(float(o) for o in operands[:6]), cur)
            except (TypeError, ValueError):
                pass
        elif op == b"Tm" and len(operands) >= 6:
            try:
                m = tuple(float(o) for o in operands[:6])
            except (TypeError, ValueError):
                pass
        elif op in (b"Tj", b"TJ", b"'", b'"'):
            break
    try:
        a, b = _ctm_concat(m, cur)[:2]
        if abs(a) < 1e-9 and abs(b) < 1e-9:
            return 0
        return (int(round(math.degrees(math.atan2(b, a)) / 5.0)) * 5) % 360
    except Exception:
        return 0


def _block_cm_after(block_ops, ctm):
    """The CTM after a block: a ``cm`` inside BT (MuPDF) outlives the ET."""
    for operands, op in block_ops:
        if op == b"cm" and len(operands) >= 6:
            try:
                ctm = _ctm_concat(tuple(float(o) for o in operands[:6]), ctm)
            except (TypeError, ValueError):
                pass
    return ctm


def _dominant_angle(blocks, prefer: int = 0) -> int:
    """The page's text axis: the baseline angle shared by the most text
    BLOCKS; characters break a tie, then ``prefer`` (upright by default).

    It used to be a character vote. On a sparse cover one long diagonal
    "DRAFT - NOT FOR DISTRIBUTION" out-weighed the upright title and date
    together, so the watermark became the page's own axis, the real title
    counted as "off-axis", and the stamp was written as the document title
    and tagged H1. A stamp is one block; a page's text is several."""
    n: Counter = Counter()
    vol: Counter = Counter()
    for b in blocks:
        w = _block_weight(b)
        if w <= 0:
            continue
        ang = getattr(b, "angle", 0)
        n[ang] += 1
        vol[ang] += w
    if not n:
        return prefer
    top = max(n.values())
    tops = [a for a, c in n.items() if c == top]
    if len(tops) > 1:
        if prefer in tops:
            return prefer
        best = max(vol[a] for a in tops)
        tops = [a for a in tops if vol[a] == best]
    return prefer if prefer in tops else tops[0]


def _off_axis(block, dominant: int) -> bool:
    """Text set at an angle to the page's own text — a diagonal "DRAFT"
    watermark, a rotated margin label. Never a heading or a title."""
    return getattr(block, "angle", 0) != dominant


def _norm_block_text(block) -> str:
    return re.sub(r"\s+", " ", _block_text(block)).strip().casefold()


def _page_axes(pages_blocks) -> Tuple[List[int], frozenset]:
    """Each page's text axis, and the document's watermark texts.

    A watermark is the same text set at an angle to the page's own text on
    two or more pages ("DRAFT - NOT FOR DISTRIBUTION" stamped diagonally on
    every page). Watermarks then do not vote on any page's axis: on a sparse
    cover a stamp drawn as two blocks still out-numbered the title, and
    became the page's H1 and the document title.
    """
    naive = [_dominant_angle(bs) for bs in pages_blocks]
    off_pages: Counter = Counter()
    for bs, ax in zip(pages_blocks, naive):
        here = {
            _norm_block_text(b) for b in bs
            if _off_axis(b, ax) and not getattr(b, "undecodable", False)
        }
        here.discard("")
        off_pages.update(here)
    marks = frozenset(t for t, n in off_pages.items() if n >= 2)
    if not marks:
        return naive, marks
    axes = [
        _dominant_angle([b for b in bs if _norm_block_text(b) not in marks])
        for bs in pages_blocks
    ]
    return axes, marks


def _decorate_block(block: "_TextBlock", decoder: Any, start_font: Optional[str]) -> "_TextBlock":
    """Fill in ``block``'s decoded text (see :class:`_TextBlock`). Never raises."""
    block.start_font = start_font
    if decoder is None:
        return block
    try:
        from app.pdf.text_decode import FontState, decode_ops, operand_bytes, show_strings

        # Cheap pass first: which fonts does the block paint with? Only a
        # block that uses a composite font needs decoding at all (the common
        # single-byte case keeps its raw-bytes path untouched, at no cost).
        state = FontState(start_font)
        for operands, op in block:
            state.feed(operands, op)
            if op in (b"Tj", b"TJ", b"'", b'"') and state.font and state.font not in block.fonts:
                block.fonts.append(state.font)
        if not any(decoder.is_composite(f) for f in block.fonts):
            return block
        text, used_composite, _end = decode_ops(block, decoder, start_font)
        if used_composite:
            block.text_override = text if text is not None else ""
            block.undecodable = text is None
        if block.undecodable:
            n = 0
            for operands, op in block:
                if op in (b"Tj", b"TJ", b"'", b'"'):
                    for s in show_strings(operands, op):
                        n += len(operand_bytes(s))
            block.raw_len = n
    except Exception:  # pragma: no cover - decoding is best-effort
        logger.debug("block decode failed", exc_info=True)
    return block


def _iter_text_blocks(ops, decoder: Any = None):
    """Yield each top-level BT..ET block of ``ops`` as a decorated
    :class:`_TextBlock`, tracking the text font across blocks (``Tf`` is
    graphics state: it persists past ET and is saved/restored by q/Q)."""
    from app.pdf.text_decode import FontState

    state = FontState()
    block = None
    start_font = None
    ctm = _IDENTITY_CTM
    ctm_stack: List[tuple] = []
    for operands, op in ops:
        if op == b"BT" and block is None:
            block = _TextBlock([(operands, op)])
            start_font = state.font
        elif op == b"ET" and block is not None:
            block.append((operands, op))
            block.angle = _block_angle(block, ctm)
            ctm = _block_cm_after(block, ctm)
            yield _decorate_block(block, decoder, start_font)
            block = None
        elif block is not None:
            block.append((operands, op))
        elif op == b"q":
            ctm_stack.append(ctm)
        elif op == b"Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
        elif op == b"cm" and len(operands) >= 6:
            try:
                ctm = _ctm_concat(tuple(float(o) for o in operands[:6]), ctm)
            except (TypeError, ValueError):
                pass
        state.feed(operands, op)


def _block_weight(block_ops) -> int:
    """Characters a block carries, for the body-size vote. An undecodable
    composite block still carries text (about one glyph per 2 bytes) even
    though we will not read it."""
    if getattr(block_ops, "undecodable", False):
        return max(1, int(getattr(block_ops, "raw_len", 0) or 0) // 2)
    return len(_block_text(block_ops).strip())


def _page_decoder(pdf: Any, page: Any, cache: Optional[Dict[Any, Any]] = None):
    try:
        from app.pdf.text_decode import FontDecoder

        return FontDecoder(page, cache)
    except Exception:  # pragma: no cover
        return None


def _block_text(block_ops) -> str:
    """Concatenate the visible text shown by a BT..ET block's Tj/TJ/'/" ops."""
    override = getattr(block_ops, "text_override", None)
    if override is not None:
        return override
    parts: List[str] = []
    for operands, op in block_ops:
        if op == b"Tj" and operands:
            parts.append(_operand_str(operands[0]))
        elif op == b"TJ" and operands and isinstance(operands[0], (list, ArrayObject)):
            for el in operands[0]:
                if not isinstance(el, NumberObject) and not isinstance(el, (int, float)):
                    parts.append(_operand_str(el))
        elif op in (b"'", b'"') and operands:
            parts.append(_operand_str(operands[-1]))
    return "".join(parts)


def _is_list_item(text: str) -> bool:
    """True when ``text`` begins with a bullet or an ordinal list marker.

    Conservative: strong bullets always count; weak ones (-, *, dashes) only when
    followed by whitespace; ordinals need a separator and following text."""
    t = (text or "").strip()
    if not t:
        return False
    c = t[0]
    if c in _STRONG_BULLETS:
        return True
    if c in _WEAK_BULLETS and len(t) > 1 and t[1] in " \t":
        return True
    return bool(_ORDINAL_RE.match(t))


# Table detection tolerances (PDF user-space units ~= points).
_ROW_TOL = 4.0   # text whose baselines are within this are the same row
_COL_TOL = 10.0  # column x-origins must line up within this across rows


def _block_origin(block_ops) -> tuple:
    """The (x, y) text origin of a block, from its first Td/TD/Tm operator.

    ``BT`` resets the text + line matrices to identity, so the first ``x y Td``
    in a block is effectively its absolute origin; ``Tm`` carries the translation
    in operands 4/5. Returns (None, None) when no positioning op is present.
    """
    for operands, op in block_ops:
        if op in (b"Td", b"TD") and len(operands) >= 2:
            try:
                return float(operands[0]), float(operands[1])
            except Exception:
                return None, None
        if op == b"Tm" and len(operands) >= 6:
            try:
                return float(operands[4]), float(operands[5])
            except Exception:
                return None, None
    return None, None


def _looks_like_data_cells(run) -> bool:
    """Reject grids that are really forms / prose / TOCs rather than data tables.

    An adversarial review showed pure geometry tags forms (``Name:`` / blank),
    two-column prose, and TOCs as tables. Genuine data cells are short, mostly
    not label-style (``foo:``) and not long running prose. Conservative: when in
    doubt we DON'T call it a table (a missed table is far less harmful in
    PDF/UA terms than mis-tagging a form as one)."""
    ncols = len(run[0])
    cells = [blk[3] for r in run for blk in r]
    # 1) Prose: a column layout whose cells are long lines, not table values.
    if sum(1 for t in cells if len(t) > 40) > len(cells) / 2:
        return False
    # 2) Prose by word count: real cells are short (<=3 words); sentences aren't.
    word_counts = sorted(len(t.split()) for t in cells)
    median_words = word_counts[len(word_counts) // 2]
    if median_words > 3:
        return False
    # 3) Form: any column that is mostly "label:" or blank/underscore fillers.
    for c in range(ncols):
        col = [r[c][3].strip() for r in run]
        labelish = sum(1 for t in col if t.endswith(":"))
        fillerish = sum(1 for t in col if t and set(t) <= set("_-.… "))
        if labelish > len(col) / 2 or fillerish > len(col) / 2:
            return False
    return True


_NUMERICISH_RE = re.compile(
    r"^[\s$€£¥(+-]*\d[\d,._/\\:%-]*\s*[)%]?$"  # 1,234  $12.50  (3)  45%  12/31/2025
)


def _cell_is_numericish(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and bool(_NUMERICISH_RE.match(t))


def _row0_is_header(run) -> bool:
    """Whether row 0 should be typed ``/TH`` for a DETECTED data grid.

    PDF carries no header semantics — a grid is just positioned text — so this
    is unavoidably an INFERENCE, and both possible answers are a claim. We pick
    the one that errs best:

    * Labels in the top row is the dominant convention for the strict data
      grids our detectors accept (>=3 aligned rows, consistent column count,
      short data-like cells), and leaving a genuine header untagged is itself a
      WCAG/PDF-UA defect — so the default is "header".
    * But we REFUSE when the content contradicts it, which is where the real
      fabrication risk lives: a row of numbers is data, not labels. Asserting
      ``/TH`` (plus a ``/Scope``) over a numeric matrix would invent a
      relationship that does not exist in the source.

    A grid that fails the test is still tagged as a real ``/Table`` with rows
    and cells — we simply don't claim any cell is a header.
    """
    if len(run) < 2:
        return False
    row0 = [str(blk[3]).strip() for blk in run[0]]
    if not any(row0):
        return False  # a blank top row labels nothing
    # Text we cannot read (glyph codes from an undecodable composite font)
    # is no evidence of labels: claim no header rather than guess.
    if any(any(ord(ch) < 0x20 and ch not in "\t\r\n" for ch in t) for t in row0):
        return False
    if any(_cell_is_numericish(t) for t in row0):
        return False  # numbers on top -> this is data, not a header row
    return True


# --- Column-aware reading order -------------------------------------------
# A multi-column PDF whose content stream interleaves the columns (line 1 left,
# line 1 right, line 2 left, ...) is read out by AT as alternating fragments of
# two unrelated texts — one of the most severe and most common failures in
# government PDFs (WCAG 1.3.2).
#
# We can fix it WITHOUT touching a single rendered byte: the visual output comes
# from the content stream, but PDF/UA reading order comes from the STRUCTURE
# TREE, and we build that. So we leave the stream exactly as-is (marked-content
# ids and painting order unchanged) and emit the structure elements in corrected
# visual order instead.
_MIN_COLUMN_BLOCKS = 8        # too few blocks to be confident about a layout
_MIN_COLUMN_SHARE = 0.30      # each column must hold >=30% of the blocks
_MIN_GUTTER = 40.0            # points of clear horizontal space between columns



def _gutter_candidate(points) -> Optional[float]:
    """The x of a clear vertical gutter with PROSE on both sides, or None.

    The geometric + content half of :func:`_detect_two_columns` (without the
    row-pairing test). On its own it is NOT enough to reorder a page — forms
    and Q/A sheets pass it — but it is enough to say "this page looks like two
    columns", which is what the reading-order disclosure needs.
    """
    xs = sorted(p[0] for p in points)
    n = len(xs)
    if n < _MIN_COLUMN_BLOCKS:
        return None
    best_gap, best_at = 0.0, None
    lo, hi = int(n * _MIN_COLUMN_SHARE), int(n * (1 - _MIN_COLUMN_SHARE))
    for i in range(max(lo, 1), max(hi, 1)):
        gap = xs[i] - xs[i - 1]
        if gap > best_gap:
            best_gap, best_at = gap, (xs[i] + xs[i - 1]) / 2.0
    if best_at is None or best_gap < _MIN_GUTTER:
        return None
    left_pts = [p for p in points if p[0] < best_at]
    right_pts = [p for p in points if p[0] >= best_at]
    if min(len(left_pts), len(right_pts)) < n * _MIN_COLUMN_SHARE:
        return None

    # --- content test: both sides must be prose, not labels/numbers ---
    def _median_words(pts) -> int:
        counts = sorted(len((str(p[2]) or "").split()) for p in pts)
        return counts[len(counts) // 2] if counts else 0

    if _median_words(left_pts) <= 3 or _median_words(right_pts) <= 3:
        return None
    return best_at


_LEADER_RE = re.compile(r"^[\s.·•…‥_]*$")


def _is_leader_text(text: str) -> bool:
    """A block made only of leader dots (at least five of them)."""
    t = (text or "").strip()
    if len(t) < 5 or not _LEADER_RE.match(t):
        return False
    dots = sum(1 for ch in t if ch in ".·…‥")
    return dots >= 5 and "_" not in t


def _column_order(leaves) -> Tuple[list, bool, bool]:
    """``(leaves, reordered, declined)`` for one page.

    ``reordered`` / the returned order come from
    :func:`_reorder_leaves_for_columns`, unchanged. ``declined`` is True when
    the page LOOKS like two columns of prose (a gutter with prose on both
    sides) whose column-major order differs from the stream order, and we
    did not reorder it — the page ships in stream order, and that must be
    said out loud rather than read as "tagged".
    """
    out, changed = _reorder_leaves_for_columns(leaves)
    if changed:
        return out, True, False
    try:
        text_leaves = [
            lf for lf in leaves
            if lf.get("bx") is not None and lf.get("by") is not None and lf.get("tag") != "/Figure"
        ]
        if len(text_leaves) < _MIN_COLUMN_BLOCKS:
            return out, False, False
        pts = [(lf["bx"], lf["by"], lf.get("text") or "") for lf in text_leaves]
        g = _gutter_candidate(pts)
        if g is None:
            return out, False, False
        column_major = (
            _top_to_bottom([lf for lf in text_leaves if lf["bx"] < g])
            + _top_to_bottom([lf for lf in text_leaves if lf["bx"] >= g])
        )
        looks_interleaved = [id(x) for x in column_major] != [id(x) for x in text_leaves]
        return out, False, bool(looks_interleaved)
    except Exception:  # pragma: no cover - disclosure must never break tagging
        return out, False, False


def _detect_two_columns(points) -> Optional[float]:
    """Return the gutter x of a clear 2-column layout of FLOWING TEXT, or None.

    ``points`` is ``[(x, y, text), ...]``.

    Deliberately strict, because reordering a page that is NOT two-column
    CREATES the very defect we're fixing. An adversarial review proved that a
    gap-in-x test alone is nowhere near enough: a label/value form (labels at
    x=72, values at x=300) and a table of contents (entry at x=72, page number
    at x=500) both match "two clusters of origins", and linearizing them
    column-major separates every label from its value — catastrophic, and
    reported as a fix. Neither is rescued by the table/list guards, because the
    table detector deliberately rejects forms and TOCs.

    So a gutter must ALSO survive two content tests:
      * both sides must read like PROSE (median >3 words), which a column of
        short labels or bare page numbers never does; and
      * the sides must not be ROW-PAIRED — if most left blocks have a right
        block on the same baseline, the page is row-structured (form, TOC,
        data sheet), not two flowing columns.
    """
    best_at = _gutter_candidate(points)
    if best_at is None:
        return None
    left_pts = [p for p in points if p[0] < best_at]
    right_pts = [p for p in points if p[0] >= best_at]

    # --- structure test: row-paired content is a form/TOC, never two columns ---
    paired = 0
    for lp in left_pts:
        if any(abs(rp[1] - lp[1]) <= _ROW_TOL for rp in right_pts):
            paired += 1
    if paired > len(left_pts) / 2:
        return None
    return best_at


def _top_to_bottom(leaves) -> list:
    """Leaves in visual order. PDF user space origins at the bottom-left, so
    larger y is HIGHER on the page — descending y reads top to bottom."""
    return sorted(leaves, key=lambda lf: (-lf["by"], lf["bx"]))


def _reorder_leaves_for_columns(leaves) -> Tuple[list, bool]:
    """Reorder structure leaves into visual reading order for 2-column pages.

    Returns ``(leaves, changed)``. Conservative — returns the input untouched
    unless the page is unambiguously two-column AND the resulting order really
    differs from the one already there. Pages containing a detected table or
    list are left alone: those constructs span columns and carry their own
    ordering.

    Scope note, learned the expensive way. A previous revision tried to do more
    here: estimate each block's painted width so a full-width title could be
    treated as a band separator, and recurse so three-column pages were ordered
    instead of declined. An adversarial review confirmed six independent
    output-corrupting defects in it, and the shape of every one was the same —
    a region the detector could NOT vouch for was ordered anyway, which
    manufactured the very WCAG 1.3.2 interleave this function exists to remove
    and then reported it as a fix. The width estimate summed a whole BT..ET
    block, so ordinary multi-line paragraphs measured wider than the page and
    became "spanners"; the recursion y-sorted any side too small for the
    detector's own confidence floor. Both were removed rather than patched.

    The rule that survives: only ever reorder a region this module can
    positively prove is one column, and otherwise leave the document alone.
    """
    positioned = [lf for lf in leaves if lf.get("by") is not None and lf.get("bx") is not None]
    if len(positioned) != len(leaves) or len(leaves) < _MIN_COLUMN_BLOCKS:
        return leaves, False
    if any(lf.get("table") is not None or lf.get("group") is not None for lf in leaves):
        return leaves, False

    points = [(lf["bx"], lf["by"], lf.get("text") or "") for lf in leaves]
    gutter = _detect_two_columns(points)
    if gutter is None:
        return leaves, False

    # THREE-or-more columns: splitting only at the widest gap would separate one
    # column and leave the rest interleaved — a partial reorder that is still
    # wrong but would be REPORTED as fixed. Detect a second real gutter inside
    # either side and decline the page entirely.
    for side in ([p for p in points if p[0] < gutter], [p for p in points if p[0] >= gutter]):
        if len(side) >= _MIN_COLUMN_BLOCKS and _detect_two_columns(side) is not None:
            return leaves, False

    out = (
        _top_to_bottom([lf for lf in leaves if lf["bx"] < gutter])
        + _top_to_bottom([lf for lf in leaves if lf["bx"] >= gutter])
    )
    # Report a fix only when the order genuinely moved. Deriving `changed` from
    # the RESULT rather than from the geometry that suggested it is what stops
    # the tagger crediting itself with a correction it did not make.
    changed = [id(lf) for lf in out] != [id(lf) for lf in leaves]
    return (out, True) if changed else (leaves, False)


_CONTENTS_RE = re.compile(r"^\s*(?:table\s+of\s+)?contents\s*$|^\s*index\s*$", re.IGNORECASE)
_PAGE_REF_RE = re.compile(r"^(?:\d{1,4}|[ivxlc]{1,7})$", re.IGNORECASE)


def _is_contents_heading(text: str) -> bool:
    return bool(_CONTENTS_RE.match(text or ""))


def _looks_like_toc(run, ctx: Optional[Dict[str, Any]]) -> bool:
    """A grid that is a table of CONTENTS, not a data table.

    Entry text on the left, a bare page number in the last column, page
    numbers never decreasing down the rows — and either a "Contents" heading
    on the page or the numbers set far out at the right margin. Tagging this
    as a /Table made a screen reader announce a data grid, and re-auditing our
    own output raised TABLE_MISSING_HEADERS: a new finding the input never
    had. Declined grids stay plain paragraphs (never counted as tables, and
    not counted as "declined tables" either — nothing was missed).
    """
    if not ctx:
        return False
    ncols = len(run[0])
    if ncols not in (2, 3) or len(run) < 3:
        return False
    last = [str(r[-1][3]).strip() for r in run]
    first = [str(r[0][3]).strip() for r in run]
    if not all(_PAGE_REF_RE.match(t) for t in last):
        return False
    if not all(any(c.isalpha() for c in t) for t in first):
        return False
    digits = [int(t) for t in last if t.isdigit()]
    if len(digits) == len(last):
        if any(b < a for a, b in zip(digits, digits[1:])):
            return False
    elif not ctx.get("has_contents"):
        return False  # roman numerals: only with an explicit Contents heading
    gap = float(run[0][-1][1]) - float(run[0][0][1])
    width = float(ctx.get("width") or 0.0)
    wide = width > 0 and gap >= 0.4 * width
    return bool(ctx.get("has_contents") or wide)


def _detect_table_groups(segments, exclude=frozenset(), toc_ctx: Optional[Dict[str, Any]] = None):
    """Reconstruct DATA tables from positioned text blocks (high-precision).

    A table is a run of >=3 consecutive rows (text blocks sharing a baseline,
    with a consistent row pitch) where every row has the SAME number of cells
    (>=2), the per-column x-origins line up, the cells look like short data
    values (not prose / form labels / fillers), and no figure interrupts the
    run. Anything that fails these is left untagged — we never risk a false
    table (forms, TOCs, two-column prose all stay plain text).

    Returns ``(cell_of, tables)`` where ``cell_of[segidx] = (table_id, row, col)``
    and ``tables[table_id] = {"nrows": n, "ncols": m}``.
    """
    figure_idxs = {i for i, (k, _o, _m) in enumerate(segments) if k == "figure"}
    blocks = []  # (segidx, x, y, text)
    for idx, (kind, ops, _meta) in enumerate(segments):
        if kind != "text" or idx in exclude:
            continue
        x, y = _block_origin(ops)
        if x is None or y is None:
            continue
        text = _block_text(ops).strip()
        if not text:
            continue
        blocks.append((idx, x, y, text))

    cell_of: Dict[int, tuple] = {}
    tables: Dict[int, Dict[str, int]] = {}
    if len(blocks) < 6:  # need at least a 3x2 grid
        return cell_of, tables

    # Group blocks into rows by baseline (y), top to bottom; sort each row L→R.
    blocks.sort(key=lambda b: (-b[2], b[1]))
    rows: List[List[tuple]] = []
    cur = [blocks[0]]
    for b in blocks[1:]:
        if abs(b[2] - cur[0][2]) <= _ROW_TOL:
            cur.append(b)
        else:
            cur.sort(key=lambda z: z[1])
            rows.append(cur)
            cur = [b]
    cur.sort(key=lambda z: z[1])
    rows.append(cur)

    def _row_y(r):
        return sum(b[2] for b in r) / len(r)

    def _emit(run, tid):
        ncols = len(run[0])
        if len(run) < 3 or ncols < 2 or any(len(r) != ncols for r in run):
            return False
        col_xs = [run[0][c][1] for c in range(ncols)]
        if not all(abs(r[c][1] - col_xs[c]) <= _COL_TOL for r in run for c in range(ncols)):
            return False
        # No figure may interrupt the run (would scramble reading order).
        seg_idxs = [blk[0] for r in run for blk in r]
        if any(lo < f < hi for f in figure_idxs for lo, hi in [(min(seg_idxs), max(seg_idxs))]):
            return False
        is_toc = _looks_like_toc(run, toc_ctx)
        if is_toc:
            # Its rows (entry | [leader] | page number) are kept so each can
            # be read as ONE paragraph instead of "entry" then a bare "3" —
            # also for leader-dot contents, which never look like data cells.
            toc_ctx.setdefault("rows", []).extend([[blk[0] for blk in r] for r in run])
        if not _looks_like_data_cells(run):
            return False
        if is_toc:
            # A grid that WOULD have been tagged a table: counted as declined.
            toc_ctx["declined"] = int(toc_ctx.get("declined") or 0) + 1
            return False
        for rr, r in enumerate(run):
            for cc, blk in enumerate(r):
                cell_of[blk[0]] = (tid, rr, cc)
        # ``has_header`` is an INFERENCE, not a fact read from the file — record
        # it so the tagger only asserts /TH + /Scope when it's plausible.
        tables[tid] = {"nrows": len(run), "ncols": ncols, "has_header": _row0_is_header(run)}
        return True

    # Find maximal runs of consecutive multi-cell rows, then split each run where
    # the vertical gap jumps (so two stacked tables don't merge into one).
    tid = 0
    i = 0
    while i < len(rows):
        if len(rows[i]) >= 2:
            j = i
            while j < len(rows) and len(rows[j]) >= 2:
                j += 1
            band = rows[i:j]
            # Split the band on large row-pitch jumps.
            gaps = [abs(_row_y(band[k]) - _row_y(band[k + 1])) for k in range(len(band) - 1)]
            sub_start = 0
            if gaps:
                med = sorted(gaps)[len(gaps) // 2] or 1.0
                for k, g in enumerate(gaps):
                    if g > 2.2 * med:
                        if _emit(band[sub_start : k + 1], tid):
                            tid += 1
                        sub_start = k + 1
            if _emit(band[sub_start:], tid):
                tid += 1
            i = j
        else:
            i += 1
    return cell_of, tables


_LIST_INDENT = 12.0  # x increase (pts) that marks a nested (deeper) list level


def _list_numbering_for(text: str) -> Optional[str]:
    """``/ListNumbering`` value for a list run, judged from its first item.

    Precision over recall: only digit ordinals ("1.", "2)") map to /Decimal —
    single letters are ambiguous between alpha and roman numbering, and
    bullets don't require the attribute (Matterhorn 16-003 targets NUMBERED
    lists), so both are left unset rather than risk a wrong value."""
    t = (text or "").strip().lstrip("(")
    if t and t[0].isdigit():
        return "/Decimal"
    return None


def _build_nested_list(items: List[tuple], list_numbering: Optional[str] = None) -> Dict[str, Any]:
    """Build a /L spec from list items, nesting by x-indentation.

    ``items`` is ``[(mcid, x), ...]`` in reading order. A item indented further
    right than the current level starts a nested ``/L`` inside the previous
    ``/LI``; a item further left pops back out. Indentation we can't read (x is
    None) keeps everything at the current level (flat). The result is always a
    valid /L -> /LI -> (/LBody [, nested /L]) tree."""
    root: Dict[str, Any] = {"s": "/L", "kids": []}
    if list_numbering:
        root["ln"] = list_numbering
    # stack of (level_x, list_spec); deeper items nest under the last LI.
    base_x = next((x for _m, x in items if x is not None), 0.0)
    stack: List[tuple] = [(base_x, root)]
    for mcid, x in items:
        xx = base_x if x is None else x
        while len(stack) > 1 and xx < stack[-1][0] - _LIST_INDENT:
            stack.pop()
        level_x, cur = stack[-1]
        if xx > level_x + _LIST_INDENT and cur["kids"]:
            parent_li = cur["kids"][-1]
            sub = {"s": "/L", "kids": []}
            parent_li["kids"].append(sub)
            stack.append((xx, sub))
            cur = sub
        cur["kids"].append({"s": "/LI", "kids": [{"s": "/LBody", "mcid": mcid}]})
    return root


# ----- Ruling-line table detection ----------------------------------------
#
# Bordered tables draw their cell structure as stroked rectangles or m/l/S
# line sequences. Detecting these lines and reconstructing the grid gives us
# what pure text-geometry can't:
# 1. RECALL — tables whose cells contain prose, labels, or a single column;
#    the text heuristic intentionally rejects these to avoid form FPs, but
#    actual ruling lines remove the ambiguity.
# 2. PRECISION — a candidate enclosed by a real grid IS a table.
#
# We never DELETE a text-geometry table (those have already been guarded
# against FPs); ruling-line detection only ADDS cells the text pass missed.

# Minimum length (pt) for an axis-aligned segment to count as a ruling line.
_MIN_RULE_LEN = 15.0
# Coordinate tolerances (pt).
_AXIS_TOL = 0.5
_CLUSTER_TOL = 3.0
# A rectangle whose short side is <= this is a "thin filled bar" = ruling line.
_LINE_THICKNESS = 2.5
# Maximum vertical distance between successive horizontals to count as one band.
_BAND_MAX_GAP = 200.0
# A bordered grid must have at least half its cells filled with text to be a
# table (rejects isolated bordered boxes / multi-card layouts that happen to
# share alignment).
_GRID_MIN_FILL = 0.5

_IDENTITY_CTM = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _ctm_apply(ctm, x, y):
    a, b, c, d, e, f = ctm
    return (a * x + c * y + e, b * x + d * y + f)


def _ctm_concat(m, parent):
    """Compose ``m`` onto ``parent`` (PDF cm: new = m * current)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = parent
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _collect_ruling_lines(ops):
    """Walk page ops with CTM tracking; return (horizontal, vertical) lines.

    Each line is ``(x0, y0, x1, y1)`` in device-space coords (CTM applied) with
    coordinates ordered so horizontals have ``y0==y1, x0<=x1`` and verticals
    have ``x0==x1, y0<=y1``. Non-axis-aligned segments (after CTM) are ignored
    — rotated lines aren't ruling for any layout engine we care about.

    Captures BOTH stroked path segments (m..l..S) and rectangles (re). A thin
    filled rectangle is treated as a single ruling line on its long axis (some
    PDF generators draw rules that way).
    """
    ctm = _IDENTITY_CTM
    stack: List[tuple] = []
    horiz: List[tuple] = []
    vert: List[tuple] = []
    path_start = None
    cur = None
    pending: List[tuple] = []  # (p0, p1) candidate segments until paint op

    def _emit(p0, p1):
        x0, y0 = _ctm_apply(ctm, *p0)
        x1, y1 = _ctm_apply(ctm, *p1)
        if abs(y0 - y1) <= _AXIS_TOL and abs(x0 - x1) >= _MIN_RULE_LEN:
            horiz.append((min(x0, x1), y0, max(x0, x1), y0))
        elif abs(x0 - x1) <= _AXIS_TOL and abs(y0 - y1) >= _MIN_RULE_LEN:
            vert.append((x0, min(y0, y1), x0, max(y0, y1)))

    for operands, op in ops:
        if op == b"q":
            stack.append(ctm)
        elif op == b"Q":
            if stack:
                ctm = stack.pop()
        elif op == b"cm" and len(operands) >= 6:
            try:
                m = tuple(float(o) for o in operands[:6])
                ctm = _ctm_concat(m, ctm)
            except Exception:
                pass
        elif op == b"m" and len(operands) >= 2:
            try:
                x = float(operands[0]); y = float(operands[1])
                path_start = (x, y); cur = (x, y); pending = []
            except Exception:
                pass
        elif op == b"l" and len(operands) >= 2 and cur is not None:
            try:
                x = float(operands[0]); y = float(operands[1])
                pending.append((cur, (x, y))); cur = (x, y)
            except Exception:
                pass
        elif op == b"h" and path_start is not None and cur is not None:
            pending.append((cur, path_start))
            cur = path_start
        elif op == b"re" and len(operands) >= 4:
            try:
                x = float(operands[0]); y = float(operands[1])
                w = float(operands[2]); h = float(operands[3])
            except Exception:
                continue
            short = min(abs(w), abs(h))
            if short <= _LINE_THICKNESS:
                # Thin filled bar: emit single ruling line on its long axis.
                if abs(w) > abs(h):
                    y_mid = y + h / 2.0
                    pending.append(((x, y_mid), (x + w, y_mid)))
                else:
                    x_mid = x + w / 2.0
                    pending.append(((x_mid, y), (x_mid, y + h)))
            else:
                # Real rectangle (cell border or filled cell): emit 4 edges. A
                # filled-only background contributes the same edges as a stroke,
                # which is what we want (filled cells form a coherent grid too).
                pending.append(((x, y), (x + w, y)))
                pending.append(((x + w, y), (x + w, y + h)))
                pending.append(((x + w, y + h), (x, y + h)))
                pending.append(((x, y + h), (x, y)))
        elif op in (b"S", b"s", b"B", b"B*", b"b", b"b*", b"f", b"F", b"f*", b"n"):
            for p0, p1 in pending:
                _emit(p0, p1)
            pending = []
            path_start = None
            cur = None
    return horiz, vert


def _cluster_1d(values, tol):
    """Cluster sorted 1D values into bins within ``tol``; return bin centers."""
    if not values:
        return []
    vs = sorted(values)
    bins = [[vs[0]]]
    for v in vs[1:]:
        if v - bins[-1][-1] <= tol:
            bins[-1].append(v)
        else:
            bins.append([v])
    return [sum(b) / len(b) for b in bins]


def _detect_ruling_grids(horiz, vert):
    """Cluster axis-aligned lines into rectangular grids.

    For each y-band of horizontals (consecutive ys within ``_BAND_MAX_GAP``),
    find verticals that span the band's y-range and lie within its x-range.
    A grid needs >=2 distinct row-lines AND >=2 distinct column-lines (so the
    grid has >=2 rows AND >=2 columns of cells is checked by the caller).

    Returns ``[{xs, ys, x0, x1, y0, y1}]`` — xs sorted L→R, ys sorted top→bottom.
    """
    if not horiz or not vert:
        return []

    h_sorted = sorted(horiz, key=lambda h: -h[1])  # top first (PDF y up)
    bands: List[List[tuple]] = [[h_sorted[0]]]
    for h in h_sorted[1:]:
        if bands[-1][-1][1] - h[1] <= _BAND_MAX_GAP:
            bands[-1].append(h)
        else:
            bands.append([h])

    grids = []
    for band in bands:
        if len(band) < 2:
            continue
        ys = _cluster_1d([h[1] for h in band], _CLUSTER_TOL)
        if len(ys) < 2:
            continue
        y_top, y_bot = max(ys), min(ys)
        x_min = min(h[0] for h in band)
        x_max = max(h[2] for h in band)
        # Verticals only need to OVERLAP the band's y-range (not span it) — many
        # tables are drawn as one rect per cell, so each vertical edge is only
        # cell-tall, not table-tall. Demand x within the horizontal x-range too.
        relevant = [
            v for v in vert
            if v[3] >= y_bot - _CLUSTER_TOL  # extends above the band bottom
            and v[1] <= y_top + _CLUSTER_TOL  # starts below the band top
            and v[0] >= x_min - _CLUSTER_TOL
            and v[0] <= x_max + _CLUSTER_TOL
        ]
        xs = _cluster_1d([v[0] for v in relevant], _CLUSTER_TOL)
        if len(xs) < 2:
            continue
        grids.append({
            "xs": sorted(xs),
            "ys": sorted(ys, reverse=True),
            "x0": x_min, "x1": x_max,
            "y0": y_bot, "y1": y_top,
            # Keep the actual drawn segments: a MISSING rule between two grid
            # positions is ground truth that the cell there spans (merged), so
            # spans are read off the page rather than guessed. See _cell_spans.
            "v_lines": relevant,
            "h_lines": list(band),
        })
    return grids


_RULE_COVERAGE = 0.6  # fraction of the span a rule must cover to count as present


def _interval_union_len(intervals) -> float:
    """Total length covered by 1-D ``(lo, hi)`` intervals, overlaps counted once."""
    total = 0.0
    cur_lo = cur_hi = None
    for lo, hi in sorted(intervals):
        if cur_hi is None or lo > cur_hi:
            if cur_hi is not None:
                total += cur_hi - cur_lo
            cur_lo, cur_hi = lo, hi
        elif hi > cur_hi:
            cur_hi = hi
    if cur_hi is not None:
        total += cur_hi - cur_lo
    return total


def _has_vertical_rule(grid, x: float, y_lo: float, y_hi: float) -> bool:
    """True if drawn vertical rules at ``x`` cover most of ``[y_lo, y_hi]``.

    Measures the UNION of every collinear segment rather than the longest
    single one. Tables are routinely stroked cell by cell, so the boundary
    between two columns exists on the page as N short strokes, not one long
    line; measuring only the best of them reported the rule ABSENT. An absent
    rule is read as a merge (see :func:`_cell_spans`), so an ordinary
    per-cell-ruled table produced phantom ``/ColSpan``s.

    Fails CLOSED — a degenerate band returns True (rule present, no merge),
    because claiming a merge is the answer that can destroy content.
    """
    span = y_hi - y_lo
    if span <= 0:
        return True
    ivs = [
        (max(v[1], y_lo), min(v[3], y_hi))
        for v in (grid.get("v_lines") or [])
        if abs(v[0] - x) <= _CLUSTER_TOL and min(v[3], y_hi) > max(v[1], y_lo)
    ]
    return _interval_union_len(ivs) >= span * _RULE_COVERAGE


def _has_horizontal_rule(grid, y: float, x_lo: float, x_hi: float) -> bool:
    """True if drawn horizontal rules at ``y`` cover most of ``[x_lo, x_hi]``.

    Union-measured and fail-closed for the same reasons as
    :func:`_has_vertical_rule`.
    """
    span = x_hi - x_lo
    if span <= 0:
        return True
    ivs = [
        (max(h[0], x_lo), min(h[2], x_hi))
        for h in (grid.get("h_lines") or [])
        if abs(h[1] - y) <= _CLUSTER_TOL and min(h[2], x_hi) > max(h[0], x_lo)
    ]
    return _interval_union_len(ivs) >= span * _RULE_COVERAGE


def _cell_spans(grid, r: int, c: int, nrows: int, ncols: int) -> Tuple[int, int]:
    """``(colspan, rowspan)`` for the cell at ``(r, c)``, read from the rules.

    A merged cell is one whose separating rule was never drawn: if there is no
    vertical line between column ``c`` and ``c+1`` across this row's band, the
    cell genuinely continues into the next column. That is evidence on the page,
    not an inference — which is why this is safe to assert as ``/ColSpan`` in the
    structure tree (PDF/UA 7.5, Matterhorn 15-003).
    """
    xs, ys = grid["xs"], grid["ys"]
    y_hi, y_lo = ys[r], ys[r + 1]

    colspan = 1
    while c + colspan < ncols and not _has_vertical_rule(grid, xs[c + colspan], y_lo, y_hi):
        colspan += 1

    x_lo, x_hi = xs[c], xs[min(c + colspan, ncols)]
    rowspan = 1
    while r + rowspan < nrows and not _has_horizontal_rule(grid, ys[r + rowspan], x_lo, x_hi):
        rowspan += 1
    return colspan, rowspan


def _spans_full_width(spans, tid: int, row: int, ncols: int) -> bool:
    """True if ``row``'s leading cell stretches across the whole table."""
    return int(spans.get((tid, row, 0), (1, 1))[0]) >= ncols


def _header_row_for_grid(run_view, spans, tid: int, nrows: int, ncols: int) -> Optional[int]:
    """Which row may be typed ``/TH``, or None to claim no header at all.

    Row 0 by convention, when it reads like labels (see :func:`_row0_is_header`).

    But a row 0 that SPANS the whole table is a title band, not a header: typing
    it ``/TH`` with ``/Scope=/Column`` asserts it labels one column when it
    labels the table, and it pushes the genuine header row beneath it down to
    ``/TD`` — so the table ends up with a fabricated header and no real one.

    The row below a title band may take the role, but only on POSITIVE
    evidence. The labels-on-top convention is what carries the claim for row 0;
    it does not carry an arbitrary interior row. So we additionally require the
    rows underneath to actually look like data (at least one numeric cell).
    Without that, a title over two rows of ordinary text would have its first
    text row promoted to a header on no evidence whatsoever. We would rather
    claim no header than invent one.
    """
    if not _spans_full_width(spans, tid, 0, ncols):
        return 0 if _row0_is_header(run_view) else None
    if nrows < 3 or _spans_full_width(spans, tid, 1, ncols):
        return None
    if not _row0_is_header(run_view[1:]):
        return None
    below = run_view[2:]
    if not any(_cell_is_numericish(str(cell[3])) for row in below for cell in row):
        return None
    return 1


def _tables_from_grids(grids, segments, cell_of_existing, stats=None):
    """Assign text blocks to cells defined by ruling-line grids.

    Skips blocks already claimed by text-geometry tables (no double-tagging).
    A grid that fills <50% of its cells with text is dropped — protects
    against isolated bordered boxes / multi-card layouts that happen to share
    alignment.

    Returns ``(cell_of_extra, tables_extra)`` keyed by NEW tids that don't
    collide with text-geometry tids.
    """
    cell_of: Dict[int, tuple] = {}
    tables: Dict[int, Dict[str, int]] = {}
    if not grids:
        return cell_of, tables

    # Snapshot block positions once; expensive otherwise. Blocks the
    # text-geometry detector already claimed are kept SEPARATELY (not tagged
    # again) so a ruling grid that merely coincides with an already-tagged
    # table can be recognized as such rather than counted as "declined".
    blocks = []
    already_tagged = []  # (x, y) of blocks that belong to a geometry table
    for idx, (kind, ops, _meta) in enumerate(segments):
        if kind != "text":
            continue
        x, y = _block_origin(ops)
        if x is None or y is None:
            continue
        if idx in cell_of_existing:
            already_tagged.append((x, y))
            continue
        btext = _block_text(ops).strip()
        if not btext:
            continue
        blocks.append((idx, x, y, btext))

    # Pick first ruling tid AFTER any text-geometry tids.
    next_tid = (max((t for t, _r, _c in cell_of_existing.values()), default=-1)) + 1

    for grid in grids:
        xs = grid["xs"]; ys = grid["ys"]
        nrows = len(ys) - 1
        ncols = len(xs) - 1
        if nrows < 2 or ncols < 2:
            continue  # not a real table grid — too small to even be a candidate

        claimed: Dict[tuple, int] = {}  # (row,col) -> seg idx (first wins)
        cell_text: Dict[tuple, str] = {}
        for (idx, bx, by, btext) in blocks:
            if bx < grid["x0"] - _CLUSTER_TOL or bx > grid["x1"] + _CLUSTER_TOL:
                continue
            if by < grid["y0"] - _CLUSTER_TOL or by > grid["y1"] + _CLUSTER_TOL:
                continue
            # Row: ys descend (top first); row r spans (ys[r], ys[r+1]).
            row = None
            for r in range(nrows):
                if ys[r + 1] - _CLUSTER_TOL <= by <= ys[r] + _CLUSTER_TOL:
                    row = r
                    break
            if row is None:
                continue
            col = None
            for c in range(ncols):
                if xs[c] - _CLUSTER_TOL <= bx <= xs[c + 1] + _CLUSTER_TOL:
                    col = c
                    break
            if col is None:
                continue
            if (row, col) not in claimed:
                claimed[(row, col)] = idx
                cell_text[(row, col)] = btext

        fill_ratio = len(claimed) / max(1, nrows * ncols)
        if fill_ratio < _GRID_MIN_FILL:
            # Is this grid just the RULING around a table the text-geometry
            # detector already tagged? Then nothing was declined — the table
            # is in the output — and counting it here made the report say
            # "N table-like grids too sparse to tag, check by hand" for the
            # very tables it had tagged, on every tabular document. Only a
            # grid with claimable text of its own that still came up sparse is
            # a genuine decline.
            coincides = any(
                grid["x0"] - _CLUSTER_TOL <= x <= grid["x1"] + _CLUSTER_TOL
                and grid["y0"] - _CLUSTER_TOL <= y <= grid["y1"] + _CLUSTER_TOL
                for (x, y) in already_tagged
            )
            if coincides:
                continue  # duplicate of a tagged table; not a decline
            # A table-SHAPED grid we deliberately declined (too sparse to be
            # sure it's data). Counted so the report can say "found N, tagged
            # M" instead of implying every table was handled.
            if stats is not None:
                stats["declined"] = stats.get("declined", 0) + 1
            continue  # mostly empty grid -> probably card layout, not a table

        tid = next_tid
        next_tid += 1
        # Merged cells: read each cell's span off the drawn rules, and mark the
        # positions it covers so they don't emit phantom empty cells.
        spans: Dict[tuple, tuple] = {}
        covered: set = set()
        def _absorbs_text(r0: int, c0: int, cs0: int, rs0: int) -> bool:
            """True if this span would swallow a position that has its OWN text."""
            for rr in range(r0, min(r0 + rs0, nrows)):
                for cc in range(c0, min(c0 + cs0, ncols)):
                    if (rr, cc) != (r0, c0) and (rr, cc) in claimed:
                        return True
            return False

        for r in range(nrows):
            for c in range(ncols):
                if (r, c) in covered:
                    continue
                cs, rs = _cell_spans(grid, r, c, nrows, ncols)
                # HARD INVARIANT — a span may NEVER absorb a position that holds
                # its own text. Span detection infers a merge from a MISSING
                # rule, and that inference is only as good as the grid: a column
                # boundary drawn in one row defines that boundary for every row,
                # and rules shorter than _MIN_RULE_LEN are discarded entirely.
                # An adversarial review showed an ordinary per-cell-ruled table
                # losing 8 of 12 cells this way — real text made unreachable by
                # assistive tech. Shrinking here makes content loss impossible
                # by construction, however wrong the geometry heuristic gets.
                while cs > 1 and _absorbs_text(r, c, cs, rs):
                    cs -= 1
                while rs > 1 and _absorbs_text(r, c, cs, rs):
                    rs -= 1
                if cs > 1 or rs > 1:
                    spans[(tid, r, c)] = (cs, rs)
                    for rr in range(r, min(r + rs, nrows)):
                        for cc in range(c, min(c + cs, ncols)):
                            if (rr, cc) != (r, c):
                                covered.add((rr, cc))
        for (r, c), idx in claimed.items():
            cell_of[idx] = (tid, r, c)
        # Same header INFERENCE as the geometry path — rebuild a run-shaped view
        # (only text matters to the test) so a ruling grid with no header row is
        # not given a fabricated /TH.
        run_view = [
            [(0, 0, 0, cell_text.get((r, c), "")) for c in range(ncols)]
            for r in range(nrows)
        ]
        header_row = _header_row_for_grid(run_view, spans, tid, nrows, ncols)
        tables[tid] = {
            "nrows": nrows,
            "ncols": ncols,
            "has_header": header_row is not None,
            "header_row": header_row or 0,
            "spans": spans,      # (tid,r,c) -> (colspan, rowspan)
            "covered": covered,  # (r,c) positions absorbed by a merged cell
        }
    return cell_of, tables


# Running heads/footers/page numbers live in the top/bottom band of the page.
_ARTIFACT_BAND = 0.11  # fraction of page height at top and bottom


def _page_height(page) -> Optional[float]:
    try:
        return float(page.mediabox.height)
    except Exception:
        return None


def _artifact_sig(x: float, y: float) -> tuple:
    """Position signature (quantized) used to match a block across pages."""
    return (round(x / 6.0), round(y / 6.0))


def _in_artifact_band(y: float, height: Optional[float]) -> bool:
    if not height:
        return False
    return y > height * (1 - _ARTIFACT_BAND) or y < height * _ARTIFACT_BAND


_PAGENUM_RE = re.compile(
    r"^(?:[-–—‒]\s*)?(?:page\s+|p\.?\s*|pg\.?\s*)?\d{1,4}"
    r"(?:\s*(?:of|/)\s*\d{1,4})?(?:\s*[-–—‒])?$",
    re.IGNORECASE,
)
_ROMAN_RE = re.compile(r"^[ivxlcdm]{1,7}$", re.IGNORECASE)


def _is_page_number(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and bool(_PAGENUM_RE.match(t) or _ROMAN_RE.match(t))


def _page_band_blocks(pdf: PdfWriter, page, font_cache: Optional[Dict[Any, Any]] = None) -> List[tuple]:
    """``(pos_sig, text)`` for each text block in this page's header/footer band."""
    height = _page_height(page)
    if not height:
        return []
    try:
        ops = ContentStream(page.get_contents(), pdf).operations
    except Exception:
        return []
    out: List[tuple] = []
    for block in _iter_text_blocks(ops, _page_decoder(pdf, page, font_cache)):
        x, y = _block_origin(block)
        if x is not None and y is not None and _in_artifact_band(y, height):
            t = _block_text(block).strip()
            if t:
                out.append((_artifact_sig(x, y), t))
    return out


def _collect_artifact_sigs(pdf: PdfWriter, pages, font_cache: Optional[Dict[Any, Any]] = None) -> tuple:
    """Identify pagination artifacts that recur across >=2 pages.

    A running head/footer is the SAME text at the SAME band position page to
    page; a page number keeps its position but changes the digits. Matching on
    position ALONE wrongly eats ordinary body lines / per-page headings that sit
    at the standard top/bottom margin (different text, same position) — so we
    require the TEXT to repeat too, with a narrow page-number carve-out.

    Returns ``(text_keys, pagenum_positions)``:
    * ``text_keys`` — set of ``(pos_sig, casefolded_text)`` seen on >=2 pages;
    * ``pagenum_positions`` — set of ``pos_sig`` whose band text is a page-number
      token on >=2 pages (text varies, position is stable).
    """
    if len(pages) < 2:
        return (frozenset(), frozenset())
    pos_texts: Dict[tuple, List[str]] = {}
    for page in pages:
        for sig, text in _page_band_blocks(pdf, page, font_cache):
            pos_texts.setdefault(sig, []).append(text)

    text_keys = set()
    pagenum_positions = set()
    for sig, texts in pos_texts.items():
        if len(texts) < 2:
            continue
        norm = [t.casefold() for t in texts]
        common, count = Counter(norm).most_common(1)[0]
        if count >= 2:  # the same running head/footer text recurs
            text_keys.add((sig, common))
        if sum(1 for t in texts if _is_page_number(t)) >= 2:  # a page-number slot
            pagenum_positions.add(sig)
    return (frozenset(text_keys), frozenset(pagenum_positions))


def _collect_doc_heading_levels(
    pdf: PdfWriter, pages, artifact_info: tuple, font_cache: Optional[Dict[Any, Any]] = None
) -> Dict[float, int]:
    """Compute ONE document-wide font-size → heading-level map.

    Per-page maps produce inconsistent levels: a 20pt title on page 1 makes a
    14pt section heading H2 there, but on pages 2-4 (no 20pt block) the same
    14pt headings became H1 — wrong navigation order for screen readers.
    Collect sizes across all pages (excluding pagination artifacts so a
    repeating 10pt running head can't masquerade as the body size).
    """
    return _collect_doc_structure_hints(pdf, pages, artifact_info, font_cache)[0]


# A bold line that is a caption/admonition label, not a section heading.
_BOLD_EXCLUDE_RE = re.compile(r"^(?:figure|fig\.|table|note|notes|warning|caution|source|sources|important)\b", re.IGNORECASE)


def _collect_doc_structure_hints(
    pdf: PdfWriter, pages, artifact_info: tuple, font_cache: Optional[Dict[Any, Any]] = None
) -> Tuple[Dict[float, int], set, Optional[int], frozenset]:
    """``(size->level map, bold heading keys, bold heading level)``.

    The size map is :func:`_heading_levels` over every non-artifact block.

    Bold headings at BODY size — the memo/letter style ("MEMORANDUM",
    "Purpose", "Background" set in Helvetica-Bold 11pt over 11pt body) — are
    invisible to a size ranking, so such documents got no headings at all.
    Precision first; a block qualifies only when ALL of:

    * every glyph it paints is in a bold face, at the body size (not already
      a size-ranked heading);
    * it is short (<= 80 chars, <= 12 words), readable, starts with a letter
      or digit, does not end like a sentence or a label (. , ; :), is not a
      list item, a page number, or a Figure/Table/Note/Warning label;
    * the NEXT text block in the stream is not bold (a bold paragraph is
      emphasis, not a heading);
    * at least two such blocks exist in the document (one bold line proves
      nothing).

    Keys are ``(id(page), block ordinal)``; the level is one below the
    deepest size-ranked heading (H1 when there are none). Table cells and
    list items are excluded again at tagging time.
    """
    text_keys, pagenum_positions = artifact_info
    sizes: List[float] = []
    weights: List[int] = []
    per_page: List[Tuple[int, List[Tuple["_TextBlock", float, bool, bool]]]] = []
    loaded: List[Tuple[Any, float, Any, List["_TextBlock"]]] = []
    for page in pages:
        height = _page_height(page)
        try:
            ops = ContentStream(page.get_contents(), pdf).operations
        except Exception:
            continue
        decoder = _page_decoder(pdf, page, font_cache)
        loaded.append((page, height, decoder, list(_iter_text_blocks(ops, decoder))))
    # Page axes with the watermark texts left out of the vote; a watermark
    # is the same text on two or more pages, off-axis on at least one.
    axes, watermarks = _page_axes([bs for _p, _h, _d, bs in loaded])
    for (page, height, decoder, page_blocks), axis in zip(loaded, axes):
        rows: List[Tuple[Any, float, bool, bool]] = []
        for block in page_blocks:
            size = _block_font_size(block)
            is_artifact = False
            if _off_axis(block, axis):
                # A diagonal "DRAFT" is the biggest text on the page: it must
                # not rank as H1 or decide what "body" is. Treated like an
                # artifact for the size/bold analysis; whether it IS one (a
                # watermark) is decided by recurrence (``watermarks``).
                rows.append((block, float(size or 0.0), True, False))
                continue
            if size and size > 0:
                bx, by = _block_origin(block)
                if bx is not None and by is not None and _in_artifact_band(by, height):
                    sig = _artifact_sig(bx, by)
                    t = _block_text(block).strip()
                    if (sig, t.casefold()) in text_keys or (
                        sig in pagenum_positions and _is_page_number(t)
                    ):
                        is_artifact = True
                if not is_artifact:
                    sizes.append(size)
                    weights.append(_block_weight(block))
            bold = bool(block.fonts) and decoder is not None and all(decoder.is_bold(f) for f in block.fonts)
            rows.append((block, float(size or 0.0), is_artifact, bold))
        per_page.append((id(page), rows))
    levels = _heading_levels(sizes, weights)
    # ``watermarks`` (the SAME off-axis text on two or more pages: "DRAFT",
    # "CONFIDENTIAL" set diagonally on every page) are marked /Artifact like a
    # running header — out of the reading order, where PDF/UA puts it.

    body = _body_size(sizes, weights)
    if body is None:
        return levels, set(), None, watermarks
    candidates: set = set()
    for pid, rows in per_page:
        # Baselines of the page's real (non-artifact, non-empty) text blocks.
        baselines: List[Tuple[int, float]] = []
        for j, (blk, _sz, art, _b) in enumerate(rows):
            if art or not _block_text(blk).strip():
                continue
            _ox, oy = _block_origin(blk)
            if oy is not None:
                baselines.append((j, oy))
        for i, (block, size, is_artifact, bold) in enumerate(rows):
            if not bold or is_artifact or getattr(block, "undecodable", False):
                continue
            if abs(round(size, 1) - body) > 0.5 or levels.get(round(size, 1)):
                continue
            # A section heading stands ALONE on its line. A bold block that
            # shares its baseline with other text is a cell of a grid we did
            # not recognise as a table (the last bold header cell is followed
            # by a plain data cell), a run-in lead, or part of a split line —
            # never evidence of a heading.
            _bx, by = _block_origin(block)
            if by is None or any(j != i and abs(oy - by) <= 2.0 for j, oy in baselines):
                continue
            t = re.sub(r"\s+", " ", _block_text(block)).strip()
            if not t or len(t) > 80 or len(t.split()) > 12 or not t[0].isalnum():
                continue
            # ":" too: a bold "Name:" is a form label or a run-in lead, not a
            # section heading.
            if t[-1] in ".,;:" or _is_list_item(t) or _is_page_number(t) or _BOLD_EXCLUDE_RE.match(t):
                continue
            if any(ord(ch) < 32 for ch in t) or not any(ch.isalpha() for ch in t):
                continue
            nxt = next((r for r in rows[i + 1:] if not r[2] and _block_text(r[0]).strip()), None)
            if nxt is None or nxt[3]:
                continue  # last thing on the page, or followed by more bold
            candidates.add((pid, i))
    if len(candidates) < 2:
        return levels, set(), None, watermarks
    bold_level = min(6, (max(levels.values()) + 1) if levels else 1)
    return levels, candidates, bold_level, watermarks


def _body_size(sizes: List[float], weights: Optional[List[int]] = None) -> Optional[float]:
    """The size carrying the most characters (smaller wins a tie), or None."""
    volume: Counter = Counter()
    for i, s in enumerate(sizes):
        if s and s > 0:
            w = 1
            if weights is not None and i < len(weights):
                w = max(1, int(weights[i] or 0))
            volume[round(s, 1)] += w
    if not volume:
        return None
    top = max(volume.values())
    return min(s for s, v in volume.items() if v == top)


def _page_image_xobjects(page) -> Dict[str, str]:
    """``{name: subtype}`` for the page's XObject resources (``/Image`` or
    ``/Form``). Unresolvable entries are simply absent — callers treat an
    unknown ``Do`` as raw content and make no claim about it."""
    out: Dict[str, str] = {}
    try:
        node = page
        res = None
        for _ in range(32):
            if node is None:
                break
            res = _resolve(node.get("/Resources")) if "/Resources" in node else None
            if isinstance(res, DictionaryObject):
                break
            node = _resolve(node.get("/Parent"))
        if not isinstance(res, DictionaryObject):
            return out
        xo = _resolve(res.get("/XObject")) if "/XObject" in res else None
        if not isinstance(xo, DictionaryObject):
            return out
        for name, ref in xo.items():
            obj = _resolve(ref)
            if isinstance(obj, DictionaryObject):
                out[str(name).lstrip("/")] = str(obj.get("/Subtype") or "")
    except Exception:
        logger.debug("xobject listing failed", exc_info=True)
    return out


def _unit_square_bbox(ctm) -> Optional[Tuple[float, float, float, float]]:
    """User-space bbox of an image XObject painted under ``ctm`` (images map
    the unit square through the CTM)."""
    try:
        pts = [_ctm_apply(ctm, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))
    except Exception:
        return None


def _tag_page_elements(
    pdf: PdfWriter,
    page,
    alt_by_xobject: Dict[str, str],
    artifact_info: tuple = (frozenset(), frozenset()),
    counters: Optional[Dict[str, int]] = None,
    doc_heading_levels: Optional[Dict[float, int]] = None,
    decorative_xobjects: Optional[set] = None,
    font_cache: Optional[Dict[Any, Any]] = None,
    bold_keys: Optional[set] = None,
    bold_level: Optional[int] = None,
    watermarks: frozenset = frozenset(),
):
    """Per-element marked content for one page.

    Segments the content into text blocks (BT..ET) and known-alt images (Do),
    tags each text block ``/H1``..``/H6`` (by relative font size) or ``/P``, and
    each image ``/Figure`` with ``/Alt``. Repeated header/footer/page-number
    blocks (``artifact_sigs``) are wrapped as ``/Artifact`` and kept OUT of the
    structure tree. ``doc_heading_levels`` (document-wide size→level map) keeps
    heading ranks consistent across pages.

    Returns the page's specs in READING ORDER, or ``None`` to signal the caller
    to fall back to safe page-level wrapping. A spec's position in that list is
    NOT its MCID: nested specs (``/Table``, ``/L``) carry their MCIDs on inner
    cells, and a columned page has its specs reordered relative to the stream.
    Each spec's MCID is always read from its own ``"mcid"`` key — the content
    stream and every marked-content id in it are left exactly as emitted.
    """
    try:
        contents = page.get_contents()
        if contents is None:
            return None
        cs = ContentStream(contents, pdf)
        ops = cs.operations
    except Exception:
        return None
    if not ops:
        return None
    orig_text = _count_text_ops(ops)

    # Pass 1 — split into ordered segments, preserving non-block ("raw") ops.
    #   "text"     a BT..ET block (a decorated _TextBlock; meta = font size)
    #   "figure"   a drawn IMAGE XObject (meta = {"alt", "bbox"}). EVERY image
    #              is tagged: with /Alt when the analysis tree has one, and
    #              without it otherwise. An image left out of the tree is
    #              invisible to assistive tech and fails PDF/UA 7.1 (content
    #              must be tagged or an artifact) — we used to do exactly that
    #              for every image without alt. We never invent the missing
    #              description; the re-analysis still reports it.
    #   "artifact" a drawn image the tree marks decorative -> /Artifact.
    #   "raw"      everything else, re-emitted verbatim. Form XObjects stay
    #              raw: they can carry text, and a /Figure would hide it.
    from app.pdf.text_decode import FontState

    decoder = _page_decoder(pdf, page, font_cache)
    xobject_kinds = _page_image_xobjects(page)
    decorative_xobjects = decorative_xobjects or set()
    font_state = FontState()
    ctm = _IDENTITY_CTM
    ctm_stack: List[tuple] = []
    segments = []  # (kind, ops, meta)
    raw = []
    block = None
    block_start_font = None
    for operands, op in ops:
        if op == b"BT":
            if block is not None:
                return None  # nested BT (malformed text objects) — fall back
            if raw:
                segments.append(("raw", raw, None))
                raw = []
            block = _TextBlock([(operands, op)])
            block_start_font = font_state.font
        elif op == b"ET" and block is not None:
            block.append((operands, op))
            block.angle = _block_angle(block, ctm)
            ctm = _block_cm_after(block, ctm)
            _decorate_block(block, decoder, block_start_font)
            segments.append(("text", block, _block_font_size(block)))
            block = None
        elif block is not None:
            block.append((operands, op))
        elif op == b"Do":
            xname = str(operands[0]).lstrip("/") if operands else ""
            kind = xobject_kinds.get(xname)
            if kind == "/Image":
                if raw:
                    segments.append(("raw", raw, None))
                    raw = []
                if xname in decorative_xobjects and not alt_by_xobject.get(xname):
                    segments.append(("artifact", [(operands, op)], None))
                else:
                    segments.append((
                        "figure",
                        [(operands, op)],
                        {"alt": alt_by_xobject.get(xname) or None, "bbox": _unit_square_bbox(ctm)},
                    ))
            else:
                raw.append((operands, op))
        else:
            raw.append((operands, op))
        # Graphics state bookkeeping (after classification; Do reads the CTM
        # in force when it is painted).
        font_state.feed(operands, op)
        if block is None:
            if op == b"q":
                ctm_stack.append(ctm)
            elif op == b"Q":
                if ctm_stack:
                    ctm = ctm_stack.pop()
            elif op == b"cm" and len(operands) >= 6:
                try:
                    ctm = _ctm_concat(tuple(float(o) for o in operands[:6]), ctm)
                except Exception:
                    pass
    if block is not None:  # unterminated BT — don't risk it
        return None
    if raw:
        segments.append(("raw", raw, None))

    height = _page_height(page)

    # Page-local counter accumulator. Merged into the shared `counters` only
    # after the page commits its rewritten stream (see the end of Pass 2).
    pending: Dict[str, int] = {}
    n_undecodable = sum(1 for k, o, _m in segments if k == "text" and getattr(o, "undecodable", False))
    if n_undecodable:
        pending["undecodable_blocks"] = n_undecodable
    # Untagged painting (rules, boxes, vector art, inline images, Form
    # XObjects) is left exactly as drawn — we cannot tell a table rule from a
    # chart — but it means this page cannot be CLAIMED PDF/UA-conformant.
    if any(k == "raw" and _raw_paints(o, xobject_kinds) for k, o, _m in segments):
        pending["untagged_paint_pages"] = 1

    # Pass 1.4 — reconstruct tables from positioned text (cells get tagged
    # TH/TD and are excluded from list/heading/paragraph/artifact handling).
    try:
        _pw = float(page.mediabox.width)
    except Exception:
        _pw = 0.0
    toc_ctx: Dict[str, Any] = {
        "width": _pw,
        "has_contents": any(
            _is_contents_heading(_block_text(o)) for k, o, _m in segments if k == "text"
        ),
        "declined": 0,
    }
    cell_of, tables = _detect_table_groups(segments, toc_ctx=toc_ctx)
    if toc_ctx["declined"]:
        pending["toc_declined"] = toc_ctx["declined"]
    # segment index -> which TOC row it belongs to (entry, leader, page number)
    toc_row_of: Dict[int, int] = {
        sidx: rk for rk, row in enumerate(toc_ctx.get("rows") or []) for sidx in row
    }

    # Pass 1.4b — ruling-line tables (additive). Bordered grids whose cells
    # contain prose / labels / a single column — which the text heuristic
    # intentionally rejects — get caught here when actual ruling lines confirm
    # the structure. Existing text-geometry cells are NEVER overwritten.
    try:
        h_lines, v_lines = _collect_ruling_lines(ops)
        _grid_stats: Dict[str, int] = {}
        ring_cells, ring_tables = _tables_from_grids(
            _detect_ruling_grids(h_lines, v_lines), segments, cell_of, stats=_grid_stats
        )
        # Accumulated locally, merged into `counters` only once this page has
        # COMMITTED its new content stream. Every check below can still bail to
        # the page-level fallback, which discards all of this analysis — so
        # counting it here would describe work the output does not contain.
        if _grid_stats.get("declined"):
            pending["tables_declined"] = _grid_stats["declined"]
        cell_of.update(ring_cells)
        tables.update(ring_tables)
    except Exception:
        # Ruling-line detection is best-effort; never block tagging on it.
        logger.debug("ruling-line detection failed", exc_info=True)

    # Pass 1.5 — pagination artifacts: header/footer-band blocks whose TEXT (or
    # page-number role) repeats across pages. Wrapped /Artifact, kept out of the
    # tree. Table cells are NEVER artifacts (protects multi-page table headers).
    text_keys, pagenum_positions = artifact_info
    artifact_idxs: set = set()
    if text_keys or pagenum_positions:
        for idx, (kind, seg_ops, _m) in enumerate(segments):
            if kind != "text" or idx in cell_of:
                continue
            bx, by = _block_origin(seg_ops)
            if bx is None or by is None or not _in_artifact_band(by, height):
                continue
            sig = _artifact_sig(bx, by)
            t = _block_text(seg_ops).strip()
            if (sig, t.casefold()) in text_keys or (sig in pagenum_positions and _is_page_number(t)):
                artifact_idxs.add(idx)
    # Dot leaders ("..........") between a contents entry and its page number
    # are drawn layout, not words: a screen reader voicing sixty "dot"s is the
    # classic TOC failure. A block made ONLY of leader dots is an artifact.
    for idx, (kind, seg_ops, _m) in enumerate(segments):
        if kind == "text" and idx not in cell_of and idx not in artifact_idxs:
            if _is_leader_text(_block_text(seg_ops)):
                artifact_idxs.add(idx)
    # Off-axis text (at an angle to the page's own text) is never a heading;
    # when the same off-axis text recurs across pages it is a watermark and
    # goes out of the reading order as an artifact.
    # A known watermark does not vote on the page's axis (see _page_axes).
    page_axis = _dominant_angle([
        o for k, o, _m in segments
        if k == "text" and not (watermarks and _norm_block_text(o) in watermarks)
    ])
    off_axis_idxs: set = set()
    for idx, (kind, seg_ops, _m) in enumerate(segments):
        if kind != "text" or idx in cell_of or not _off_axis(seg_ops, page_axis):
            continue
        off_axis_idxs.add(idx)
        if idx not in artifact_idxs and watermarks:
            if _norm_block_text(seg_ops) in watermarks:
                artifact_idxs.add(idx)
                pending["watermarks"] = pending.get("watermarks", 0) + 1

    levels = (
        doc_heading_levels
        if doc_heading_levels is not None
        else _heading_levels(
            [m for i, (k, _o, m) in enumerate(segments) if k == "text" and m and i not in artifact_idxs and i not in off_axis_idxs],
            [_block_weight(o) for i, (k, o, m) in enumerate(segments) if k == "text" and m and i not in artifact_idxs and i not in off_axis_idxs],
        )
    )

    # Pass 1.5b — find runs of >=2 consecutive list-item text blocks. Raw ops
    # between items don't break a run; a figure, table cell, or non-list text
    # block does.
    group_of: Dict[int, int] = {}  # segment idx -> list-group id
    group_numbering: Dict[int, Optional[str]] = {}  # group id -> /ListNumbering
    gid = 0
    run: List[int] = []

    def _flush_run() -> None:
        nonlocal gid
        if len(run) >= 2:
            for ti in run:
                group_of[ti] = gid
            group_numbering[gid] = _list_numbering_for(_block_text(segments[run[0]][1]))
            gid += 1
        run.clear()

    for idx, (kind, seg_ops, _meta) in enumerate(segments):
        if kind in ("raw", "artifact") or idx in artifact_idxs:
            continue
        if (
            kind == "text"
            and idx not in cell_of
            and _is_list_item(_block_text(seg_ops))
        ):
            run.append(idx)
        else:
            _flush_run()
    _flush_run()

    # Pass 2 — emit marked content; record an ordered leaf per tagged segment.
    new_ops = []
    leaves = []  # ordered dicts: mcid, tag, alt, group, table=(tid,row,col)
    table_cell_mcid: Dict[tuple, int] = {}  # (tid,row,col) -> mcid
    n_artifacts = 0
    mcid = 0
    text_ordinal = -1
    bold_keys = bold_keys or set()
    page_key = id(page)
    for idx, (kind, seg_ops, meta) in enumerate(segments):
        if kind == "text":
            text_ordinal += 1
        if kind == "raw":
            new_ops.extend(seg_ops)
            continue
        if idx in artifact_idxs or kind == "artifact":
            # Pagination artifact / dot leader / decorative image: wrap
            # /Artifact BMC..EMC, NOT in the tree.
            new_ops.append(([NameObject("/Artifact")], b"BMC"))
            new_ops.extend(seg_ops)
            new_ops.append(([], b"EMC"))
            n_artifacts += 1
            if kind == "artifact":
                pending["decorative_images"] = pending.get("decorative_images", 0) + 1
            continue
        tcell = cell_of.get(idx) if kind == "text" else None
        grp = group_of.get(idx) if kind == "text" else None
        if kind == "text":
            if tcell is not None:
                # A row is a header only when the detector found a positive
                # signal (see _row0_is_header). Never fabricate one. The header
                # is usually row 0, but a full-width title band pushes it down.
                _dims = tables.get(tcell[0]) or {}
                _has_hdr = bool(_dims.get("has_header"))
                _hdr_row = int(_dims.get("header_row") or 0)
                tag = "/TH" if (_has_hdr and tcell[1] == _hdr_row) else "/TD"
            elif grp is not None:
                tag = "/LBody"
            elif getattr(seg_ops, "undecodable", False) or idx in off_axis_idxs:
                # Text we cannot read is never asserted to be a heading: a
                # screen reader cannot read it either, and a nameless /Hn is
                # its own defect. Nor is text set at an angle to the page (a
                # one-off diagonal stamp): it is the largest text on the page
                # and ranked H1 on every page.
                tag = "/P"
            else:
                lvl = levels.get(round(meta, 1)) if meta else None
                if not lvl and bold_level and (page_key, text_ordinal) in bold_keys:
                    lvl = bold_level
                    pending["bold_headings"] = pending.get("bold_headings", 0) + 1
                tag = f"/H{lvl}" if lvl else "/P"
            alt = None
            fbbox = None
        else:  # figure
            tag = "/Figure"
            alt = (meta or {}).get("alt")
            fbbox = (meta or {}).get("bbox")
        # Block origin: x indents nested list items, and (x, y) together drive
        # the column-aware reading-order correction below.
        bx, by = _block_origin(seg_ops)
        # x-indent is used to nest list items (deeper x => sub-list).
        lx = bx if (kind == "text" and grp is not None) else None
        new_ops.append(([NameObject(tag), DictionaryObject({NameObject("/MCID"): NumberObject(mcid)})], b"BDC"))
        new_ops.extend(seg_ops)
        new_ops.append(([], b"EMC"))
        btext = _block_text(seg_ops) if kind == "text" else ""
        leaves.append({
            "mcid": mcid, "tag": tag, "alt": alt, "group": grp, "table": tcell,
            "x": lx, "bx": bx, "by": by,
            # Needed by the column detector's prose test — a page of short
            # labels must never be linearized as two columns.
            "text": btext,
            "bbox": fbbox,
            "size": meta if kind == "text" else None,
            "toc_row": toc_row_of.get(idx) if kind == "text" else None,
        })
        if tcell is not None:
            table_cell_mcid[tcell] = mcid
        mcid += 1

    if not leaves:
        return None
    if _count_text_ops(new_ops) != orig_text:  # text must be preserved exactly
        return None
    # Every original op must survive exactly once; we add 2 ops (BDC/BMC+EMC) per
    # tagged segment AND per artifact. Anything else means an op was lost/dupd.
    if len(new_ops) != len(ops) + 2 * (len(leaves) + n_artifacts):
        return None
    pending["artifacts"] = pending.get("artifacts", 0) + n_artifacts

    cs.operations = new_ops
    try:
        new_data = cs.get_data()
    except Exception:
        return None
    ns = DecodedStreamObject()
    ns.set_data(new_data)
    # Flate-compress the rewritten stream. Real-world PDFs arrive with
    # FlateDecode content; emitting our rewritten copy uncompressed inflated
    # a 2.7 MB text-heavy document to 12.8 MB (up to 19x on dense pages) —
    # slower downloads, and 24h of artifact storage per job at 4-5x. The
    # bytes are byte-identical after decode; the op-count guard above already
    # ran against `new_data`, so nothing here can hide a content change.
    try:
        ns = ns.flate_encode()
    except Exception:  # pragma: no cover - keep the uncompressed stream rather than fail
        logger.debug("flate_encode failed; writing uncompressed content stream", exc_info=True)
    page[NameObject("/Contents")] = pdf._add_object(ns)  # noqa: SLF001

    # COMMIT POINT — the page's new stream is in the file, so everything the
    # analysis found above is now genuinely reflected in the output bytes and
    # may be counted. Before this line the page could still return None and be
    # replaced by the page-level fallback.
    if counters is not None:
        for k, v in pending.items():
            counters[k] = counters.get(k, 0) + v

    # Reading order: the content stream (and therefore the rendered page and
    # every MCID) is left EXACTLY as produced above — we only choose the order
    # in which the structure elements reference those MCIDs, which is what
    # PDF/UA reading order actually is. On a two-column page whose stream
    # interleaves the columns, this turns an alternating jumble into the order a
    # sighted reader sees (WCAG 1.3.2).
    # /Rotate turns the page, so "x" and "y" no longer mean horizontal and
    # vertical — column logic on a rotated page sorts on the wrong axes and
    # scrambles a correctly-ordered landscape document. Only reorder upright pages.
    try:
        _rotation = int(page.get("/Rotate", 0) or 0) % 360
    except Exception:
        _rotation = 0
    if _rotation == 0:
        leaves, reordered, declined = _column_order(leaves)
        if reordered and counters is not None:
            counters["reading_order_fixed"] = counters.get("reading_order_fixed", 0) + 1
        # The honest complement: this page LOOKS like two columns of prose
        # whose stream order interleaves them, and we did not reorder it (a
        # table/list/figure on the page, rows that pair up like a form, a
        # third column...). It is delivered in stream order and must be
        # disclosed, never counted as fixed.
        if declined and counters is not None:
            counters["reading_order_declined"] = counters.get("reading_order_declined", 0) + 1

    # Pass 3 — build nested specs. A table emits one /Table -> /TR -> /TH|/TD at
    # the position of its first cell; a list run emits /L -> /LI -> /LBody;
    # everything else stays a flat leaf (/P, /Hn, /Figure).
    specs: List[Dict[str, Any]] = []
    emitted_tables: set = set()
    i = 0
    while i < len(leaves):
        leaf = leaves[i]
        tcell = leaf["table"]
        grp = leaf["group"]
        if tcell is not None:
            tid = tcell[0]
            if tid not in emitted_tables:
                emitted_tables.add(tid)
                dims = tables[tid]
                has_hdr = bool(dims.get("has_header"))
                hdr_row = int(dims.get("header_row") or 0)
                cell_spans = dims.get("spans") or {}
                covered_cells = dims.get("covered") or set()
                trs = []
                for r in range(dims["nrows"]):
                    tcs = []
                    for c in range(dims["ncols"]):
                        # Absorbed by a merged neighbour — it has no cell of
                        # its own, so emitting one would invent a structure the
                        # page doesn't have.
                        if (r, c) in covered_cells:
                            continue
                        mc = table_cell_mcid.get((tid, r, c))
                        if mc is None:
                            continue
                        # Only assert a header cell when the detector inferred
                        # one; otherwise every cell is plain data (/TD).
                        cell = {"s": "/TH" if (has_hdr and r == hdr_row) else "/TD", "mcid": mc}
                        cs, rs = cell_spans.get((tid, r, c), (1, 1))
                        if cs > 1:
                            cell["colspan"] = cs
                        if rs > 1:
                            cell["rowspan"] = rs
                        tcs.append(cell)
                    if tcs:
                        trs.append({"s": "/TR", "kids": tcs})
                if trs:
                    ys = [lf["by"] for lf in leaves if lf["table"] is not None and lf["table"][0] == tid and lf.get("by") is not None]
                    tspec: Dict[str, Any] = {"s": "/Table", "kids": trs}
                    if ys:
                        tspec["yspan"] = (min(ys), max(ys))
                    specs.append(tspec)
            i += 1  # this cell is already inside the table spec
        elif grp is not None:
            items = []
            ys = []
            while i < len(leaves) and leaves[i]["group"] == grp and leaves[i]["table"] is None:
                items.append((leaves[i]["mcid"], leaves[i].get("x")))
                if leaves[i].get("by") is not None:
                    ys.append(leaves[i]["by"])
                i += 1
            lspec = _build_nested_list(items, group_numbering.get(grp))
            if ys:
                lspec["yspan"] = (min(ys), max(ys))
            specs.append(lspec)
        else:
            # A table-of-contents row ("1. Introduction" | leader | "3") reads
            # as ONE paragraph — "1. Introduction 3" — instead of the entry
            # and then a bare page number as two unrelated paragraphs. Only
            # when the row's pieces are consecutive, plain /P leaves.
            trow = leaf.get("toc_row")
            if trow is not None and leaf["tag"] == "/P":
                j = i
                while (
                    j < len(leaves)
                    and leaves[j].get("toc_row") == trow
                    and leaves[j]["tag"] == "/P"
                    and leaves[j]["table"] is None
                    and leaves[j]["group"] is None
                ):
                    j += 1
                if j - i >= 2:
                    joined = {"s": "/P", "mcids": [lf["mcid"] for lf in leaves[i:j]]}
                    if leaf.get("bx") is not None and leaf.get("by") is not None:
                        joined["pos"] = (leaf["bx"], leaf["by"])
                    specs.append(joined)
                    if counters is not None:
                        counters["toc_rows_joined"] = counters.get("toc_rows_joined", 0) + 1
                    i = j
                    continue
            node = {"s": leaf["tag"], "mcid": leaf["mcid"]}
            # Position of the block (not written to the file): lets link and
            # form-field elements be placed next to the text on their line.
            if leaf.get("bx") is not None and leaf.get("by") is not None:
                node["pos"] = (leaf["bx"], leaf["by"])
            if leaf["alt"]:
                node["alt"] = leaf["alt"]
            if leaf.get("bbox"):
                node["bbox"] = leaf["bbox"]
            specs.append(node)
            i += 1
    return specs


def _build_struct_elem(
    writer: PdfWriter,
    spec: Dict[str, Any],
    parent_ref: IndirectObject,
    page: Any,
    mcid_to_ref: Dict[int, IndirectObject],
    stats: Dict[str, int],
) -> IndirectObject:
    """Recursively build a StructElem from a (possibly nested) spec.

    A *container* spec (``{"s": "/L", "kids": [...]}``) gets ``/K`` = an array of
    child StructElem refs and carries no marked content. A *leaf* spec
    (``{"s": "/P", "mcid": n}``) gets ``/K`` = its MCID and ``/Pg`` = the page,
    and is registered in ``mcid_to_ref`` so the ParentTree can point back to it.
    """

    elem = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/StructElem"),
            NameObject("/S"): NameObject(spec["s"]),
            NameObject("/P"): parent_ref,
        }
    )
    ref = writer._add_object(elem)  # noqa: SLF001
    if spec["s"] in ("/TH", "/TD"):
        # Table cell attributes (PDF/UA 7.5, Matterhorn 15-003):
        #   /Scope   — only on a /TH, and only /Column. A /TH is emitted solely
        #              for the row _header_row_for_grid picked, and that
        #              function never picks a row whose leading cell spans the
        #              whole table — a cell that wide heads no single column, so
        #              /Column would be a fabrication. That guard lives there,
        #              in one place, rather than being re-checked here.
        #   /ColSpan, /RowSpan — merged cells. These come from MISSING drawn
        #              rules (see _cell_spans), i.e. evidence on the page, not
        #              an inference, so asserting them is safe.
        attrs = {NameObject("/O"): NameObject("/Table")}
        if spec["s"] == "/TH":
            attrs[NameObject("/Scope")] = NameObject("/Column")
        if int(spec.get("colspan") or 1) > 1:
            attrs[NameObject("/ColSpan")] = NumberObject(int(spec["colspan"]))
        if int(spec.get("rowspan") or 1) > 1:
            attrs[NameObject("/RowSpan")] = NumberObject(int(spec["rowspan"]))
        # A plain /TD with no span carries no attribute at all.
        if len(attrs) > 1:
            elem[NameObject("/A")] = DictionaryObject(attrs)
    elif spec.get("ln"):
        # Numbered list: /ListNumbering tells AT how the labels are generated
        # (Matterhorn 16-003).
        elem[NameObject("/A")] = DictionaryObject(
            {
                NameObject("/O"): NameObject("/List"),
                NameObject("/ListNumbering"): NameObject(spec["ln"]),
            }
        )
    elif spec["s"] == "/Figure" and spec.get("bbox"):
        # Where the figure is on the page (PDF 32000 14.8.5.4.3 layout BBox):
        # read off the CTM it was painted under, i.e. a fact, not a guess.
        try:
            x0, y0, x1, y1 = (float(v) for v in spec["bbox"])
            if x1 > x0 and y1 > y0:
                elem[NameObject("/A")] = DictionaryObject(
                    {
                        NameObject("/O"): NameObject("/Layout"),
                        NameObject("/BBox"): ArrayObject(
                            [FloatObject(round(v, 2)) for v in (x0, y0, x1, y1)]
                        ),
                    }
                )
        except Exception:
            pass
    # /Pg on EVERY element of this page, containers included (PDF 32000 allows
    # it on any StructElem). A /Table or /L used to carry none, so readers —
    # ours included — attributed a table on page 2 to page 1.
    elem[NameObject("/Pg")] = page.indirect_reference
    kids = spec.get("kids")
    if kids:
        kid_refs = ArrayObject()
        for kid in kids:
            kid_refs.append(
                _build_struct_elem(writer, kid, ref, page, mcid_to_ref, stats)
            )
        elem[NameObject("/K")] = kid_refs
    elif spec.get("mcids"):
        # One element over several marked-content sequences on this page (a
        # table-of-contents row: entry + page number), in reading order.
        elem[NameObject("/K")] = ArrayObject([NumberObject(m) for m in spec["mcids"]])
        for m in spec["mcids"]:
            mcid_to_ref[m] = ref
    else:
        elem[NameObject("/K")] = NumberObject(spec["mcid"])
        if spec.get("alt"):
            elem[NameObject("/Alt")] = TextStringObject(str(spec["alt"]))
            stats["figures"] += 1
        elif spec["s"] == "/Figure":
            # Tagged so it is in the reading order, but still undescribed —
            # counted separately so "figures" keeps meaning "with alt".
            stats["figures_no_alt"] = stats.get("figures_no_alt", 0) + 1
        mcid_to_ref[spec["mcid"]] = ref
    return ref


_PAINT_OPS = {b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"sh", b"INLINE IMAGE", b"BI"}


def _raw_paints(seg_ops, xobject_kinds: Dict[str, str]) -> bool:
    """True when a raw (untagged) segment PAINTS something: a path, a shading,
    an inline image or a Form XObject. Such content is neither tagged nor an
    artifact, which PDF/UA forbids (Matterhorn 01-005)."""
    for operands, op in seg_ops:
        if op in _PAINT_OPS:
            return True
        if op == b"Do" and operands:
            if xobject_kinds.get(str(operands[0]).lstrip("/")) != "/Image":
                return True
    return False


def _font_embedded(font: Any) -> bool:
    font = _resolve(font)
    if not isinstance(font, DictionaryObject):
        return False
    sub = str(font.get("/Subtype") or "")
    if sub == "/Type3":
        return True  # glyphs are content-stream procedures inside the font
    if sub == "/Type0":
        desc = _resolve(font.get("/DescendantFonts"))
        if isinstance(desc, (list, ArrayObject)) and desc:
            return _font_embedded(desc[0])
        return False
    fd = _resolve(font.get("/FontDescriptor"))
    if not isinstance(fd, DictionaryObject):
        return False
    return any(k in fd for k in ("/FontFile", "/FontFile2", "/FontFile3"))


def _unembedded_fonts(pages) -> List[str]:
    """BaseFont names of fonts the pages use without embedding them
    (PDF/UA-1 7.21.4.1 requires every font program to be embedded)."""
    from app.pdf.text_decode import page_fonts

    missing: List[str] = []
    for page in pages:
        fonts = page_fonts(page)
        if not isinstance(fonts, DictionaryObject):
            continue
        for _name, ref in fonts.items():
            f = _resolve(ref)
            if not _font_embedded(f):
                base = str((f or {}).get("/BaseFont") or "unnamed").lstrip("/")
                if base not in missing:
                    missing.append(base)
    return missing


_STANDARD_ENCODINGS = {"/WinAnsiEncoding", "/MacRomanEncoding", "/StandardEncoding", "/PDFDocEncoding"}


def _fonts_without_unicode(pages) -> List[str]:
    """Fonts whose glyphs cannot be mapped to Unicode reliably: no /ToUnicode
    and no standard encoding (symbolic TrueType, custom Type3, Identity-H
    without a map). PDF/UA-1 7.21.7 requires the mapping."""
    from app.pdf.text_decode import page_fonts

    out: List[str] = []
    for page in pages:
        fonts = page_fonts(page)
        if not isinstance(fonts, DictionaryObject):
            continue
        for _name, ref in fonts.items():
            f = _resolve(ref)
            if not isinstance(f, DictionaryObject) or "/ToUnicode" in f:
                continue
            enc = _resolve(f.get("/Encoding"))
            base_enc = None
            if isinstance(enc, DictionaryObject):
                base_enc = str(enc.get("/BaseEncoding") or "")
                ok = base_enc in _STANDARD_ENCODINGS and "/Differences" not in enc
            else:
                ok = str(enc or "") in _STANDARD_ENCODINGS
            if str(f.get("/Subtype") or "") == "/Type1" and enc is None:
                ok = True  # a standard Type1 font's built-in encoding is Latin text
            if not ok:
                base = str(f.get("/BaseFont") or "unnamed").lstrip("/")
                if base not in out:
                    out.append(base)
    return out


def _pending_review_alt_count(tree: AccessibilityTree) -> int:
    """Image descriptions our own alt step wrote in this run and flagged for
    human review — a conformance claim cannot rest on unreviewed alt text."""
    n = 0
    try:
        from app.models.accessibility import ImageNode, iter_reading_order

        for node in iter_reading_order(tree.root):
            if isinstance(node, ImageNode) and (node.metadata.properties or {}).get("alt_text_pending_review"):
                n += 1
    except Exception:  # pragma: no cover
        return 0
    return n


def _existing_xmp(catalog: DictionaryObject) -> bytes:
    try:
        md = _resolve(catalog.get("/Metadata"))
        return md.get_data() if md is not None else b""
    except Exception:
        return b""


_PDFUA_PART_RE = re.compile(rb"pdfuaid:part\s*(?:>\s*|=\s*[\"'])\s*(\d)")


def _clamp_heading_specs(specs: List[Dict[str, Any]], state: Dict[str, Any]) -> int:
    """Turn size-ranked heading levels into a contiguous OUTLINE.

    Levels come from a font-size ranking, which knows nothing about order: a
    14pt subtitle under a 26pt title ranked H4 because 18pt and 16pt headings
    existed elsewhere, and re-auditing our own output raised
    HEADING_LEVEL_JUMP — a new defect we had created (Matterhorn 14-003).

    Clamping each heading to "previous + 1" fixed the jump but broke
    siblings: under a 16pt H3, the 13pt "2.1" was clamped to H4 and the
    equally-sized "2.2" right after it was then allowed H5 — a sibling
    section filed as a child of the one before it. So the level is taken from
    the outline instead: a stack of the size ranks of the open headings (a
    larger font is a smaller rank). A heading closes every open heading of the
    same or a smaller size, and its level is the number still open + 1. So

    * levels never jump (each is at most one deeper than the open parent);
    * equal sizes in the same context are siblings (the same level);
    * a heading is only ever PROMOTED relative to its size rank, never
      demoted, and the first heading of the document is H1.

    ``state["stack"]`` carries the open headings across pages. Content is
    never touched. Returns how many headings changed level.
    """
    changed = 0
    stack: List[int] = state.setdefault("stack", [])
    for spec in specs:
        s = str(spec.get("s") or "")
        m = re.fullmatch(r"/H([1-6])", s)
        if not m:
            continue
        rank = int(m.group(1))
        while stack and stack[-1] >= rank:
            stack.pop()
        lvl = min(6, len(stack) + 1)
        stack.append(rank)
        if lvl != rank:
            spec["s"] = f"/H{lvl}"
            changed += 1
    return changed


def _annot_rect(a_obj: Any) -> Optional[Tuple[float, float, float, float]]:
    try:
        vals = [float(v) for v in list(_resolve(a_obj.get("/Rect")))[:4]]
        x0, y0, x1, y1 = vals
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    except Exception:
        return None


def _placement_index(specs: List[Dict[str, Any]], rect, kind: str) -> Optional[Tuple[int, str]]:
    """Where an annotation's element belongs among a page's top-level specs.

    ``(index, "after"|"before")`` next to the text on the SAME line — the
    block a link sits in, a form field's label — or None (append, as before).
    Pure ordering: the annotation and the text are unchanged. It fixes the
    screen-reader experience of hearing every label on a form and then every
    field, because all /Form elements were appended after the page's text.
    """
    if rect is None:
        return None
    x0, y0, x1, y1 = rect
    h = max(1.0, y1 - y0)
    same_line = []
    for i, sp in enumerate(specs):
        pos = sp.get("pos")
        if not pos:
            continue
        bx, by = pos
        if bx is None or by is None:
            continue
        if (y0 - 0.35 * h - 2.0) <= by <= (y1 + 1.0):
            same_line.append((i, bx))
    if not same_line:
        # Inside a list or table (a list of links, a form laid out as a
        # grid): keep the element right after that container.
        for i, sp in enumerate(specs):
            ysp = sp.get("yspan")
            if ysp and (y0 - 2.0) <= ysp[1] and (y1 + 2.0) >= ysp[0]:
                return (i, "after")
        if kind == "widget":
            # A label printed directly ABOVE the field, at its left edge.
            above = [
                (i, sp["pos"][1]) for i, sp in enumerate(specs)
                if sp.get("pos") and sp["pos"][0] is not None and sp["pos"][1] is not None
                and y1 < sp["pos"][1] <= y1 + 30.0 and abs(sp["pos"][0] - x0) <= 24.0
            ]
            if above:
                return (min(above, key=lambda t: t[1])[0], "after")
        return None
    left = [(i, bx) for i, bx in same_line if bx <= (x1 if kind == "link" else x0 + 1.0)]
    if left:
        i = max(left, key=lambda t: t[1])[0]
        return (i, "after")
    right = [(i, bx) for i, bx in same_line if bx >= x1 - 1.0]
    if right and kind == "widget":
        i = min(right, key=lambda t: t[1])[0]
        return (i, "before")
    return None


def tag_pdf(writer: PdfWriter, tree: AccessibilityTree) -> Dict[str, Any]:
    """Add PDF/UA document metadata + a basic structure tree to ``writer``.

    Never raises. Returns a report dict. Safe by construction:
    * already-tagged PDFs keep their structure (only additive metadata is set);
    * pages with existing marked content or no content are skipped;
    * the structure step is isolated so a failure still lands metadata and
      never corrupts the file.

    The XMP ``pdfuaid:part`` identifier — a claim that the file CONFORMS to
    PDF/UA-1 — is written only when every check this module can verify passes
    (``report["pdfuaClaimed"]``); otherwise it is omitted and
    ``report["pdfuaBlockers"]`` says why in plain words. It used to be
    written unconditionally, labelling files with untagged images and
    unembedded fonts as conformant.
    """
    report: Dict[str, Any] = {"applied": [], "structTree": False, "pdfuaClaimed": False}
    applied: List[str] = report["applied"]
    blockers: List[str] = []

    title: Optional[str] = None
    language: Optional[str] = None
    if isinstance(tree.root, DocumentNode):
        props = tree.root.metadata.properties or {}
        title = props.get("title")
        language = tree.root.metadata.language

    try:
        catalog = writer._root_object  # noqa: SLF001 - pypdf exposes intentionally
    except Exception as exc:  # pragma: no cover - defensive
        report["error"] = f"no_catalog: {exc}"
        return report

    # ---- Document-level essentials (additive, safe even when tagged) ------
    try:
        if language:
            catalog[NameObject("/Lang")] = TextStringObject(str(language))
            applied.append("lang")
    except Exception as exc:
        logger.debug("set /Lang failed: %s", exc)

    # Only declare DisplayDocTitle when there is actually a title to display,
    # otherwise the viewer shows an empty title bar.
    if title:
        try:
            vp = _resolve(catalog.get("/ViewerPreferences"))
            if not isinstance(vp, DictionaryObject):
                vp = DictionaryObject()
                catalog[NameObject("/ViewerPreferences")] = vp
            vp[NameObject("/DisplayDocTitle")] = BooleanObject(True)
            applied.append("display_doc_title")
        except Exception as exc:
            logger.debug("set DisplayDocTitle failed: %s", exc)

    prior_xmp = _existing_xmp(catalog)

    def _write_xmp(claim_part: Optional[int]) -> None:
        try:
            meta = DecodedStreamObject()
            meta.set_data(_xmp_packet(title, language, pdfua_part=claim_part))
            meta[NameObject("/Type")] = NameObject("/Metadata")
            meta[NameObject("/Subtype")] = NameObject("/XML")
            catalog[NameObject("/Metadata")] = writer._add_object(meta)  # noqa: SLF001
            applied.append("xmp_metadata")
        except Exception as exc:
            logger.debug("write XMP failed: %s", exc)

    # ---- Never modify an already-tagged document's structure --------------
    if _is_already_tagged(catalog):
        report["alreadyTagged"] = True
        # We did not build (or verify) this structure tree, so we assert
        # nothing about it. An identifier the AUTHOR already wrote is theirs
        # and is carried over unchanged — dropping it silently would be its
        # own misstatement.
        m = _PDFUA_PART_RE.search(prior_xmp or b"")
        carried = int(m.group(1)) if m else None
        report["pdfuaClaimed"] = bool(carried)
        if carried:
            report["pdfuaClaimCarriedOver"] = True
        _write_xmp(carried)
        return report

    # ---- Structure tree over taggable pages only --------------------------
    try:
        # Keep each taggable page's TRUE 1-based document page number: the
        # alt map is keyed by it, and `taggable` is a filtered subset whose
        # own index would drift the moment any page is skipped.
        page_numbers = {id(p): i for i, p in enumerate(writer.pages, start=1)}
        all_pages = list(writer.pages)
        taggable = [p for p in all_pages if _is_taggable_page(writer, p)]
        if not taggable:
            report["structSkipped"] = "no_taggable_pages"
            blockers.append("no page could be given a structure tree")
            raise _NoStructure()

        struct_root = DictionaryObject()
        struct_root_ref = writer._add_object(struct_root)  # noqa: SLF001
        doc_elem = DictionaryObject()
        doc_elem_ref = writer._add_object(doc_elem)  # noqa: SLF001

        font_cache: Dict[Any, Any] = {}
        alt_by_xobject = _build_alt_by_xobject(tree)
        decorative_by_page = _build_decorative_by_xobject(tree)
        artifact_info = _collect_artifact_sigs(writer, taggable, font_cache)
        doc_levels, bold_keys, bold_level, watermarks = _collect_doc_structure_hints(
            writer, taggable, artifact_info, font_cache
        )
        page_counters: Dict[str, int] = {"artifacts": 0}
        elem_refs: List[IndirectObject] = []  # all struct elems, reading order
        nums = ArrayObject()
        figures = 0
        figures_no_alt = 0
        per_element_pages = 0
        lists_tagged = 0
        tables_tagged = 0
        links_tagged = 0
        links_named_from_page = 0
        forms_tagged = 0
        widgets_without_name = 0
        other_annots = 0
        headings_clamped = 0
        heading_state: Dict[str, Any] = {}
        # Annotation ParentTree keys live ABOVE the page keys; a number tree's
        # /Nums must stay ascending, so collect and append them after the loop.
        next_annot_key = len(taggable)
        annot_nums: List[Tuple[int, IndirectObject]] = []
        for key, page in enumerate(taggable):
            pno = page_numbers.get(id(page), -1)
            page_alt = alt_by_xobject.get(pno, {})
            specs = _tag_page_elements(
                writer, page, page_alt, artifact_info, page_counters, doc_levels,
                decorative_xobjects=decorative_by_page.get(pno, set()),
                font_cache=font_cache,
                bold_keys=bold_keys,
                bold_level=bold_level,
                watermarks=watermarks,
            )
            if specs is None:
                # Safe fallback: page-level single /P (original bytes untouched).
                _wrap_page_marked_content(writer, page, mcid=0)
                specs = [{"s": "/P", "mcid": 0}]
            else:
                per_element_pages += 1
            headings_clamped += _clamp_heading_specs(specs, heading_state)

            # Build the page's (possibly nested) struct elements. Leaves register
            # their MCID -> ref so the ParentTree can index back to them.
            mcid_to_ref: Dict[int, IndirectObject] = {}
            stats = {"figures": 0, "figures_no_alt": 0}
            page_refs: List[IndirectObject] = []
            for spec in specs:
                ref = _build_struct_elem(
                    writer, spec, doc_elem_ref, page, mcid_to_ref, stats
                )
                page_refs.append(ref)  # top-level elems are the Document's kids
                if spec.get("s") == "/L":
                    lists_tagged += 1
                elif spec.get("s") == "/Table":
                    tables_tagged += 1
            figures += stats["figures"]
            figures_no_alt += stats.get("figures_no_alt", 0)

            # ParentTree row: index = MCID -> the leaf StructElem that owns it.
            page_arr = ArrayObject()
            if mcid_to_ref:
                for m in range(max(mcid_to_ref) + 1):
                    page_arr.append(mcid_to_ref.get(m, NullObject()))
            page[NameObject("/StructParents")] = NumberObject(key)
            page[NameObject("/Tabs")] = NameObject("/S")
            nums.append(NumberObject(key))
            nums.append(page_arr)

            # Annotations: PDF/UA requires link annotations nested in /Link
            # structure elements (Matterhorn 28-011) and form widgets nested
            # in /Form elements, each via an OBJR reference with a ParentTree
            # entry pointing back at the element.
            after: Dict[int, List[IndirectObject]] = {}
            before: Dict[int, List[IndirectObject]] = {}
            tail: List[IndirectObject] = []
            words_cache: Dict[str, Any] = {}

            def _page_words():
                if "w" not in words_cache:
                    try:
                        from app.pdf.text_geometry import all_words, page_spans

                        sp = page_spans(page, writer)
                        words_cache["w"] = all_words(sp) if sp is not None else []
                    except Exception:
                        words_cache["w"] = []
                return words_cache["w"]

            for a in list(page.get("/Annots") or []):
                try:
                    a_obj = a.get_object()
                except Exception:
                    continue
                subtype = str(a_obj.get("/Subtype") or "")
                if subtype == "/Link":
                    elem_s = "/Link"
                elif subtype == "/Widget":
                    elem_s = "/Form"
                else:
                    try:
                        hidden = bool(int(a_obj.get("/F") or 0) & 2)
                    except Exception:
                        hidden = False
                    if subtype not in ("/Popup",) and not hidden:
                        other_annots += 1
                    continue
                a_ref = a if isinstance(a, IndirectObject) else None
                if a_ref is None:
                    continue  # direct-dict annots are vanishingly rare; skip
                objr = DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/OBJR"),
                        NameObject("/Obj"): a_ref,
                        NameObject("/Pg"): page.indirect_reference,
                    }
                )
                annot_elem = DictionaryObject(
                    {
                        NameObject("/Type"): NameObject("/StructElem"),
                        NameObject("/S"): NameObject(elem_s),
                        NameObject("/P"): doc_elem_ref,
                        NameObject("/Pg"): page.indirect_reference,
                        NameObject("/K"): objr,
                    }
                )
                annot_elem_ref = writer._add_object(annot_elem)  # noqa: SLF001
                rect = _annot_rect(a_obj)
                place = _placement_index(specs, rect, "link" if subtype == "/Link" else "widget")
                if place is None:
                    tail.append(annot_elem_ref)
                elif place[1] == "after":
                    after.setdefault(place[0], []).append(annot_elem_ref)
                else:
                    before.setdefault(place[0], []).append(annot_elem_ref)
                a_obj[NameObject("/StructParent")] = NumberObject(next_annot_key)
                annot_nums.append((next_annot_key, annot_elem_ref))
                next_annot_key += 1
                # Accessible description (Matterhorn 28-012). A link is named by
                # the words printed under it — what a sighted reader sees —
                # falling back to its URI only when no text is there. A widget
                # falls back to its /TU label.
                if not str(a_obj.get("/Contents") or "").strip():
                    fallback = ""
                    if subtype == "/Link":
                        try:
                            from app.pdf.text_geometry import text_in_rect

                            visible = text_in_rect(_page_words(), a_obj.get("/Rect") or [])
                        except Exception:
                            visible = None
                        if visible:
                            fallback = visible
                            links_named_from_page += 1
                        else:
                            try:
                                action = a_obj.get("/A")
                                action = action.get_object() if hasattr(action, "get_object") else action
                                fallback = str((action or {}).get("/URI") or "").strip()
                            except Exception:
                                fallback = ""
                    else:
                        fallback = str(a_obj.get("/TU") or "").strip()
                    if fallback:
                        a_obj[NameObject("/Contents")] = TextStringObject(fallback)
                if subtype == "/Link":
                    links_tagged += 1
                else:
                    forms_tagged += 1
                    if not str(a_obj.get("/TU") or "").strip():
                        parent = _resolve(a_obj.get("/Parent"))
                        if not (isinstance(parent, DictionaryObject) and str(parent.get("/TU") or "").strip()):
                            widgets_without_name += 1

            for i, ref in enumerate(page_refs):
                elem_refs.extend(before.get(i, []))
                elem_refs.append(ref)
                elem_refs.extend(after.get(i, []))
            elem_refs.extend(tail)

        doc_elem.update(
            {
                NameObject("/Type"): NameObject("/StructElem"),
                NameObject("/S"): NameObject("/Document"),
                NameObject("/P"): struct_root_ref,
                NameObject("/K"): ArrayObject(elem_refs),
            }
        )
        # Annotation keys appended after all page keys keeps /Nums ascending.
        for k, link_ref in annot_nums:
            nums.append(NumberObject(k))
            nums.append(link_ref)
        parent_tree = DictionaryObject({NameObject("/Nums"): nums})
        parent_tree_ref = writer._add_object(parent_tree)  # noqa: SLF001
        struct_root.update(
            {
                NameObject("/Type"): NameObject("/StructTreeRoot"),
                NameObject("/K"): ArrayObject([doc_elem_ref]),
                NameObject("/ParentTree"): parent_tree_ref,
                NameObject("/ParentTreeNextKey"): NumberObject(next_annot_key),
            }
        )
        catalog[NameObject("/StructTreeRoot")] = struct_root_ref
        # Only NOW mark the document Tagged — there is a real, linked tree.
        catalog[NameObject("/MarkInfo")] = DictionaryObject(
            {NameObject("/Marked"): BooleanObject(True)}
        )
        report["structTree"] = True
        report["pages"] = len(taggable)
        report["elements"] = len(elem_refs)
        # "figures" keeps meaning figures WITH a description; the undescribed
        # ones are tagged (so they are in the reading order) and counted apart.
        report["figures"] = figures
        report["figuresWithoutAlt"] = figures_no_alt
        report["decorativeImages"] = page_counters.get("decorative_images", 0)
        report["lists"] = lists_tagged
        report["tables"] = tables_tagged
        report["links"] = links_tagged
        report["linksNamedFromPage"] = links_named_from_page
        report["formWidgets"] = forms_tagged
        report["artifacts"] = page_counters.get("artifacts", 0)
        report["perElementPages"] = per_element_pages
        # ...and its honest complement: pages we could NOT segment (malformed or
        # unparseable content streams, nested text objects, an op count that
        # didn't reconcile) and had to wrap as a single page-level /P. They are
        # tagged and valid, but carry no per-element structure — so reporting
        # only perElementPages would let a mostly-unstructured document read as
        # a fully structured one.
        report["pagesPageLevelOnly"] = len(taggable) - per_element_pages
        # Pages whose columns were interleaved in the content stream and are
        # now presented in correct visual reading order by the structure tree.
        report["readingOrderFixedPages"] = page_counters.get("reading_order_fixed", 0)
        # Pages that LOOK two-column (and interleaved) but were left in stream
        # order — disclosed, never counted as fixed.
        report["readingOrderDeclinedPages"] = page_counters.get("reading_order_declined", 0)
        # Table-SHAPED grids we deliberately DECLINED to tag (too sparse to be
        # sure they're data). Surfaced so "N tables tagged" is never read as
        # "all of your tables were handled" — a silent under-count is the kind
        # of quiet overclaim this project refuses.
        report["tablesDeclined"] = page_counters.get("tables_declined", 0)
        report["tocTablesDeclined"] = page_counters.get("toc_declined", 0)
        # Contents rows read as one paragraph each ("1. Introduction 3").
        report["tocRowsJoined"] = page_counters.get("toc_rows_joined", 0)
        report["headingLevelsNormalized"] = headings_clamped
        report["boldHeadings"] = page_counters.get("bold_headings", 0)
        # Recurring off-axis stamps ("DRAFT" on every page) marked /Artifact;
        # already included in "artifacts".
        report["watermarkArtifacts"] = page_counters.get("watermarks", 0)
        report["undecodableTextBlocks"] = page_counters.get("undecodable_blocks", 0)
        applied.append("struct_tree")
        applied.append("mark_info")

        # ---- May this file claim PDF/UA-1? Only if nothing we can check fails.
        if len(taggable) != len(all_pages):
            blockers.append(f"{len(all_pages) - len(taggable)} page(s) could not be tagged")
        if report["pagesPageLevelOnly"]:
            blockers.append(f"{report['pagesPageLevelOnly']} page(s) are tagged only as one block")
        if not title:
            blockers.append("the document has no title")
        if not language:
            blockers.append("the document language is not set")
        if figures_no_alt:
            blockers.append(f"{figures_no_alt} image(s) still need a text description")
        if report["readingOrderDeclinedPages"]:
            blockers.append(
                f"{report['readingOrderDeclinedPages']} page(s) may have a two-column reading order we did not change"
            )
        if report["tablesDeclined"]:
            blockers.append(f"{report['tablesDeclined']} table-like grid(s) were not tagged")
        if report["undecodableTextBlocks"]:
            blockers.append("some text uses a font we could not map to Unicode")
        if widgets_without_name:
            blockers.append(f"{widgets_without_name} form field(s) have no accessible name")
        if other_annots:
            blockers.append(f"{other_annots} comment/markup annotation(s) are not tagged")
        if page_counters.get("untagged_paint_pages"):
            blockers.append(
                f"{page_counters['untagged_paint_pages']} page(s) draw lines, shapes or embedded "
                "graphics that are neither tagged nor marked as decoration"
            )
        unembedded = _unembedded_fonts(taggable)
        if unembedded:
            shown = ", ".join(unembedded[:3]) + ("…" if len(unembedded) > 3 else "")
            blockers.append(f"{len(unembedded)} font(s) are not embedded ({shown})")
        unmapped = _fonts_without_unicode(taggable)
        if unmapped:
            blockers.append(f"{len(unmapped)} font(s) have no reliable Unicode mapping")
        enc = getattr(writer, "_encryption", None)
        try:
            if enc is not None and not (int(enc.P) & (1 << 9)):
                blockers.append("the file's security settings block text extraction for assistive technology")
        except Exception:
            pass
        pending_alt = _pending_review_alt_count(tree)
        if pending_alt:
            blockers.append(f"{pending_alt} image description(s) were generated automatically and await review")
    except _NoStructure:
        pass
    except Exception as exc:
        logger.warning("PDF struct-tree tagging failed (essentials still applied): %s", exc)
        report["structError"] = str(exc)
        blockers.append("the structure tree could not be completed")

    claim = report["structTree"] and not blockers
    report["pdfuaClaimed"] = bool(claim)
    report["pdfuaBlockers"] = blockers
    _write_xmp(1 if claim else None)
    return report


class _NoStructure(Exception):
    """Internal: no taggable page; skip to the metadata tail."""


__all__ = ["tag_pdf"]
