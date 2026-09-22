"""Image -> PDF, so a picture of a document can take the scanned-PDF path.

Each image (each frame of a multi-page TIFF) becomes one PDF page drawn edge
to edge at its real size, which is the shape the scanned-document analyzer
recognizes and the OCR writer expects (it maps recognized words from image
pixels to the full page).

Lossless on purpose. A baseline RGB or greyscale JPEG is embedded byte for
byte (DCTDecode, no re-encode); everything else is stored as Flate-compressed
pixels. Pillow's own PDF writer re-encodes RGB as JPEG, which smears the
small text in a screenshot — exactly what OCR then has to read.

No title, author or language is written: the conversion must not claim
anything about the document that the image does not say. Those stay
findings for the pipeline to report (and, where it can, fix).
"""

from __future__ import annotations

import zlib
from pathlib import Path

from fastapi import HTTPException

# Bounds that keep one upload from exhausting a worker. Each is disclosed to
# the customer as a sentence, never silently applied.
MAX_PIXELS_PER_PAGE = 60_000_000          # ~ an A3 page scanned at 600 dpi
MAX_TOTAL_PIXELS = 150_000_000          # pages stay compressed in memory until written
MAX_PAGES = 100
_DEFAULT_DPI = 150.0
_MAX_PAGE_SIDE_PT = 17 * 72                # anything bigger is scaled to...
_SCALED_PAGE_SIDE_PT = 11 * 72             # ...an 11-inch long side


def _unreadable() -> HTTPException:
    return HTTPException(
        status_code=422,
        detail=(
            "We couldn't read this image. It may be damaged, or saved in a variant we don't "
            "support. Save it as PNG or JPG and upload it again."
        ),
    )


def _dpi(info: dict) -> float:
    raw = info.get("dpi")
    try:
        x = float(raw[0]) if isinstance(raw, (tuple, list)) else float(raw)
    except (TypeError, ValueError, IndexError):
        return _DEFAULT_DPI
    return x if 30.0 <= x <= 2400.0 else _DEFAULT_DPI


def _orientation(img) -> int:
    try:
        return int(img.getexif().get(0x0112) or 1)
    except Exception:
        return 1


def _normalize(frame):
    """Return (image, colorspace, bits) in a mode a PDF image can carry."""
    from PIL import Image

    mode = frame.mode
    if mode == "1":
        return frame, "/DeviceGray", 1
    if mode == "L":
        return frame, "/DeviceGray", 8
    if mode in ("I;16", "I;16B", "I;16L", "I"):
        return frame.convert("I").point(lambda v: v * (1.0 / 256.0)).convert("L"), "/DeviceGray", 8
    if mode == "F":
        return frame.point(lambda v: v * 255.0).convert("L"), "/DeviceGray", 8
    if mode in ("RGBA", "LA", "PA") or (mode == "P" and "transparency" in frame.info):
        rgba = frame.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat, "/DeviceRGB", 8
    if mode != "RGB":
        return frame.convert("RGB"), "/DeviceRGB", 8
    return frame, "/DeviceRGB", 8


def image_to_pdf(src: Path, dest: Path) -> int:
    """Write ``src`` as a PDF at ``dest``; return the number of pages."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
        StreamObject,
    )

    try:
        img = Image.open(str(src))
    except Image.DecompressionBombError:
        raise HTTPException(
            status_code=413,
            detail=(
                "That image is too large to process. Resize it to under "
                f"{MAX_PIXELS_PER_PAGE // 1_000_000} megapixels and upload it again."
            ),
        )
    except (UnidentifiedImageError, OSError, ValueError):
        raise _unreadable()

    writer = PdfWriter()
    total = 0
    with img:
        fmt = (img.format or "").upper()
        # Only TIFF frames are pages. An animated GIF/WebP is one picture.
        frames = int(getattr(img, "n_frames", 1) or 1) if fmt == "TIFF" else 1
        if frames > MAX_PAGES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"That TIFF has {frames} pages; we can take up to {MAX_PAGES} at a time. "
                    "Split it into smaller files and upload each one."
                ),
            )
        for i in range(frames):
            try:
                img.seek(i)
            except EOFError:
                break
            w, h = img.size
            if w <= 0 or h <= 0:
                raise _unreadable()
            if w * h > MAX_PIXELS_PER_PAGE:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"That image is too large to process ({w} x {h} pixels). Resize it to under "
                        f"{MAX_PIXELS_PER_PAGE // 1_000_000} megapixels and upload it again."
                    ),
                )
            total += w * h
            if total > MAX_TOTAL_PIXELS:
                raise HTTPException(
                    status_code=413,
                    detail="Those images are too large to process together. Split the file and upload the parts.",
                )
            dpi = _dpi(img.info)
            orientation = _orientation(img)
            # Byte-for-byte only for a plain RGB/greyscale JPEG that needs no
            # rotation (progressive is fine in PDF). CMYK JPEGs are often
            # stored inverted (Adobe), so they are decoded and re-stored.
            passthrough = fmt == "JPEG" and frames == 1 and orientation == 1 and img.mode in ("RGB", "L")
            try:
                if passthrough:
                    data = Path(src).read_bytes()
                    colorspace = "/DeviceRGB" if img.mode == "RGB" else "/DeviceGray"
                    bits = 8
                    filt = "/DCTDecode"
                    pw, ph = w, h
                else:
                    frame = img.copy()
                    if orientation != 1:
                        frame = ImageOps.exif_transpose(frame)
                    frame, colorspace, bits = _normalize(frame)
                    pw, ph = frame.size
                    data = zlib.compress(frame.tobytes(), 6)
                    filt = "/FlateDecode"
            except HTTPException:
                raise
            except Exception:
                raise _unreadable()

            page_w = pw * 72.0 / dpi
            page_h = ph * 72.0 / dpi
            longest = max(page_w, page_h)
            if longest > _MAX_PAGE_SIDE_PT:
                scale = _SCALED_PAGE_SIDE_PT / longest
                page_w *= scale
                page_h *= scale
            page_w = max(page_w, 36.0)
            page_h = max(page_h, 36.0)

            page = writer.add_blank_page(width=page_w, height=page_h)
            xobj = StreamObject()
            xobj._data = data  # noqa: SLF001 - already encoded with ``filt``
            xobj.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(pw),
                    NameObject("/Height"): NumberObject(ph),
                    NameObject("/ColorSpace"): NameObject(colorspace),
                    NameObject("/BitsPerComponent"): NumberObject(bits),
                    NameObject("/Filter"): NameObject(filt),
                }
            )
            ref = writer._add_object(xobj)  # noqa: SLF001
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): ref})}
            )
            content = DecodedStreamObject()
            content.set_data(f"q {page_w:.4f} 0 0 {page_h:.4f} 0 0 cm /Im0 Do Q".encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(content)  # noqa: SLF001

    if len(writer.pages) == 0:
        raise _unreadable()
    tmp = Path(str(dest) + ".tmp")
    with open(tmp, "wb") as fh:
        writer.write(fh)
    tmp.replace(dest)
    return len(writer.pages)


__all__ = ["image_to_pdf", "MAX_PAGES", "MAX_PIXELS_PER_PAGE"]
