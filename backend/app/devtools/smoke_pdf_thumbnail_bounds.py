"""Smoke: a PDF picture's thumbnail can't be turned into a memory bomb.

The finding-location pass shows a thumbnail of every picture that lacks alt
text. For a PDF it used to hand the image to pypdf (``page.images[...]``),
which inflates the WHOLE Flate stream with no limit — so one signed-out
/pipeline/analyze of a ~2 MB PDF, whose 1000x1000 picture (3 MB of samples,
under every pixel cap) inflated to 2 GB of zeros, peaked at 4 GB. The server
is sized at 2 GB.

``finding_location._pdf_xobject_image`` now inflates at most the bytes the
image's OWN dictionary declares (width x height x components x bits), refuses
an encoded stream over MAX_THUMB_SOURCE_BYTES and a declared size over
MAX_THUMB_DECODED_BYTES, and never calls pypdf's decoder.

Pinned here (Python-heap peak via tracemalloc — every unbounded path is a
Python ``bytes`` from zlib, so a regression shows up as hundreds of MB):
  1. build_locations on the bomb PDF: bounded, and the picture still gets its
     thumbnail (the declared 1000x1000 samples, as a viewer shows them);
  2. a signed-out /pipeline/analyze of it: 200, bounded, thumbnail present;
  3. the same bomb hidden behind a PNG predictor, in the /SMask, in an
     /Indexed lookup stream, behind a second Flate, in front of a DCT: bounded;
  4. the decoder draws what the PDF means — pixel-exact against the source
     for Flate RGB/gray/CMYK, PNG predictors, 4- and 8-bit palettes, 1-bit
     gray, a stencil mask, a soft mask, /Decode [1 0], ASCII85; and
  5. an encoding it can't bound (LZW) is simply no thumbnail, never an error.

Run: python -m app.devtools.smoke_pdf_thumbnail_bounds
"""

from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import tracemalloc
import zlib
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="508_smoke_thumbbomb_"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP / 'bomb.db').as_posix()}"
os.environ["STORAGE_LOCAL_ROOT"] = str(_TMP / "storage")
os.environ["MATERIALIZED_ROOT"] = str(_TMP / "materialized")
for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SEMANTIC_PROVIDER", "SMTP_HOST"):
    os.environ.pop(_key, None)

from PIL import Image, ImageChops, ImageDraw  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    BooleanObject,
    ByteStringObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
)

BOMB_MB = 256
# Generous for everything the request legitimately allocates on the Python
# heap; a regression inflates BOMB_MB (256 MB) or more.
PEAK_LIMIT = 64 * 1024 * 1024
W, H = 120, 80


def _zeros_bomb(mb: int) -> bytes:
    co = zlib.compressobj(9)
    chunk = b"\0" * (1 << 20)
    return b"".join([co.compress(chunk) for _ in range(mb)] + [co.flush()])


def _image(data: bytes, width: int, height: int, **entries) -> StreamObject:
    s = StreamObject()
    s._data = data
    s.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(width),
        NameObject("/Height"): NumberObject(height),
    })
    for key, value in entries.items():
        s[NameObject("/" + key)] = value
    return s


def _pdf_with(images: dict, draw: bool = True) -> bytes:
    """One page; each image an XObject (a stream-valued entry is made indirect)."""
    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    xobjects = DictionaryObject()
    body = []
    for i, (name, stream) in enumerate(images.items()):
        for key in ("/SMask",):
            if isinstance(stream.get(key), StreamObject):
                stream[NameObject(key)] = w._add_object(stream[key])
        cs = stream.get("/ColorSpace")
        if isinstance(cs, ArrayObject) and len(cs) >= 4 and isinstance(cs[3], StreamObject):
            cs[3] = w._add_object(cs[3])
        xobjects[NameObject("/" + name)] = w._add_object(stream)
        body.append(f"q 200 0 0 150 {50 + (i % 2) * 250} {600 - (i // 2) * 160} cm /{name} Do Q")
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects})
    if draw:
        cs = DecodedStreamObject()
        cs.set_data("\n".join(body).encode("latin-1"))
        page[NameObject("/Contents")] = w._add_object(cs)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def _xobject(pdf: bytes, name: str):
    return PdfReader(io.BytesIO(pdf)).pages[0]["/Resources"]["/XObject"]["/" + name].get_object()


def _peak(fn):
    """(result, Python-heap peak in bytes) of fn()."""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        result = fn()
        return result, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def _source() -> Image.Image:
    im = Image.new("RGB", (W, H), (240, 240, 240))
    d = ImageDraw.Draw(im)
    d.rectangle([5, 5, W - 30, H - 20], fill=(200, 30, 30))
    d.ellipse([60, 20, 110, 70], fill=(30, 90, 220))
    d.line([0, H - 1, W - 1, 0], fill=(20, 160, 60), width=4)
    return im


