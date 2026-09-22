"""Tiny builders for PDF smoke fixtures (pypdf only — no reportlab, no fitz).

Real producers emit shapes the older fixtures never exercised:

* composite ``/Type0`` fonts with ``/Identity-H`` (Google Docs, Chrome, Skia,
  most subsetting exporters): every glyph is a 2-byte CID and the text is
  only readable through the ``/ToUnicode`` CMap;
* ToUnicode CMaps that pypdf 4.2 cannot parse (MuPDF's 5-hex-digit bfrange
  destinations);
* ``/Title null`` in the Info dictionary;
* images, link annotations, AcroForm widgets.

Not a smoke itself (no ``smoke_`` prefix, so run_smoke does not collect it).
"""

from __future__ import annotations

import io
import struct
import zlib
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NullObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)


# CIDs as a subsetting producer assigns them: NOT the character codes.
# (Arial's glyph ids put "W" at 0x3A, which is why "Winter" arrives as
# "\x00:\x00L\x00Q..." in the raw operand bytes.)
def cid_for(ch: str) -> int:
    return ord(ch) - 29 if ord(ch) >= 32 else 3


def type0_hex(text: str) -> bytes:
    """``<....>`` hex string of 2-byte CIDs for ``text``."""
    return b"<" + b"".join(f"{cid_for(c):04X}".encode() for c in text) + b">"


def _tounicode_cmap(chars: Iterable[str], *, broken: bool = False) -> bytes:
    lines = [
        b"/CIDInit /ProcSet findresource begin",
        b"12 dict begin",
        b"begincmap",
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        b"/CMapName /Adobe-Identity-UCS def",
        b"/CMapType 2 def",
        b"1 begincodespacerange",
        b"<0000> <FFFF>",
        b"endcodespacerange",
    ]
    uniq = sorted(set(chars))
    lines.append(f"{len(uniq)} beginbfchar".encode())
    for ch in uniq:
        lines.append(f"<{cid_for(ch):04X}> <{ord(ch):04X}>".encode())
    lines.append(b"endbfchar")
    if broken:
        # MuPDF writes some destinations with an odd number of hex digits;
        # pypdf 4.2's parse_bfrange raises binascii.Error on it.
        lines += [b"1 beginbfrange", b"<11CD> <11D2> <10780>", b"endbfrange"]
    lines += [b"endcmap", b"CMapName currentdict /CMap defineresource pop", b"end", b"end"]
    return b"\n".join(lines)


def type0_font(w: PdfWriter, chars: Iterable[str], *, to_unicode: bool = True, broken: bool = False,
               base: str = "/ArialMT", embedded: bool = False):
    cid_font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/CIDFontType2"),
        NameObject("/BaseFont"): NameObject(base),
        NameObject("/CIDSystemInfo"): DictionaryObject({
            NameObject("/Registry"): TextStringObject("Adobe"),
            NameObject("/Ordering"): TextStringObject("Identity"),
            NameObject("/Supplement"): NumberObject(0),
        }),
        NameObject("/DW"): NumberObject(556),
    })
    desc = DictionaryObject({
        NameObject("/Type"): NameObject("/FontDescriptor"),
        NameObject("/FontName"): NameObject(base),
        NameObject("/Flags"): NumberObject(32),
        NameObject("/FontBBox"): ArrayObject([NumberObject(v) for v in (-665, -325, 2000, 1040)]),
        NameObject("/ItalicAngle"): NumberObject(0),
        NameObject("/Ascent"): NumberObject(905),
        NameObject("/Descent"): NumberObject(-212),
        NameObject("/CapHeight"): NumberObject(716),
        NameObject("/StemV"): NumberObject(80),
    })
    if embedded:
        ff = DecodedStreamObject()
        ff.set_data(b"\x00\x01\x00\x00fake-font-program")
        desc[NameObject("/FontFile2")] = w._add_object(ff)  # noqa: SLF001
    cid_font[NameObject("/FontDescriptor")] = w._add_object(desc)  # noqa: SLF001
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type0"),
        NameObject("/BaseFont"): NameObject(base),
        NameObject("/Encoding"): NameObject("/Identity-H"),
        NameObject("/DescendantFonts"): ArrayObject([w._add_object(cid_font)]),  # noqa: SLF001
    })
    if to_unicode:
        tu = DecodedStreamObject()
        tu.set_data(_tounicode_cmap(chars, broken=broken))
        font[NameObject("/ToUnicode")] = w._add_object(tu)  # noqa: SLF001
    return w._add_object(font)  # noqa: SLF001


