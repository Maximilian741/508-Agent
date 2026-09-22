"""Where text sits on a PDF page: decoded words with user-space boxes.

The parser used to know WHAT a page said (``extract_text``) but not WHERE, so
anything that depends on placement was either guessed or skipped:

* a link's accessible name was its URI, or "(link)" — the words printed under
  the link rectangle were never read, so every PDF link was reported as
  non-descriptive;
* a form field whose label is printed right next to it stayed unlabeled;
* a "Figure 1. ..." caption under a chart was invisible to the alt-text step;
* no finding could say where on the page it is.

This module walks a page's content stream with pypdf's own layout-mode text
state machine (the same font widths and ToUnicode decoding ``extract_text``
uses), and returns :class:`Word` objects in PDF user space: origin
bottom-left, y up, points. Rotated text is skipped (its boxes would be
wrong). Anything unexpected returns ``None`` — callers must treat "no
geometry" as "no claim".

pypdf is pinned (requirements.txt); the private layout-mode helpers used here
exist in 4.x. Every entry point is wrapped so an API change degrades to
``None`` rather than an exception.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, IndirectObject

from app.pdf.text_decode import is_readable_text, page_fonts

logger = logging.getLogger(__name__)

Rect = Tuple[float, float, float, float]


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    y: float  # baseline
    size: float  # effective font height in user space

    @property
    def top(self) -> float:
        return self.y + self.size * 0.8

    @property
    def bottom(self) -> float:
        return self.y - self.size * 0.2

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0


@dataclass
class Span:
    """One show-text operation's decoded text and extent."""

    text: str
    x0: float
    x1: float
    y: float
    size: float
    words: List[Word] = field(default_factory=list)


def _resolve(obj: Any) -> Any:
    if isinstance(obj, IndirectObject):
        try:
            return obj.get_object()
        except Exception:
            return None
    return obj


# Advance widths (1/1000 em) of the printable ASCII range for the standard
# base-14 families, from Adobe's AFM files. pypdf 4.2's own table is shifted
# from "U" onward (Helvetica "a" = 278, "o" = 278, "w" = 500 ...), which put
# every word of a base-14 PDF in the wrong place — link rectangles then
# "contained" the wrong words. Only used for simple fonts that carry no
# /Widths array of their own.
_ASCII = "".join(chr(c) for c in range(32, 127))
_AFM = {
    "Helvetica": [
        278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
        556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
        1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
        667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
        333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
        556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
    ],
    "Helvetica-Bold": [
        278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
        556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
        975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
        667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
        333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
        611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584,
    ],
    "Times": [
        250, 333, 408, 500, 500, 833, 778, 180, 333, 333, 500, 564, 250, 333, 250, 278,
        500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 278, 278, 564, 564, 564, 444,
        921, 722, 667, 667, 722, 611, 556, 722, 722, 333, 389, 722, 611, 889, 722, 722,
        556, 722, 667, 556, 611, 722, 722, 944, 722, 722, 611, 333, 278, 333, 469, 500,
        333, 444, 500, 444, 500, 444, 333, 500, 500, 278, 278, 500, 278, 778, 500, 500,
        500, 500, 333, 389, 278, 500, 500, 722, 500, 500, 444, 480, 200, 480, 541,
    ],
    "Times-Bold": [
        250, 333, 555, 500, 500, 1000, 833, 278, 333, 333, 500, 570, 250, 333, 250, 278,
        500, 500, 500, 500, 500, 500, 500, 500, 500, 500, 333, 333, 570, 570, 570, 500,
        930, 722, 667, 722, 722, 667, 611, 778, 778, 389, 500, 778, 667, 944, 722, 778,
        611, 778, 722, 556, 667, 722, 722, 1000, 722, 722, 667, 333, 278, 333, 581, 500,
        333, 500, 556, 444, 556, 444, 333, 500, 556, 278, 333, 556, 278, 833, 556, 500,
        556, 556, 444, 389, 333, 556, 500, 722, 500, 500, 444, 394, 220, 394, 520,
    ],
}


def _afm_table(base_font: str) -> Optional[Dict[str, int]]:
    b = (base_font or "").lstrip("/")
    if "+" in b[:7]:
        b = b.split("+", 1)[1]  # subset prefix ABCDEF+
    low = b.lower().replace(" ", "").replace(",", "-")
    bold = "bold" in low or "black" in low or "heavy" in low
    if low.startswith("courier"):
        return {ch: 600 for ch in _ASCII}
    if low.startswith(("helvetica", "arial")):
        key = "Helvetica-Bold" if bold else "Helvetica"
    elif low.startswith(("times", "timesnewroman")):
        key = "Times-Bold" if bold else "Times"
    else:
        return None
    return dict(zip(_ASCII, _AFM[key]))


