"""Font-aware decoding of PDF show-text operands.

Why this exists. The structure tagger reads block text from the RAW operand
bytes (latin-1), which is exactly right for single-byte fonts: a WinAnsi
bullet arrives as byte 0x95 and the marker tests are keyed on it. It is
exactly WRONG for composite (``/Type0``) fonts — what Google Docs, Chrome,
Skia, LibreOffice-with-subsetting and most modern producers emit. There every
glyph is a 2-byte CID, so "Winter Road" arrives as ``\\x00:\\x00L\\x00Q...``.
Every text test built on those bytes then sees garbage:

* the title candidate wrote the CID bytes as ``/Title`` (and charged for it);
* a numeric top row ("2021 | 31.2") could not be recognised as data, so it
  was typed ``/TH``;
* bullets, ordinals and "Page 1 of 2" were never recognised.

This module answers one question per operand: what Unicode text does it
show, or ``None`` when that cannot be known. For composite fonts the answer
requires a ``/ToUnicode`` map that covers EVERY code shown (Identity-H CIDs
are glyph indices, not characters: without the map, decoding "succeeds" into
printable nonsense). ``None`` means "make no claim from this text" — callers
fail closed.

Pure read-only helpers; never raises.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NumberObject

logger = logging.getLogger(__name__)

_SHOW_OPS = (b"Tj", b"TJ", b"'", b'"')


def _resolve(obj: Any) -> Any:
    seen = 0
    while isinstance(obj, IndirectObject) and seen < 8:
        try:
            obj = obj.get_object()
        except Exception:
            return None
        seen += 1
    return obj


def operand_bytes(obj: Any) -> bytes:
    """The raw bytes of a string operand, whatever pypdf wrapped it in."""
    try:
        raw = getattr(obj, "original_bytes", None)
        if raw is not None:
            return bytes(raw)
        if isinstance(obj, (bytes, bytearray)):
            return bytes(obj)
        if isinstance(obj, str):
            return obj.encode("latin-1", "replace")
    except Exception:
        pass
    return b""


def page_fonts(page: Any) -> Optional[DictionaryObject]:
    """The page's ``/Font`` resource dict, following ``/Resources`` inheritance."""
    node = page
    for _ in range(32):
        if node is None:
            return None
        try:
            res = _resolve(node.get("/Resources")) if "/Resources" in node else None
        except Exception:
            res = None
        if isinstance(res, DictionaryObject):
            fonts = _resolve(res.get("/Font")) if "/Font" in res else None
            return fonts if isinstance(fonts, DictionaryObject) else None
        try:
            node = _resolve(node.get("/Parent"))
        except Exception:
            return None
    return None


class _FontInfo:
    __slots__ = ("composite", "encoding", "char_map", "cmap_name", "font_dict", "ok")

    def __init__(self) -> None:
        self.composite = False
        self.encoding: Any = None
        self.char_map: Dict[Any, Any] = {}
        self.cmap_name = ""
        self.font_dict: Optional[DictionaryObject] = None
        self.ok = False


class FontDecoder:
    """Decode show-text operands for one page's fonts (lazily, cached)."""

    def __init__(self, page: Any, shared_cache: Optional[Dict[Any, _FontInfo]] = None) -> None:
        self._fonts = page_fonts(page)
        self._cache: Dict[str, Optional[_FontInfo]] = {}
        # Fonts are usually shared across pages by indirect reference; parsing a
        # ToUnicode CMap once per document instead of once per page matters on
        # 400-page reports.
        self._shared = shared_cache if shared_cache is not None else {}

    # -- font lookup ---------------------------------------------------------
    def _info(self, font_name: Optional[str]) -> Optional[_FontInfo]:
        if not font_name or self._fonts is None:
            return None
        if font_name in self._cache:
            return self._cache[font_name]
        info: Optional[_FontInfo] = None
        try:
            ref = self._fonts.raw_get(font_name) if font_name in self._fonts else None
        except Exception:
            ref = None
        key = None
        if isinstance(ref, IndirectObject):
            key = (ref.idnum, ref.generation)
            if key in self._shared:
                self._cache[font_name] = self._shared[key]
                return self._shared[key]
        ft = _resolve(ref)
        if isinstance(ft, DictionaryObject):
            info = _FontInfo()
            info.font_dict = ft
            info.composite = str(ft.get("/Subtype") or "") == "/Type0"
            try:
                from pypdf._cmap import build_char_map_from_dict

                _sub, _half, encoding, char_map = build_char_map_from_dict(200.0, ft)
                info.encoding = encoding
                info.char_map = {k: v for k, v in (char_map or {}).items() if k != -1}
                enc = _resolve(ft.get("/Encoding"))
                info.cmap_name = str(enc) if isinstance(enc, str) else ""
                info.ok = True
            except Exception:
                # e.g. pypdf 4.2's parse_bfrange raises binascii.Error on the
                # 5-hex-digit destinations MuPDF writes. We cannot decode this
                # font; say so rather than guess.
                logger.debug("font %s: char map unavailable", font_name, exc_info=True)
                info.ok = False
        self._cache[font_name] = info
        if key is not None:
            self._shared[key] = info
        return info

    def is_composite(self, font_name: Optional[str]) -> bool:
        info = self._info(font_name)
        return bool(info and info.composite)

    def is_known(self, font_name: Optional[str]) -> bool:
        return self._info(font_name) is not None

    def is_bold(self, font_name: Optional[str]) -> bool:
        """True when the font is a bold face: its BaseFont names a bold weight,
        its descriptor declares /FontWeight >= 600, or ForceBold is set."""
        info = self._info(font_name)
        if info is None or info.font_dict is None:
            return False
        try:
            ft = info.font_dict
            dicts = [ft]
            if info.composite:
                desc = _resolve(ft.get("/DescendantFonts"))
                if isinstance(desc, (list, ArrayObject)) and desc:
                    d0 = _resolve(desc[0])
                    if isinstance(d0, DictionaryObject):
                        dicts.append(d0)
            for d in dicts:
                base = str(d.get("/BaseFont") or "").lower()
                if any(w in base for w in ("bold", "black", "heavy", "demi")):
                    return True
                fd = _resolve(d.get("/FontDescriptor"))
                if isinstance(fd, DictionaryObject):
                    try:
                        if float(fd.get("/FontWeight") or 0) >= 600:
                            return True
                    except (TypeError, ValueError):
                        pass
                    try:
                        if int(fd.get("/Flags") or 0) & (1 << 18):
                            return True
                    except (TypeError, ValueError):
                        pass
        except Exception:
            return False
        return False

    # -- decoding ------------------------------------------------------------
    def decode(self, font_name: Optional[str], raw: bytes) -> Optional[str]:
        """Unicode text for ``raw`` shown in ``font_name``, or None if unknowable."""
        info = self._info(font_name)
        if info is None or not info.ok:
            return None
        enc = info.encoding
        try:
            if isinstance(enc, str):
                txt = raw.decode(enc, "surrogatepass")
            elif isinstance(enc, dict):
                txt = "".join(enc[b] if b in enc else chr(b) for b in raw)
            else:
                return None
        except Exception:
            return None
        cmap = info.char_map
        if info.composite:
            # Identity-H/V (and embedded CMaps) show glyph ids: only a ToUnicode
            # entry turns one into a character. Predefined Unicode CMaps (UCS2 /
            # UTF16) already decode to real text through the codec.
            unicode_cmap = ("UCS2" in info.cmap_name or "UTF16" in info.cmap_name)
            if not unicode_cmap:
                if not cmap or any(ch not in cmap for ch in txt):
                    return None
        if cmap:
            txt = "".join(cmap.get(ch, ch) for ch in txt)
        return txt

    def decode_unicode(self, font_name: Optional[str], raw: bytes) -> Optional[str]:
        """Like :meth:`decode`, and for simple fonts falls back to latin-1."""
        txt = self.decode(font_name, raw)
        if txt is not None:
            return txt
        if self.is_composite(font_name):
            return None
        try:
            return raw.decode("latin-1")
        except Exception:
            return None


