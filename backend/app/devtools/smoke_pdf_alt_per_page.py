"""Smoke: image alt text lands on the RIGHT page when XObject names repeat.

XObject names are per-page resource keys, and producers reuse them: every
page of a scanned PDF is /Im0. Both places that write alt text keyed a flat
{name: alt} map, so whichever page's node came LAST won, and its description
was stamped onto every other page's image — the user's approved alt for
page 1 written onto page 3's picture, and reported as applied.

Pinned here on a 3-page PDF whose images are all named /Im0 with a distinct
approved alt per page:
  * the writer's /Alt on each page's XObject is THAT page's alt
  * the tagger's /Figure /Alt on each page is THAT page's alt
  * the writer reports one alt_text application per page (not 3x the last)

Usage:
    python -m app.devtools.smoke_pdf_alt_per_page
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_alt_')}/s.db")

from pathlib import Path  # noqa: E402

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from app.models.accessibility import ImageNode, iter_reading_order  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.writers.pdf_writer import write_remediated_pdf  # noqa: E402


def _pdf_with_shared_image_names(n: int) -> bytes:
    """n pages, each drawing an image XObject named /Im0 plus a line of text."""
    w = PdfWriter()
    font = DictionaryObject()
    font.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    font_ref = w._add_object(font)  # noqa: SLF001
    for i in range(n):
        img = DecodedStreamObject()
        img.set_data(bytes([200 - 40 * i]) * (4 * 4 * 3))  # tiny 4x4 RGB, distinct per page
        img.update({
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(4),
            NameObject("/Height"): NumberObject(4),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        })
        img_ref = w._add_object(img)  # noqa: SLF001
        fonts = DictionaryObject()
        fonts[NameObject("/F1")] = font_ref
        xobjs = DictionaryObject()
        xobjs[NameObject("/Im0")] = img_ref            # SAME name on every page
        res = DictionaryObject()
        res[NameObject("/Font")] = fonts
        res[NameObject("/XObject")] = xobjs
        page = w.add_blank_page(width=300, height=300)
        cs = DecodedStreamObject()
        cs.set_data(
            b"BT /F1 12 Tf 20 270 Td (Page %d caption text) Tj ET\n"
            b"q 100 0 0 100 100 100 cm /Im0 Do Q" % (i + 1)
        )
        page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
        page[NameObject("/Resources")] = res
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _figure_alts_by_page(reader: PdfReader) -> dict:
    """{page_number: [alt, ...]} read back from /Figure StructElems."""
    out: dict = {}
    try:
        st = reader.trailer["/Root"]["/StructTreeRoot"].get_object()
    except Exception:
        return out
    # Attribute by the page's indirect reference (idnum, generation) — object
    # identity is not stable across separate resolutions in pypdf.
    page_index = {}
    for i, p in enumerate(reader.pages, start=1):
        ref = getattr(p, "indirect_reference", None)
        if ref is not None:
            page_index[(ref.idnum, ref.generation)] = i

    def walk(e):
        e = e.get_object()
        if str(e.get("/S") or "") == "/Figure" and "/Alt" in e:
            pg = e.get("/Pg")
            pgn = None
            if pg is not None and hasattr(pg, "idnum"):
                pgn = page_index.get((pg.idnum, pg.generation))
            out.setdefault(pgn, []).append(str(e["/Alt"]))
        k = e.get("/K")
        k = k.get_object() if hasattr(k, "get_object") else k
        kids = k if isinstance(k, list) else ([k] if k is not None else [])
        for kid in kids:
            if hasattr(kid, "get_object") and isinstance(kid.get_object(), DictionaryObject):
                walk(kid)

    for kid in st.get("/K", ArrayObject()):
        walk(kid)
    return out


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="508_alt_"))
    src = tmp / "shared.pdf"
    out = tmp / "shared-fixed.pdf"
    src.write_bytes(_pdf_with_shared_image_names(3))

    res = parse_to_tree(str(src))
    images = [n for n in iter_reading_order(res.tree.root) if isinstance(n, ImageNode)]
    check("parser sees one image per page (3)", len(images) == 3, str(len(images)))
    check("every image node is named /Im0 (the shared-name shape)",
          all((n.metadata.properties or {}).get("xobject", "").lstrip("/") == "Im0" for n in images))
    check("image nodes carry distinct 1-based page numbers",
          sorted(n.metadata.page for n in images) == [1, 2, 3], str([n.metadata.page for n in images]))

    # Approve a DISTINCT alt per page, the way a user would.
    for n in images:
        n.alt_text = f"Approved description for page {n.metadata.page}"
        n.is_decorative = False

    rep = write_remediated_pdf(src, res.tree, out)
    alt_apps = [a for a in rep.get("applied", []) if a.get("kind") == "alt_text"]
    check("writer reports exactly one alt application per page (3)", len(alt_apps) == 3, str(alt_apps))

    reader = PdfReader(str(out))
    per_page_xobj_alt = []
    for i, page in enumerate(reader.pages, start=1):
        xo = page["/Resources"]["/XObject"]["/Im0"].get_object()
        per_page_xobj_alt.append(str(xo.get("/Alt", "")))
    for i, alt in enumerate(per_page_xobj_alt, start=1):
        check(f"page {i} XObject /Alt is page {i}'s own approved text",
              alt == f"Approved description for page {i}", repr(alt))

    fig = _figure_alts_by_page(reader)
    check("tagger emitted a /Figure with /Alt on every page", sorted(k for k in fig if k) == [1, 2, 3], str(fig))
    for i in (1, 2, 3):
        check(f"page {i} /Figure /Alt is page {i}'s own approved text",
              fig.get(i) == [f"Approved description for page {i}"], str(fig.get(i)))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
