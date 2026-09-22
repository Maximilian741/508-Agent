"""Streaming upload helpers.

Goal: stop calling ``await UploadFile.read()`` blindly — that pulls the
entire request body into memory before we can decide whether the caller
exceeded ``settings.max_upload_bytes``.  Instead we copy the body into a
``SpooledTemporaryFile`` chunk-by-chunk and abort with a 413 the moment we
cross the limit.

Also checks that the bytes are what the name says: a ``.pdf`` starts with
``%PDF-``, a ``.png`` with the PNG signature, a ``.docx`` is a real Word
package (not a workbook renamed, not a password-protected file, not a zip
bomb), and so on for every type the pipeline accepts. The parsers still
validate; this cuts off the obvious mismatches early, before any parser or
converter touches the file.

Every ``detail`` raised here is a sentence a customer reads verbatim — the
UI shows it as-is — so it says what is wrong and what to do about it, never
a machine code.
"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)


_ZIP = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
# OLE2 compound file: legacy .doc/.xls/.ppt — and ALSO what a password-
# protected .docx/.xlsx/.pptx really is (an encrypted package inside OLE2).
_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Declared extension -> acceptable magic prefixes. HTML is text and has no
# signature; it is left untyped.
_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".docx": _ZIP,
    ".pptx": _ZIP,
    ".xlsx": _ZIP,
    ".odt": _ZIP,
    ".ods": _ZIP,
    ".odp": _ZIP,
    ".doc": (_OLE2,),
    ".xls": (_OLE2,),
    ".ppt": (_OLE2,),
    ".rtf": (b"{\\rtf",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".bmp": (b"BM",),
    ".tif": (b"II*\x00", b"MM\x00*"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
}


def _is_webp(head: bytes) -> bool:
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"


_CUSTOM_MAGIC: Dict[str, Callable[[bytes], bool]] = {".webp": _is_webp}

# What each extension is called in a sentence.
_KIND_NAME = {
    ".pdf": "a PDF",
    ".docx": "a Word document",
    ".doc": "a Word document",
    ".rtf": "a Rich Text document",
    ".odt": "an OpenDocument text file",
    ".pptx": "a PowerPoint presentation",
    ".ppt": "a PowerPoint presentation",
    ".odp": "an OpenDocument presentation",
    ".xlsx": "an Excel workbook",
    ".xls": "an Excel workbook",
    ".ods": "an OpenDocument spreadsheet",
    ".png": "a PNG image",
    ".jpg": "a JPEG image",
    ".jpeg": "a JPEG image",
    ".gif": "a GIF image",
    ".bmp": "a BMP image",
    ".tif": "a TIFF image",
    ".tiff": "a TIFF image",
    ".webp": "a WebP image",
}

_OOXML = {".docx", ".pptx", ".xlsx"}
_ODF_MIMETYPES = {
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}

_DEFAULT_CHUNK = 64 * 1024

# Cap the total *uncompressed* size of a zip-based upload (docx/pptx/xlsx and
# the OpenDocument formats) against zip bombs — a tiny upload can otherwise
# expand to gigabytes when a parser reads it.
_MAX_OOXML_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MiB


@dataclass(frozen=True)
class UploadResult:
    path: Path
    size: int
    sniffed_kind: Optional[str]  # "pdf" / "zip" / "ole2" / image kind / None


def _kind(suffix: str) -> str:
    return _KIND_NAME.get(suffix.lower(), f"a {suffix.lstrip('.').upper()} file")


def _mismatch_message(suffix: str, head: bytes) -> str:
    """Explain a signature mismatch in terms of what the bytes actually are."""
    s = suffix.lower()
    if s in _OOXML and head.startswith(_OLE2):
        return (
            f"This {s} file is password-protected (or it is an older Office file renamed to {s}). "
            "Remove the password in Office (File > Info > Protect), save it again, and upload that copy."
        )
    actual = _sniff_kind(head)
    names = {
        "pdf": "a PDF",
        "zip": "a zip archive or an Office file",
        "ole2": "an older Office file (.doc, .xls or .ppt)",
        "png": "a PNG image",
        "jpeg": "a JPEG image",
        "gif": "a GIF image",
        "bmp": "a BMP image",
        "tiff": "a TIFF image",
        "webp": "a WebP image",
        "rtf": "a Rich Text document",
    }
    if actual in names:
        return (
            f"This file is named {s} but it is really {names[actual]}. "
            "Give it the right extension and upload it again."
        )
    return (
        f"This file is named {s} but its contents are not {_kind(s)}. "
        "It may be damaged, or it may have the wrong extension."
    )


async def stream_to_tempfile(
    upload: UploadFile,
    max_bytes: int,
    *,
    expected_suffix: Optional[str] = None,
    chunk_size: int = _DEFAULT_CHUNK,
) -> UploadResult:
    """Stream ``upload`` to a tempfile on disk and return its path + size.

    Raises ``HTTPException(413)`` when the body crosses ``max_bytes``.
    Raises ``HTTPException(400)`` when ``expected_suffix`` is given and the
    sniffed magic bytes don't match the declared extension.
    """

    if max_bytes <= 0:
        raise HTTPException(status_code=500, detail="invalid max_upload_bytes")

    # Use a real on-disk path with the right suffix so downstream parsers
    # that key off ``Path.suffix`` still work.  ``SpooledTemporaryFile``
    # doesn't expose ``.name`` reliably, so we go straight to NamedTemp.
    suffix = (expected_suffix or "").lower()
    fd_obj = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = Path(fd_obj.name)

    written = 0
    head = b""
    try:
        while True:
            chunk = await upload.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                fd_obj.close()
                tmp_path.unlink(missing_ok=True)
                # A user-facing sentence, not a config key. The UI shows
                # `detail` verbatim, so this is the text a customer reads.
                limit_mb = max(1, max_bytes // (1024 * 1024))
                raise HTTPException(
                    status_code=413,
                    detail=(
                        f"That file is larger than the {limit_mb} MB limit. "
                        "Try compressing it, or split it into parts and audit each one."
                    ),
                )
            if len(head) < 16:
                head = (head + chunk)[:16]
            fd_obj.write(chunk)
        fd_obj.flush()
        fd_obj.close()
    except HTTPException:
        raise
    except Exception as exc:
        try:
            fd_obj.close()
        except Exception:
            pass
        tmp_path.unlink(missing_ok=True)
        logger.exception("stream_to_tempfile failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="We could not receive that file. Please try uploading it again.",
        )

    if written == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="That file is empty (0 bytes). Check that it saved correctly and upload it again.")

    sniffed = _sniff_kind(head)

    if expected_suffix:
        try:
            validate_signature(tmp_path, expected_suffix, head)
        except HTTPException:
            tmp_path.unlink(missing_ok=True)
            raise

    return UploadResult(path=tmp_path, size=written, sniffed_kind=sniffed)


def validate_signature(path: Path, suffix: str, head: Optional[bytes] = None) -> None:
    """Raise a friendly 400/413 unless ``path`` really is a ``suffix`` file."""
    s = (suffix or "").lower()
    if head is None:
        with open(path, "rb") as fh:
            head = fh.read(16)
    check = _CUSTOM_MAGIC.get(s)
    prefixes = _MAGIC_PREFIXES.get(s)
    if check is not None and not check(head):
        raise HTTPException(status_code=400, detail=_mismatch_message(s, head))
    if prefixes and not any(head.startswith(p) for p in prefixes):
        raise HTTPException(status_code=400, detail=_mismatch_message(s, head))
    # Zip-based formats: confirm it is the package it claims to be, and not
    # a decompression bomb, before any parser or converter opens it.
    if s in _OOXML:
        validate_ooxml_package(path, expected_suffix=s)
    elif s in _ODF_MIMETYPES:
        validate_odf_package(path, s)


# Main-part content types by the family a parser can open.
_OOXML_FAMILY_MARKERS = {
    ".docx": ("wordprocessingml.document.main", "wordprocessingml.template.main", "ms-word.document", "ms-word.template"),
    ".xlsx": ("spreadsheetml.sheet.main", "spreadsheetml.template.main", "ms-excel.sheet", "ms-excel.template"),
    ".pptx": (
        "presentationml.presentation.main",
        "presentationml.slideshow.main",
        "presentationml.template.main",
        "ms-powerpoint.presentation",
        "ms-powerpoint.slideshow",
        "ms-powerpoint.template",
    ),
}


def _ooxml_family(content_types_xml: bytes) -> Optional[str]:
    try:
        from lxml import etree

        root = etree.fromstring(
            content_types_xml, parser=etree.XMLParser(resolve_entities=False, no_network=True)
        )
    except Exception:
        return None
    types = [(el.get("ContentType") or "").lower() for el in root]
    for suffix, markers in _OOXML_FAMILY_MARKERS.items():
        if any(m in ct for ct in types for m in markers):
            return suffix
    return None


def _zip_bomb_guard(zf: zipfile.ZipFile) -> None:
    total = sum(int(info.file_size) for info in zf.infolist())
    if total > _MAX_OOXML_UNCOMPRESSED_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "That file expands to more than 500 MB when opened, which is more than we can "
                "process. Split it into smaller files and upload each one."
            ),
        )


def validate_ooxml_package(path: Path, expected_suffix: Optional[str] = None) -> None:
    """Validate that ``path`` is a real OOXML (docx/pptx/xlsx) ZIP package and
    not a decompression bomb. With ``expected_suffix``, also that it is the
    RIGHT kind of package (a workbook renamed .docx is caught here with a
    sentence saying so, instead of a parser error later). Raises
    ``HTTPException`` (400/413) otherwise.
    """
    s = (expected_suffix or "").lower()
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "[Content_Types].xml" not in names:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"This is a zip file, not {_kind(s) if s else 'an Office document'}. "
                        "Open the original in Office, save it again, and upload that copy."
                    ),
                )
            _zip_bomb_guard(zf)
            if s in _OOXML_FAMILY_MARKERS:
                family = _ooxml_family(zf.read("[Content_Types].xml"))
                if family is None:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"This file is named {s} but it is not {_kind(s)} we can open. "
                            "Open it in Office, save it again, and upload that copy."
                        ),
                    )
                if family != s:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"This file is named {s} but it is really {_kind(family)}. "
                            f"Rename it to end in {family} and upload it again."
                        ),
                    )
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This {s or 'Office'} file is damaged (it is not a complete zip package). "
                "Open it in Office, save it again, and upload that copy."
            ),
        )


def validate_odf_package(path: Path, suffix: str) -> None:
    """An OpenDocument file is a zip whose ``mimetype`` member names its kind."""
    s = suffix.lower()
    want = _ODF_MIMETYPES.get(s)
    try:
        with zipfile.ZipFile(path) as zf:
            try:
                mimetype = zf.read("mimetype").decode("ascii", "replace").strip()
            except KeyError:
                mimetype = ""
            _zip_bomb_guard(zf)
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=400,
            detail=f"This {s} file is damaged. Open it in LibreOffice, save it again, and upload that copy.",
        )
    if want and mimetype != want:
        actual = next((ext for ext, m in _ODF_MIMETYPES.items() if m == mimetype), None)
        if actual:
            raise HTTPException(
                status_code=400,
                detail=f"This file is named {s} but it is really {_kind(actual)}. Rename it to end in {actual} and upload it again.",
            )
        raise HTTPException(
            status_code=400,
            detail=f"This file is named {s} but it is not {_kind(s)}. It may be damaged, or have the wrong extension.",
        )


def _sniff_kind(head: bytes) -> Optional[str]:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if any(head.startswith(p) for p in _ZIP):
        # Could be docx, pptx, xlsx, OpenDocument, or any zip — caller should
        # narrow by declared suffix if it cares.
        return "zip"
    if head.startswith(_OLE2):
        return "ole2"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if _is_webp(head):
        return "webp"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"BM"):
        return "bmp"
    return None


__all__ = [
    "stream_to_tempfile",
    "UploadResult",
    "validate_ooxml_package",
    "validate_odf_package",
    "validate_signature",
]