def standard_text_width(base_font: str, text: str, size: float) -> Optional[float]:
    """Width of ``text`` in a base-14 font at ``size`` (None if not base-14)."""
    table = _afm_table(base_font)
    if table is None:
        return None
    return sum(table.get(ch, 500) for ch in text) * size / 1000.0


def _layout_fonts(page: Any) -> Dict[str, Any]:
    from pypdf._cmap import build_char_map_from_dict
    from pypdf._text_extraction._layout_mode._font import Font

    fonts: Dict[str, Any] = {}
    fdict = page_fonts(page)
    if not isinstance(fdict, DictionaryObject):
        return fonts
    for name in list(fdict.keys()):
        ft = _resolve(fdict.raw_get(name))
        if not isinstance(ft, DictionaryObject):
            continue
        try:
            cmap = build_char_map_from_dict(200.0, ft)
            font_dict = {
                k: (
                    v.get_object()
                    if isinstance(v, IndirectObject)
                    else [_v.get_object() if isinstance(_v, IndirectObject) else _v for _v in v]
                    if isinstance(v, ArrayObject)
                    else v
                )
                for k, v in ft.items()
            }
            font = Font(*cmap, font_dict)  # type: ignore[arg-type]
            if "/Widths" not in ft and "/DescendantFonts" not in ft:
                table = _afm_table(str(ft.get("/BaseFont") or ""))
                if table is not None:
                    merged = dict(font.width_map or {})
                    merged.update(table)
                    font.width_map = merged
            fonts[str(name)] = font
        except Exception:
            logger.debug("layout font %s unavailable", name, exc_info=True)
    return fonts


def _split_words(tj: Any) -> List[Word]:
    """Word boxes inside one text-show op, advancing by the font's widths."""
    words: List[Word] = []
    txt = tj.txt or ""
    if not txt.strip():
        return words
    try:
        a = float(tj.transform[0])
        if abs(a) < 1e-9:
            return words
        font = tj.font
        scale_tz = float(tj.Tz) / 100.0
        x = float(tj.tx)
        start_x = None
        buf: List[str] = []
        for ch in txt:
            w = font.width_map.get(ch, font.space_width * 2)
            adv = (float(tj.font_size) * (float(w) / 1000.0) + float(tj.Tc)
                   + (float(tj.Tw) if ch == " " else 0.0)) * scale_tz * a
            if ch.isspace():
                if buf:
                    words.append(Word("".join(buf), start_x, x, float(tj.ty), float(tj.font_height)))
                    buf = []
                    start_x = None
            else:
                if start_x is None:
                    start_x = x
                buf.append(ch)
            x += adv
        if buf:
            words.append(Word("".join(buf), start_x, x, float(tj.ty), float(tj.font_height)))
    except Exception:
        return []
    # A negative horizontal scale mirrors text; boxes would be inverted.
    return [w for w in words if w.x1 >= w.x0]


def page_spans(page: Any, pdf: Any = None) -> Optional[List[Span]]:
    """Decoded, positioned text of one page in user space, or None."""
    try:
        from pypdf._text_extraction._layout_mode._fixed_width_page import recurs_to_target_op
        from pypdf._text_extraction._layout_mode._text_state_manager import TextStateManager

        fonts = _layout_fonts(page)
        contents = _resolve(page.get("/Contents")) if "/Contents" in page else None
        if contents is None:
            return []
        owner = pdf if pdf is not None else getattr(page, "pdf", None)
        ops = iter(ContentStream(contents, owner, "bytes").operations)
        state = TextStateManager()
        tjs: List[Any] = []
        while True:
            try:
                operands, op = next(ops)
            except StopIteration:
                break
            if op in (b"BT", b"q"):
                _bts, got = recurs_to_target_op(
                    ops, state, b"ET" if op == b"BT" else b"Q", fonts, True
                )
                tjs.extend(got)
            elif op == b"cm":
                state.add_cm(*operands)
            else:
                state.set_state_param(op, operands)
    except Exception:
        logger.debug("page_spans failed", exc_info=True)
        return None
    spans: List[Span] = []
    for tj in tjs:
        try:
            if getattr(tj, "rotated", False) or getattr(tj, "flip_vertical", False):
                continue
            txt = tj.txt or ""
            if not txt.strip():
                continue
            words = _split_words(tj)
            x0, x1 = float(tj.tx), float(tj.displaced_tx)
            spans.append(Span(txt, min(x0, x1), max(x0, x1), float(tj.ty), float(tj.font_height), words))
        except Exception:
            continue
    return spans


def all_words(spans: Optional[Sequence[Span]]) -> List[Word]:
    out: List[Word] = []
    for s in spans or []:
        out.extend(s.words)
    return out


