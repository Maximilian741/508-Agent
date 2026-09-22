"""Smoke: images are accepted, become a scanned PDF, and are only charged when readable.

A photo or scan of a document (.png .jpg .jpeg .gif .bmp .tif .tiff .webp)
is converted to a PDF page at its real size and analysed on the scanned-
document path. Pinned here:

  * every image type is accepted and analysed as a PDF, with an ``intake``
    record saying what was done and whether OCR is on (a 3-frame TIFF is a
    3-page PDF); the scan is flagged SCANNED_DOCUMENT_NO_TEXT;
  * the conversion is lossless where it matters: a plain JPEG is embedded
    byte for byte, EXIF rotation is applied, transparency is flattened onto
    white, and a 100-dpi letter scan becomes a 612 x 792 pt page;
  * OCR OFF + "fix everything": the only thing that would persist is a
    filename-derived title on a PDF around an unreadable picture — not
    clearly better than the image — so nothing is charged, the executions say
    why, and the customer gets their own PNG back (no free conversion);
  * OCR ON (stub engine): the text layer persists, the run is charged at the
    PDF price, the customer gets a PDF, and re-analysis no longer finds a
    scanned document;
  * a bomb-sized image, a damaged image and a mislabelled image are refused
    with sentences a person can act on, never a machine code.

Usage:
    python -m app.devtools.smoke_image_intake
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="508_smoke_images_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/s.db"
os.environ["MATERIALIZED_ROOT"] = str(Path(_TMP) / "materialized")
os.environ.setdefault("APP_SECRET", "x" * 64)
os.environ["SEMANTIC_PROVIDER"] = "heuristic"
os.environ["OCR_ENABLED"] = "false"
for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ.pop(_key, None)

from PIL import Image, ImageDraw  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _png(img: Image.Image, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", **kw)
    return buf.getvalue()


def _scan() -> Image.Image:
    img = Image.new("RGB", (850, 1100), (250, 250, 247))
    d = ImageDraw.Draw(img)
    for y in range(120, 1000, 40):
        d.rectangle([80, y, 760, y + 12], fill=(40, 40, 40))
    return img


class ReadableOcr:
    name = "stub"

    def available(self) -> bool:
        return True

    def recognize(self, image_bytes: bytes):
        from app.services.ocr import OcrPageResult, OcrWord

        words = [OcrWord("ANNUAL", 80, 90, 220, 48), OcrWord("REPORT", 320, 90, 230, 48)]
        x = 80
        for token in "Operations summary for the reporting period scanned original".split():
            words.append(OcrWord(token, x, 220, 16 * len(token), 28))
            x += 16 * len(token) + 12
        return OcrPageResult(width_px=850, height_px=1100, words=words)


def main() -> int:  # noqa: PLR0915
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    from app.db.models import UserRow
    from app.db.session_sqlalchemy import session_scope
    from app.intake import HOLLOW_IMAGE_NOTE
    from app.intake.images import image_to_pdf
    from app.main import app
    from app.services.ocr import set_ocr_provider_for_testing

    tmp = Path(_TMP)
    scan_png = _png(_scan(), dpi=(100, 100))

    jpeg_buf = io.BytesIO()
    Image.new("RGB", (800, 600), (200, 120, 40)).save(jpeg_buf, format="JPEG", quality=90)
    photo_jpg = jpeg_buf.getvalue()

    rot = Image.new("RGB", (800, 600), (10, 120, 200))
    exif = rot.getexif()
    exif[0x0112] = 6  # stored sideways; displays rotated 90 degrees
    rot_buf = io.BytesIO()
    rot.save(rot_buf, format="JPEG", exif=exif.tobytes())
    rotated_jpg = rot_buf.getvalue()

    rgba = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    ImageDraw.Draw(rgba).rectangle([50, 50, 250, 150], fill=(200, 0, 0, 255))
    transparent_png = _png(rgba)

    gif = Image.new("P", (120, 80), 0)
    gif_buf = io.BytesIO()
    gif.save(gif_buf, format="GIF", transparency=0)
    frames = [Image.new("1", (1700, 2200), 1) for _ in range(3)]
    tif_buf = io.BytesIO()
    frames[0].save(tif_buf, format="TIFF", save_all=True, append_images=frames[1:], dpi=(200, 200))
    bmp_buf = io.BytesIO()
    Image.new("RGB", (400, 300), (5, 5, 5)).save(bmp_buf, format="BMP")
    webp_buf = io.BytesIO()
    Image.new("RGB", (400, 300), (5, 90, 5)).save(webp_buf, format="WEBP")

    samples = {
        "scan.png": (scan_png, "image/png", 1),
        "photo.jpg": (photo_jpg, "image/jpeg", 1),
        "photo.jpeg": (photo_jpg, "image/jpeg", 1),
        "icon.gif": (gif_buf.getvalue(), "image/gif", 1),
        "fax.tiff": (tif_buf.getvalue(), "image/tiff", 3),
        "fax.tif": (tif_buf.getvalue(), "image/tiff", 3),
        "pic.bmp": (bmp_buf.getvalue(), "image/bmp", 1),
        "pic.webp": (webp_buf.getvalue(), "image/webp", 1),
    }

    # ---- 1. conversion details (in-process) ------------------------------
    def convert(name: str, data: bytes) -> PdfReader:
        p = tmp / name
        p.write_bytes(data)
        out = p.with_suffix(".pdf")
        image_to_pdf(p, out)
        return PdfReader(str(out))

    rd = convert("scan.png", scan_png)
    mb = rd.pages[0].mediabox
    check("100-dpi letter scan -> 612 x 792 pt page", (round(float(mb.width)), round(float(mb.height))) == (612, 792), str(mb))
    check("no title/author/language is invented by the conversion",
          not (rd.metadata or {}).get("/Title") and "/Lang" not in rd.trailer["/Root"])

    def xobj(reader: PdfReader):
        return reader.pages[0]["/Resources"]["/XObject"]["/Im0"].get_object()

    x = xobj(convert("photo.jpg", photo_jpg))
    check("plain JPEG embedded byte for byte (DCTDecode)", x["/Filter"] == "/DCTDecode" and x._data == photo_jpg)  # noqa: SLF001
    rr_ = convert("rotated.jpg", rotated_jpg)
    rmb = rr_.pages[0].mediabox
    check("EXIF rotation applied (sideways 800x600 JPEG -> portrait page)", float(rmb.height) > float(rmb.width), str(rmb))
    x = xobj(convert("transparent.png", transparent_png))
    check("transparency flattened onto white (RGB, no soft mask)", x["/ColorSpace"] == "/DeviceRGB" and "/SMask" not in x)
    check("3-frame TIFF -> 3 pages", len(convert("fax.tiff", tif_buf.getvalue()).pages) == 3)

    # Pixels are compressed a band at a time; every pixel must survive, for
    # colour, greyscale and 1-bit images whose rows do not end on a byte.
    import zlib

    from app.intake import images as images_mod

    saved_band = images_mod._BAND_BYTES  # noqa: SLF001
    images_mod._BAND_BYTES = 997  # noqa: SLF001 - force many bands
    try:
        for label, im, name in (
            ("colour PNG", Image.effect_noise((413, 257), 90).convert("RGB"), "noise.png"),
            ("greyscale PNG", Image.effect_noise((301, 199), 90).convert("L"), "grey.png"),
            ("1-bit TIFF, odd width", Image.effect_noise((1701, 93), 90).convert("1"), "bits.tiff"),
        ):
            buf = io.BytesIO()
            im.save(buf, format="TIFF" if name.endswith(".tiff") else "PNG")
            x = xobj(convert(name, buf.getvalue()))
            check(f"{label}: every pixel survives the conversion",
                  x["/Filter"] == "/FlateDecode" and zlib.decompress(x._data) == im.tobytes()  # noqa: SLF001
                  and (int(x["/Width"]), int(x["/Height"])) == im.size)
    finally:
        images_mod._BAND_BYTES = saved_band  # noqa: SLF001

    # ---- 2. HTTP: every image type is accepted and analysed -------------
    client = TestClient(app, raise_server_exceptions=False)
    email = "image-smoke@example.com"
    r = client.post("/auth/sign-in", json={"email": email, "displayName": "I", "password": "imagesmokepass1"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['token']}"}

    def set_balance(n: int) -> None:
        with session_scope() as s:
            row = s.execute(select(UserRow).where(UserRow.email == email)).scalars().first()
            row.credits_balance = n

    def balance() -> int:
        return int(client.get("/credits/balance", headers=headers).json()["balance"])

    def post(path: str, name: str, data: bytes, mime: str, ids=None):
        kw = {"files": {"file": (name, data, mime)}, "headers": headers}
        if ids is not None:
            kw["data"] = {"approved_violations": json.dumps(ids), "rejected_violations": "[]"}
        return client.post(path, **kw)

    set_balance(40)
    set_ocr_provider_for_testing(None)
    for name, (data, mime, pages) in samples.items():
        rr = post("/pipeline/analyze", name, data, mime)
        ok = rr.status_code == 200
        body = rr.json() if ok else {}
        intake = body.get("intake") or {}
        check(
            f"analyze {name}: accepted as a {pages}-page PDF with an intake record",
            ok and body["summary"]["sourceFormat"] == "pdf" and body["summary"]["pageCount"] == pages
            and intake.get("converter") == "image-to-pdf" and intake.get("originalFormat") == name.rsplit(".", 1)[1],
            rr.text[:240],
        )
    rep = post("/pipeline/analyze", "scan.png", scan_png, "image/png").json()
    rules = [v["ruleId"] for v in rep["violations"]]
    check("scan is flagged SCANNED_DOCUMENT_NO_TEXT", "SCANNED_DOCUMENT_NO_TEXT" in rules, str(rules))
    check("OCR off is said plainly", rep["intake"]["ocrAvailable"] is False and "not switched on" in rep["intake"]["note"], rep["intake"]["note"])

    # ---- 3. OCR off: nothing readable -> nothing charged, their PNG back --
    # Today the PDF title executor finds nothing to derive a title from on
    # a text-less page, so make it succeed (as any future title source would):
    # the gate must still refuse to sell a titled-but-unreadable picture.
    from app.services.remediators import set_document_title_executor as _title_mod

    named = "board_minutes_march.png"
    rep = post("/pipeline/analyze", named, scan_png, "image/png").json()
    ids = [v["id"] for v in rep["violations"]]
    b0 = balance()
    _real_derive = _title_mod._derive_title_verdict
    _title_mod._derive_title_verdict = lambda tree, target: ("Board minutes, March", "")
    try:
        rr = post("/pipeline/remediate", named, scan_png, "image/png", ids)
    finally:
        _title_mod._derive_title_verdict = _real_derive
    body = rr.json()
    check("OCR off: 200, not charged", rr.status_code == 200 and body["charged"] is False and balance() == b0,
          f"{rr.status_code} {body.get('charged')} {b0}->{balance()}")
    check("OCR off: persistedFixes 0", body.get("persistedFixes") == 0, str(body.get("persistedFixes")))
    check("OCR off: the title is withheld with the reason",
          any(e["actionCode"] == "SET_DOCUMENT_TITLE" and e["status"] == "skipped" and e["notes"] == HOLLOW_IMAGE_NOTE
              for e in body["executions"]), str([(e["actionCode"], e["status"], e["notes"][:60]) for e in body["executions"]]))
    check("OCR off: the customer gets their own image back", body["filename"] == "board_minutes_march-remediated.png", body["filename"])
    dl = client.get(body["downloadUrl"], headers=headers)
    check("OCR off: ...byte for byte (no free conversion)", dl.status_code == 200 and dl.content == scan_png)
    check("OCR off: intake record on the job", (body.get("intake") or {}).get("ocrAvailable") is False)

    # ---- 4. OCR on (stub): the text layer is the fix, charged as a PDF ----
    set_ocr_provider_for_testing(ReadableOcr())
    try:
        rep_on = post("/pipeline/analyze", "scan.png", scan_png, "image/png").json()
        check("OCR on: the note promises text recognition", rep_on["intake"]["ocrAvailable"] is True and "text recognition" in rep_on["intake"]["note"])
        b0 = balance()
        rr = post("/pipeline/remediate", "scan.png", scan_png, "image/png", [v["id"] for v in rep_on["violations"]])
        body = rr.json()
        check("OCR on: charged once at the PDF price", rr.status_code == 200 and body["charged"] is True and b0 - balance() == 5,
              f"{rr.status_code} {b0}->{balance()} {rr.text[:200]}")
        check("OCR on: a PDF comes back", body.get("filename") == "scan-remediated.pdf", str(body.get("filename")))
        dl = client.get(body["downloadUrl"], headers=headers)
        text = "".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(dl.content)).pages)
        check("OCR on: the text is in the file", "ANNUAL" in text, text[:120])
    finally:
        set_ocr_provider_for_testing(None)
    again = post("/pipeline/analyze", "scan-remediated.pdf", dl.content, "application/pdf").json()
    check("OCR on: re-analysis no longer sees a scan", "SCANNED_DOCUMENT_NO_TEXT" not in [v["ruleId"] for v in again["violations"]])

    # ---- 5. refusals are sentences ---------------------------------------
    huge = _png(Image.new("1", (8000, 8000), 1))
    rr = post("/pipeline/analyze", "huge.png", huge, "image/png")
    check("64-megapixel image -> 413 with a resize instruction", rr.status_code == 413 and "megapixels" in rr.json()["detail"], rr.text[:200])
    rr = post("/pipeline/analyze", "broken.png", b"\x89PNG\r\n\x1a\n" + b"garbage" * 50, "image/png")
    check("damaged PNG -> 422 'couldn't read this image'", rr.status_code == 422 and "couldn't read this image" in rr.json()["detail"], rr.text[:200])
    rr = post("/pipeline/analyze", "really-a-jpeg.png", photo_jpg, "image/png")
    check("JPEG named .png -> 400 naming what it really is", rr.status_code == 400 and "really a JPEG image" in rr.json()["detail"], rr.text[:200])
    b0 = balance()
    rr = post("/pipeline/remediate", "broken.png", b"\x89PNG\r\n\x1a\n" + b"garbage" * 50, "image/png", [])
    check("damaged image at remediate -> 422, not charged", rr.status_code == 422 and balance() == b0, rr.text[:200])

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