def _png_up_rows(raw: bytes, row: int, rows: int) -> bytes:
    """PNG predictor 'Up' on every row, then Flate — how pdfTeX embeds PNGs."""
    out = bytearray()
    prev = bytes(row)
    for y in range(rows):
        cur = raw[y * row:(y + 1) * row]
        out.append(2)
        out += bytes((cur[i] - prev[i]) & 0xFF for i in range(row))
        prev = cur
    return zlib.compress(bytes(out))


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, detail: object = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, "" if cond else f"  [{str(detail)[:500]}]")
        if not cond:
            failures += 1

    from fastapi.testclient import TestClient

    from app.main import app
    from app.parsers import parse_to_tree
    from app.services import finding_location as fl
    from app.services.remediation_engine import RemediationEngine
    from app.services.remediators.registry import RemediationDispatcher

    mb = 2 ** 20
    bomb = _zeros_bomb(BOMB_MB)
    rgb8 = {"BitsPerComponent": NumberObject(8), "ColorSpace": NameObject("/DeviceRGB")}

    # ---- 1 + 2: the verifier's shape: declared 1000x1000 RGB, inflates to 256 MB ------
    bomb_pdf = _pdf_with({"Im1": _image(bomb, 1000, 1000, Filter=NameObject("/FlateDecode"), **rgb8)})
    path = _TMP / "bomb.pdf"
    path.write_bytes(bomb_pdf)
    res = parse_to_tree(str(path))
    violations = RemediationEngine(dispatcher=RemediationDispatcher([])).detect_violations(res.tree)
    locs, peak = _peak(lambda: fl.build_locations(res.tree, violations, res.format, path))
    alt = [v for v in violations if v.rule_id == "MISSING_ALT_TEXT"]
    thumb = locs.get(alt[0].violation_id, {}).get("thumbnail") if alt else None
    check(
        f"build_locations on a {len(bomb_pdf) // 1024} KB PDF whose picture inflates to {BOMB_MB} MB: "
        f"Python heap peak {peak / mb:.1f} MB (< {PEAK_LIMIT // mb})",
        peak < PEAK_LIMIT,
        f"{peak / mb:.0f} MB",
    )
    check("... and the picture still gets its thumbnail (its declared 1000x1000 samples)", bool(thumb) and thumb.startswith("data:image/png;base64,"), thumb and thumb[:40])

    client = TestClient(app)
    r, peak = _peak(lambda: client.post("/pipeline/analyze", files={"file": ("bomb.pdf", bomb_pdf, "application/pdf")}))
    body = r.json() if r.status_code == 200 else {}
    api_thumbs = [bool(v["location"]["thumbnail"]) for v in body.get("violations", []) if v["ruleId"] == "MISSING_ALT_TEXT"]
    check("signed-out /pipeline/analyze of the bomb -> 200 with the picture's thumbnail", r.status_code == 200 and api_thumbs == [True], (r.status_code, api_thumbs))
    check(f"... Python heap peak for the whole request {peak / mb:.1f} MB (< {PEAK_LIMIT // mb})", peak < PEAK_LIMIT, f"{peak / mb:.0f} MB")

    # ---- 3: the bomb hidden everywhere else a stream can be --------------------------
    flate = {"Filter": NameObject("/FlateDecode")}
    png_parms = DictionaryObject({
        NameObject("/Predictor"): NumberObject(15), NameObject("/Colors"): NumberObject(3),
        NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Columns"): NumberObject(1000),
    })
    lookup = StreamObject()
    lookup._data = bomb
    lookup[NameObject("/Filter")] = NameObject("/FlateDecode")
    variants = {
        "png_predictor": _image(bomb, 1000, 1000, DecodeParms=png_parms, **flate, **rgb8),
        "soft_mask": _image(zlib.compress(b"\x80" * 3_000_000), 1000, 1000, **flate, **rgb8,
                            SMask=_image(bomb, 1000, 1000, BitsPerComponent=NumberObject(8), ColorSpace=NameObject("/DeviceGray"), **flate)),
        "indexed_lookup": _image(zlib.compress(b"\x01" * 1_000_000), 1000, 1000, BitsPerComponent=NumberObject(8), **flate,
                                 ColorSpace=ArrayObject([NameObject("/Indexed"), NameObject("/DeviceRGB"), NumberObject(255), lookup])),
        "double_flate": _image(zlib.compress(bomb), 1000, 1000, Filter=ArrayObject([NameObject("/FlateDecode"), NameObject("/FlateDecode")]), **rgb8),
        "flate_then_dct": _image(bomb, 1000, 1000, Filter=ArrayObject([NameObject("/FlateDecode"), NameObject("/DCTDecode")]), **rgb8),
    }
    variants_pdf = _pdf_with(variants, draw=False)
    for name in variants:
        xo = _xobject(variants_pdf, name)
        _img, peak = _peak(lambda: fl._pdf_xobject_image(xo))
        check(f"bomb as {name}: Python heap peak {peak / mb:.1f} MB (< {PEAK_LIMIT // mb})", peak < PEAK_LIMIT, f"{peak / mb:.0f} MB")

    # ---- 4: what it draws is what the PDF means ---------------------------------------
    src = _source()
    gray = src.convert("L")
    cmyk = src.convert("CMYK")
    one = gray.point(lambda v: 255 if v > 128 else 0).convert("1")
    pal = src.quantize(colors=16)
    lut = bytes((pal.getpalette() + [0] * 48)[:48])
    packed = bytearray()
    idx = pal.tobytes()
    for y in range(H):
        row = idx[y * W:(y + 1) * W]
        for x in range(0, W, 2):
            packed.append((row[x] << 4) | (row[x + 1] if x + 1 < W else 0))
    indexed = ArrayObject([NameObject("/Indexed"), NameObject("/DeviceRGB"), NumberObject(15), ByteStringObject(lut)])
    alpha = Image.new("L", (W, H), 0)
    ImageDraw.Draw(alpha).ellipse([10, 10, 110, 70], fill=255)
    cmyk_parms = DictionaryObject({
        NameObject("/Predictor"): NumberObject(15), NameObject("/Colors"): NumberObject(4),
        NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Columns"): NumberObject(W),
    })
    rgb_parms = DictionaryObject({
        NameObject("/Predictor"): NumberObject(12), NameObject("/Colors"): NumberObject(3),
        NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/Columns"): NumberObject(W),
    })
    bpc8 = {"BitsPerComponent": NumberObject(8)}
    cases = {
        "rgb_flate": (_image(zlib.compress(src.tobytes()), W, H, **flate, **rgb8), src),
        "rgb_uncompressed": (_image(src.tobytes(), W, H, **rgb8), src),
        "gray_flate": (_image(zlib.compress(gray.tobytes()), W, H, **flate, **bpc8, ColorSpace=NameObject("/DeviceGray")), gray),
        "cmyk_flate": (_image(zlib.compress(cmyk.tobytes()), W, H, **flate, **bpc8, ColorSpace=NameObject("/DeviceCMYK")), cmyk.convert("RGB")),
        "rgb_png_predictor": (_image(_png_up_rows(src.tobytes(), W * 3, H), W, H, DecodeParms=rgb_parms, **flate, **rgb8), src),
        "cmyk_png_predictor": (_image(_png_up_rows(cmyk.tobytes(), W * 4, H), W, H, DecodeParms=cmyk_parms, **flate, **bpc8,
                                      ColorSpace=NameObject("/DeviceCMYK")), cmyk.convert("RGB")),
        "palette_8bit": (_image(zlib.compress(idx), W, H, **flate, **bpc8, ColorSpace=indexed), pal.convert("RGB")),
        "palette_4bit": (_image(zlib.compress(bytes(packed)), W, H, **flate, BitsPerComponent=NumberObject(4), ColorSpace=indexed), pal.convert("RGB")),
        "gray_1bit": (_image(zlib.compress(one.tobytes()), W, H, **flate, BitsPerComponent=NumberObject(1), ColorSpace=NameObject("/DeviceGray")), one.convert("L")),
        "stencil_mask": (_image(zlib.compress(one.tobytes()), W, H, **flate, ImageMask=BooleanObject(True)), one.convert("L")),
        "decode_inverted": (_image(zlib.compress(gray.tobytes()), W, H, **flate, **bpc8, ColorSpace=NameObject("/DeviceGray"),
                                   Decode=ArrayObject([NumberObject(1), NumberObject(0)])), gray.point(lambda v: 255 - v)),
        "ascii85_flate": (_image(base64.a85encode(zlib.compress(src.tobytes())) + b"~>", W, H, **rgb8,
                                 Filter=ArrayObject([NameObject("/ASCII85Decode"), NameObject("/FlateDecode")])), src),
        "soft_mask": (_image(zlib.compress(src.tobytes()), W, H, **flate, **rgb8,
                             SMask=_image(zlib.compress(alpha.tobytes()), W, H, **flate, **bpc8, ColorSpace=NameObject("/DeviceGray"))),
                      Image.merge("RGBA", (*src.split(), alpha))),
    }
    cases_pdf = _pdf_with({name: stream for name, (stream, _) in cases.items()}, draw=False)
    for name, (_stream, truth) in cases.items():
        got = fl._pdf_xobject_image(_xobject(cases_pdf, name))
        if got is None:
            check(f"decodes {name} exactly", False, "no image")
            continue
        got = got.convert(truth.mode)
        diff = ImageChops.difference(got, truth)
        worst = max(hi for _lo, hi in diff.getextrema()) if diff.getbands() != ("L",) else diff.getextrema()[1]
        check(f"decodes {name} exactly ({got.size}, max channel diff {worst})", got.size == truth.size and worst == 0, worst)

    # ---- 5: an encoding we can't bound: no thumbnail, no exception --------------------
    lzw = _pdf_with({"L": _image(b"\x80\x0b\x60\x50\x22\x0c\x0c\x85\x01", W, H, Filter=NameObject("/LZWDecode"), **rgb8)}, draw=False)
    check("an LZW picture (no bounded decoder) -> no thumbnail, no exception", fl._pdf_xobject_image(_xobject(lzw, "L")) is None)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