def _reading_sorted(words: Iterable[Word]) -> List[Word]:
    ws = sorted(words, key=lambda w: (-w.y, w.x0))
    lines: List[List[Word]] = []
    for w in ws:
        if lines and abs(lines[-1][0].y - w.y) <= max(2.0, 0.3 * w.size):
            lines[-1].append(w)
        else:
            lines.append([w])
    out: List[Word] = []
    for ln in lines:
        out.extend(sorted(ln, key=lambda w: w.x0))
    return out


def _norm_rect(rect: Sequence[Any]) -> Optional[Rect]:
    try:
        x0, y0, x1, y1 = (float(v) for v in list(rect)[:4])
    except Exception:
        return None
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def text_in_rect(words: Sequence[Word], rect: Sequence[Any], *, max_chars: int = 150) -> Optional[str]:
    """The words printed inside ``rect`` (a link annotation), in reading order.

    A word counts when its horizontal centre lies inside the rectangle and
    its baseline sits within the rectangle's vertical span (baseline is
    inside the box for text an author selected as a link). Returns None when
    nothing is there, when the text is unreadable, or when the rectangle
    holds more than a link's worth of text (a whole-paragraph or full-page
    link rect names nothing specific).
    """
    r = _norm_rect(rect)
    if r is None:
        return None
    x0, y0, x1, y1 = r
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None
    hits = [
        w for w in words
        if x0 - 0.5 <= w.cx <= x1 + 0.5 and (y0 - 1.5) <= w.y <= (y1 + 0.5)
    ]
    if not hits:
        return None
    ordered = _reading_sorted(hits)
    lines = len({round(w.y) for w in ordered})
    text = re.sub(r"\s+", " ", " ".join(w.text for w in ordered)).strip()
    if not text or len(text) > max_chars or lines > 3:
        return None
    if not is_readable_text(text):
        return None
    return text


# --- form-field labels from the page ---------------------------------------

_LABEL_TRAIL_RE = re.compile(r"[\s:*]+$")
_FILLER_RE = re.compile(r"^[_.\-…·]+$")


def _contiguous(words: List[Word], direction: int) -> List[Word]:
    """Words adjacent along a line (``direction`` -1: walk left, +1: right)."""
    if not words:
        return []
    out = [words[0]]
    for w in words[1:]:
        prev = out[-1]
        gap = (prev.x0 - w.x1) if direction < 0 else (w.x0 - prev.x1)
        if gap < -1.0 or gap > max(4.0, 0.9 * max(prev.size, w.size)):
            break
        out.append(w)
    return out if direction > 0 else list(reversed(out))


def _clean_label(text: str, *, allow_sentence: bool) -> Optional[str]:
    t = _LABEL_TRAIL_RE.sub("", re.sub(r"\s+", " ", text or "")).strip()
    if not t or not any(c.isalpha() for c in t):
        return None
    if not is_readable_text(t):
        return None
    limit = 120 if allow_sentence else 60
    if len(t) > limit or len(t) == 1:
        return None
    if not allow_sentence and (t.endswith((".", "?", "!")) or len(t.split()) > 8):
        return None
    return t