def helvetica(w: PdfWriter, bold: bool = False):
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica-Bold" if bold else "/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })
    return w._add_object(font)  # noqa: SLF001


def gray_image(w: PdfWriter, width: int = 40, height: int = 30, value: int = 128):
    img = StreamObject()
    img._data = zlib.compress(bytes([value]) * (width * height))  # noqa: SLF001
    img.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(width),
        NameObject("/Height"): NumberObject(height),
        NameObject("/ColorSpace"): NameObject("/DeviceGray"),
        NameObject("/BitsPerComponent"): NumberObject(8),
        NameObject("/Filter"): NameObject("/FlateDecode"),
    })
    return w._add_object(img)  # noqa: SLF001


def add_page(w: PdfWriter, content: bytes, fonts: Dict[str, object], *, width: float = 612,
             height: float = 792, xobjects: Optional[Dict[str, object]] = None,
             annots: Optional[List[object]] = None):
    page = w.add_blank_page(width=width, height=height)
    cs = DecodedStreamObject()
    cs.set_data(content)
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    res = DictionaryObject()
    fd = DictionaryObject()
    for k, ref in fonts.items():
        fd[NameObject(k if k.startswith("/") else "/" + k)] = ref
    res[NameObject("/Font")] = fd
    if xobjects:
        xd = DictionaryObject()
        for k, ref in xobjects.items():
            xd[NameObject(k if k.startswith("/") else "/" + k)] = ref
        res[NameObject("/XObject")] = xd
    page[NameObject("/Resources")] = res
    if annots:
        page[NameObject("/Annots")] = ArrayObject(annots)
    return page


def link_annot(w: PdfWriter, rect: Sequence[float], *, uri: Optional[str] = None,
               dest_page=None, contents: Optional[str] = None, named: Optional[str] = None):
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
        NameObject("/Border"): ArrayObject([NumberObject(0)] * 3),
    })
    if uri is not None:
        annot[NameObject("/A")] = DictionaryObject({
            NameObject("/S"): NameObject("/URI"), NameObject("/URI"): TextStringObject(uri)})
    elif dest_page is not None:
        annot[NameObject("/Dest")] = ArrayObject([dest_page, NameObject("/Fit")])
    elif named is not None:
        annot[NameObject("/A")] = DictionaryObject({
            NameObject("/S"): NameObject("/GoTo"), NameObject("/D"): TextStringObject(named)})
    if contents:
        annot[NameObject("/Contents")] = TextStringObject(contents)
    return w._add_object(annot)  # noqa: SLF001


def widget(w: PdfWriter, rect: Sequence[float], *, name: str, ft: str = "/Tx",
           tu: Optional[str] = None, ff: int = 0):
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject(ft),
        NameObject("/T"): TextStringObject(name),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
        NameObject("/F"): NumberObject(4),
    })
    if tu:
        annot[NameObject("/TU")] = TextStringObject(tu)
    if ff:
        annot[NameObject("/Ff")] = NumberObject(ff)
    return w._add_object(annot)  # noqa: SLF001


def set_acroform(w: PdfWriter, field_refs: List[object]) -> None:
    w._root_object[NameObject("/AcroForm")] = DictionaryObject({  # noqa: SLF001
        NameObject("/Fields"): ArrayObject(field_refs),
        NameObject("/NeedAppearances"): BooleanObject(True),
    })


def null_title(w: PdfWriter) -> None:
    """Write ``/Title null`` into the Info dictionary (PyMuPDF does this)."""
    info = w._info  # noqa: SLF001
    info_obj = info.get_object() if hasattr(info, "get_object") else info
    info_obj[NameObject("/Title")] = NullObject()


def to_bytes(w: PdfWriter) -> bytes:
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def bt(font: str, size: float, x: float, y: float, operand: bytes) -> bytes:
    """One BT..ET line: ``operand`` is a ready (…) or <…> string."""
    return b"BT /%s %g Tf %g %g Td %s Tj ET\n" % (font.encode(), size, x, y, operand)


def lit(text: str) -> bytes:
    esc = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + esc.encode("latin-1") + b")"


