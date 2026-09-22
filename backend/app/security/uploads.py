"""Streaming upload helpers.

Goal: stop calling ``await UploadFile.read()`` blindly — that pulls the
entire request body into memory before we can decide whether the caller
exceeded ``settings.max_upload_bytes``.  Instead we copy the body into a
``SpooledTemporaryFile`` chunk-by-chunk and abort with a 413 the moment we
cross the limit.

Also performs a magic-byte sniff so that a request claiming to upload a
``.pdf`` actually starts with ``%PDF-``, etc.  This is best-effort — the
parsers themselves still validate — but it cuts off the obvious shape
mismatches early.
"""

from __future__ import annotations

import logging
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

logger = logging.getLogger(__name__)


# Maps the declared file extension to a tuple of acceptable magic byte
# prefixes.  DOCX/PPTX are ZIP-based (PK\x03\x04 / PK\x05\x06 / PK\x07\x08).
# PDF is "%PDF-".  Anything else we leave untyped.
_MAGIC_PREFIXES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".pptx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
}

_DEFAULT_CHUNK = 64 * 1024

# Cap the total *uncompressed* size of an OOXML (docx/pptx) package to defend
# against zip bombs — a tiny upload can otherwise expand to gigabytes when a
# parser reads it.
_MAX_OOXML_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MiB


@dataclass(frozen=True)
class UploadResult:
    path: Path
    size: int
    sniffed_kind: Optional[str]  # "pdf" / "docx-or-pptx" / None


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
        raise HTTPException(status_code=500, detail="upload_io_failure")

    if written == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="empty_upload")

    sniffed = _sniff_kind(head)

    if expected_suffix:
        prefixes = _MAGIC_PREFIXES.get(expected_suffix.lower())
        if prefixes and not any(head.startswith(p) for p in prefixes):
            tmp_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=400,
                detail=(
                    f"file_content_mismatch: declared {expected_suffix} but "
                    "magic bytes do not match"
                ),
            )
        # ZIP-based OOXML: confirm it is a real docx/pptx package and not a
        # decompression bomb before any parser opens it.
        if expected_suffix.lower() in {".docx", ".pptx"}:
            try:
                validate_ooxml_package(tmp_path)
            except HTTPException:
                tmp_path.unlink(missing_ok=True)
                raise

    return UploadResult(path=tmp_path, size=written, sniffed_kind=sniffed)


def validate_ooxml_package(path: Path) -> None:
    """Validate that ``path`` is a real OOXML (docx/pptx) ZIP package and not a
    decompression bomb. Raises ``HTTPException`` (400/413) otherwise.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            if "[Content_Types].xml" not in zf.namelist():
                raise HTTPException(
                    status_code=400,
                    detail="invalid_ooxml: missing [Content_Types].xml",
                )
            total = sum(int(info.file_size) for info in zf.infolist())
            if total > _MAX_OOXML_UNCOMPRESSED_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail="upload_rejected: decompressed size exceeds limit",
                )
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="invalid_ooxml: not a valid zip archive")


def _sniff_kind(head: bytes) -> Optional[str]:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        # Could be docx, pptx, xlsx, or any zip — caller should narrow by
        # declared suffix if it cares.
        return "zip"
    return None


__all__ = ["stream_to_tempfile", "UploadResult", "validate_ooxml_package"]