def label_for_widget(
    rect: Sequence[Any],
    words: Sequence[Word],
    other_rects: Sequence[Rect] = (),
    *,
    kind: str = "text",
) -> Optional[Tuple[str, Tuple[Tuple[float, float], ...]]]:
    """The visible label printed next to a form widget, or None.

    ``kind`` "text" (text/choice fields): the words on the same line
    immediately to the LEFT of the field, else the words directly ABOVE it
    starting at its left edge. ``kind`` "check" (a checkbox): the words on the
    same line on the NEARER side of the box — "[ ] Yes" and "Yes [ ]" are both
    common, and "Yes [ ]  No [ ]" read right-hand-only labelled the Yes box
    "No". Returns ``(label, word_keys)`` so the caller can refuse a label
    claimed by two widgets. Precision first: anything ambiguous returns None
    and the field stays manual. Refusals include: a checkbox with words
    equally near on both sides; an above-"label" that is larger than the
    page's body text (a heading, not a label); an above-"label" printed
    directly UNDER another field (that field's caption).
    """
    r = _norm_rect(rect)
    if r is None:
        return None
    x0, y0, x1, y1 = r
    h = max(1.0, y1 - y0)
    # Fill-in rules typed as text ("________", "......") are the field's line,
    # never part of its label: they are dropped, and they end a label run.
    words = [w for w in words if not _FILLER_RE.match(w.text)]
    # The "same line" band. A tall field (a multi-line comments box) is
    # labelled at its TOP line; its full height would sweep in any text that
    # happens to sit to its left further down.
    lo = (y0 - 0.35 * h - 2.0) if h <= 30.0 else (y1 - 24.0)
    band = [w for w in words if lo <= w.y <= (y1 + 1.0)]
    sizes = sorted(w.size for w in words if w.size > 0)
    body = sizes[len(sizes) // 2] if sizes else 0.0

    def blocked(a: float, b: float, line_y: float) -> bool:
        """Another widget sits between x=a and x=b on this line."""
        lo, hi = min(a, b), max(a, b)
        for o in other_rects:
            if o == r:
                continue
            if o[0] < hi - 1 and o[2] > lo + 1 and o[1] - 2 <= line_y <= o[3] + 2:
                return True
        return False

    def finish(ws: List[Word], allow_sentence: bool):
        if not ws:
            return None
        label = _clean_label(" ".join(w.text for w in ws), allow_sentence=allow_sentence)
        if not label:
            return None
        return label, tuple((round(w.x0, 1), round(w.y, 1)) for w in ws)

    if kind == "check":
        right = sorted((w for w in band if w.x0 >= x1 - 1.0), key=lambda w: w.x0)
        left_c = sorted((w for w in band if w.x1 <= x0 + 1.0), key=lambda w: -w.x1)
        if right and blocked(x1, right[0].x0, right[0].y):
            right = []  # the nearest right word is past another widget
        if left_c and blocked(left_c[0].x1, x0, left_c[0].y):
            left_c = []
        gap_r = (right[0].x0 - x1) if right else None
        gap_l = (x0 - left_c[0].x1) if left_c else None
        sides = [(g, s) for g, s in ((gap_r, "R"), (gap_l, "L")) if g is not None and g <= 40.0]
        if not sides:
            return None
        if len(sides) == 2 and abs(sides[0][0] - sides[1][0]) < 3.0:
            return None  # words equally near on both sides: whose label is it?
        side = min(sides)[1]
        if side == "R":
            first = right[0]
            same_line = [w for w in right if abs(w.y - first.y) <= 2.0]
            run = _contiguous(same_line, +1)
            # stop at the next widget on the line
            cut: List[Word] = []
            for w in run:
                if blocked(x1, w.x0, w.y):
                    break
                cut.append(w)
            return finish(cut, allow_sentence=True)
        first = left_c[0]
        same_line = [w for w in left_c if abs(w.y - first.y) <= 2.0]
        run = _contiguous(same_line, -1)
        # never reach past the previous widget on the line
        kept = [w for w in run if not blocked(w.x1, x0, w.y)]
        return finish(kept, allow_sentence=True)

    left = sorted((w for w in band if w.x1 <= x0 + 1.5), key=lambda w: -w.x1)
    if left and x0 - left[0].x1 <= 100.0:
        first = left[0]
        if not blocked(first.x1, x0, first.y):
            same_line = [w for w in left if abs(w.y - first.y) <= 2.0]
            run = _contiguous(same_line, -1)
            got = finish(run, allow_sentence=False)
            if got:
                return got
    # Label printed directly above the field, aligned with its left edge.
    above = [
        w for w in words
        if y1 < w.y <= y1 + max(14.0, 1.9 * w.size) and x0 - 4.0 <= w.x0 <= x0 + 24.0
    ]
    if not above:
        return None
    lowest = min(w.y for w in above)
    start = min((w for w in above if abs(w.y - lowest) <= 2.0), key=lambda w: w.x0)
    line = sorted(
        (w for w in words if abs(w.y - start.y) <= 2.0 and w.x0 >= start.x0 - 0.5),
        key=lambda w: w.x0,
    )
    run = _contiguous(line, +1)
    # An above-label must not run past the field (then it labels a row).
    run = [w for w in run if w.x0 <= x1 + 2.0]
    if not run:
        return None
    # Larger than the page's body text: a heading or title, not a label.
    if body and max(w.size for w in run) > 1.25 * body:
        return None
    # Printed directly UNDER another field: that field's caption ("Signature
    # of applicant" under the signature line), sitting just above this one.
    rx0, rx1 = min(w.x0 for w in run), max(w.x1 for w in run)
    for o in other_rects:
        if o == r:
            continue
        if o[0] < rx1 - 1 and o[2] > rx0 + 1 and 0.0 <= o[1] - start.y <= 1.9 * start.size + 2.0:
            return None
    return finish(run, allow_sentence=False)


def union_bbox(boxes: Iterable[Rect]) -> Optional[Rect]:
    bs = [b for b in boxes if b]
    if not bs:
        return None
    return (
        min(b[0] for b in bs),
        min(b[1] for b in bs),
        max(b[2] for b in bs),
        max(b[3] for b in bs),
    )


def word_bbox(w: Word) -> Rect:
    return (w.x0, w.bottom, w.x1, w.top)


__all__ = [
    "Span",
    "Word",
    "all_words",
    "label_for_widget",
    "page_spans",
    "text_in_rect",
    "union_bbox",
    "word_bbox",
]