def isolated_env(prefix: str) -> str:
    """Point this process at a fresh sqlite DB + a 64-char secret. Call
    BEFORE importing app.main. Returns the temp dir."""
    import os
    import tempfile

    tmp = tempfile.mkdtemp(prefix=prefix)
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/s.db"
    os.environ["MATERIALIZED_ROOT"] = os.path.join(tmp, "materialized")
    os.environ["APP_SECRET"] = ("pdf-lane-smoke-secret-" + prefix + "x" * 64)[:64]
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        os.environ.pop(k, None)
    return tmp


class Pipeline:
    """A signed-in TestClient with a funded wallet (set directly, as the
    starter grant needs a verified email)."""

    def __init__(self, email: str) -> None:
        import json as _json

        from fastapi.testclient import TestClient
        from sqlalchemy import select

        from app.db.models import UserRow
        from app.db.session_sqlalchemy import session_scope
        from app.main import app

        self._json = _json
        self._select = select
        self._UserRow = UserRow
        self._session_scope = session_scope
        self.email = email
        self.client = TestClient(app, raise_server_exceptions=False)
        r = self.client.post("/auth/sign-in", json={"email": email, "displayName": "PDF", "password": "pdflanepass1"})
        assert r.status_code == 200, r.text
        self.headers = {"Authorization": f"Bearer {r.json()['token']}"}
        self.set_balance(100)

    def set_balance(self, n: int) -> None:
        with self._session_scope() as s:
            row = s.execute(self._select(self._UserRow).where(self._UserRow.email == self.email)).scalars().first()
            row.credits_balance = n

    def balance(self) -> int:
        return int(self.client.get("/credits/balance", headers=self.headers).json()["balance"])

    def analyze(self, name: str, data: bytes):
        return self.client.post("/pipeline/analyze", files={"file": (name, data, "application/pdf")}, headers=self.headers)

    def remediate(self, name: str, data: bytes, ids=None):
        if ids is None:
            a = self.analyze(name, data)
            assert a.status_code == 200, a.text
            ids = [v["id"] for v in a.json()["violations"]]
        return self.client.post(
            "/pipeline/remediate",
            files={"file": (name, data, "application/pdf")},
            data={"approved_violations": self._json.dumps(ids), "rejected_violations": "[]"},
            headers=self.headers,
        )

    def download(self, body) -> bytes:
        dl = self.client.get(body["downloadUrl"], headers=self.headers)
        assert dl.status_code == 200, dl.text[:200]
        return dl.content


class Checker:
    def __init__(self) -> None:
        self.failures = 0

    def __call__(self, name: str, cond: bool, extra: str = "") -> None:
        print(("PASS" if cond else "FAIL"), "-", name, (extra if not cond else ""))
        if not cond:
            self.failures += 1

    def done(self) -> int:
        print(f"\nRESULT: {'all passed' if self.failures == 0 else str(self.failures) + ' FAILED'}")
        return 1 if self.failures else 0


def struct_elems(reader) -> List[Tuple[int, str, object]]:
    """Depth-first ``(depth, /S, elem)`` over the output structure tree."""
    out: List[Tuple[int, str, object]] = []
    st = reader.trailer["/Root"].get("/StructTreeRoot")
    if st is None:
        return out

    def walk(e, d):
        e = e.get_object() if hasattr(e, "get_object") else e
        if not isinstance(e, DictionaryObject) or "/S" not in e:
            return
        out.append((d, str(e["/S"]), e))
        k = e.get("/K")
        k = k.get_object() if hasattr(k, "get_object") and not isinstance(k, int) else k
        if isinstance(k, (list, ArrayObject)):
            for kid in k:
                walk(kid, d + 1)
        elif isinstance(k, DictionaryObject) and "/S" in k:
            walk(k, d + 1)

    root_k = st.get_object()["/K"]
    for kid in (root_k if isinstance(root_k, (list, ArrayObject)) else [root_k]):
        walk(kid, 0)
    return out


def marked_tags(reader, page_index: int) -> List[str]:
    """Marked-content tags (BDC/BMC operands) in a page's stream, in order."""
    from pypdf.generic import ContentStream

    ops = ContentStream(reader.pages[page_index].get_contents(), reader).operations
    return [str(o[0][0]) for o in ops if o[1] in (b"BDC", b"BMC") and o[0]]