class FontState:
    """Track the current text font through a content stream (q/Q/Tf)."""

    __slots__ = ("font", "_stack")

    def __init__(self, font: Optional[str] = None) -> None:
        self.font = font
        self._stack: List[Optional[str]] = []

    def feed(self, operands: Any, op: bytes) -> None:
        if op == b"q":
            self._stack.append(self.font)
        elif op == b"Q":
            if self._stack:
                self.font = self._stack.pop()
        elif op == b"Tf" and operands:
            try:
                self.font = str(operands[0])
            except Exception:
                pass


def show_strings(operands: Any, op: bytes) -> List[Any]:
    """The string operands a show-text operator paints, in order."""
    if op == b"Tj" and operands:
        return [operands[0]]
    if op == b"TJ" and operands and isinstance(operands[0], (list, ArrayObject)):
        return [el for el in operands[0] if not isinstance(el, (NumberObject, int, float))]
    if op == b"'" and operands:
        return [operands[-1]]
    if op == b'"' and operands:
        return [operands[-1]]
    return []


def decode_ops(
    ops: Iterable[Tuple[Any, bytes]],
    decoder: FontDecoder,
    start_font: Optional[str],
    *,
    unicode_simple: bool = False,
) -> Tuple[Optional[str], bool, Optional[str]]:
    """Decode the text shown by ``ops``.

    Returns ``(text, used_composite, end_font)``. ``text`` is None when any
    operand in a composite font could not be decoded. For simple fonts the
    RAW bytes are returned as latin-1 (the marker tests depend on them) unless
    ``unicode_simple`` asks for the font's own encoding.
    """
    state = FontState(start_font)
    parts: List[str] = []
    used_composite = False
    failed = False
    for operands, op in ops:
        state.feed(operands, op)
        if op not in _SHOW_OPS:
            continue
        for s in show_strings(operands, op):
            raw = operand_bytes(s)
            if decoder.is_composite(state.font):
                used_composite = True
                txt = decoder.decode(state.font, raw)
                if txt is None:
                    failed = True
                    continue
                parts.append(txt)
            elif unicode_simple:
                txt = decoder.decode_unicode(state.font, raw)
                parts.append(txt if txt is not None else raw.decode("latin-1", "ignore"))
            else:
                parts.append(raw.decode("latin-1", "ignore"))
    if failed:
        return None, used_composite, state.font
    return "".join(parts), used_composite, state.font


def is_readable_text(text: Optional[str], *, min_share: float = 0.9) -> bool:
    """True when ``text`` reads as human text rather than glyph codes.

    Rejects any C0/C1 control character (other than ordinary whitespace) — a
    2-byte CID string always carries ``\\x00`` — and requires that at least
    ``min_share`` of the non-space characters are letters, digits, marks or
    common punctuation.
    """
    if not text or not text.strip():
        return False
    visible = 0
    good = 0
    for ch in text:
        if ch in " \t\r\n":
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cs", "Co", "Cn"):
            return False
        if ch == "�":
            return False
        visible += 1
        if cat[0] in ("L", "N", "M", "P", "S", "Z"):
            good += 1
    return visible > 0 and good / visible >= min_share


__all__ = [
    "FontDecoder",
    "FontState",
    "decode_ops",
    "is_readable_text",
    "operand_bytes",
    "page_fonts",
    "show_strings",
]
