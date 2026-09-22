"""Intake: turn whatever the customer uploaded into a format the pipeline fixes.

The pipeline parses, fixes and writes five formats natively: PDF, Word
(.docx), PowerPoint (.pptx), Excel (.xlsx) and HTML. Intake widens the door
without pretending the other formats are native:

* **Images** (.png .jpg .jpeg .gif .bmp .tif .tiff .webp) become a PDF, one
  page per image (per frame of a multi-page TIFF), drawn at their true size.
  That PDF then takes the scanned-document path: when OCR is enabled the fix
  adds a real text layer and tags it; when it is not, we SAY so — the page is
  still a picture of text, and nothing about the conversion is sold as an
  accessibility fix.
* **Legacy and OpenDocument files** (.doc .rtf .odt / .xls .ods / .ppt .odp)
  are converted to .docx / .xlsx / .pptx with LibreOffice when the server has
  it (``soffice``), in a throwaway sandbox with a hard timeout. When it does
  not, the upload is refused with a sentence telling the person exactly how to
  save it as the modern format themselves.

Everything else is refused with a sentence listing what we do accept. The
format a document is PRICED and WRITTEN as is its effective format
(``effective_format``): a .doc is remediated — and charged — as a .docx, and
the customer gets a .docx back.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

NATIVE_FORMATS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".html": "html",
    ".htm": "html",
}
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"})
OFFICE_CONVERSIONS = {
    ".doc": "docx",
    ".rtf": "docx",
    ".odt": "docx",
    ".xls": "xlsx",
    ".ods": "xlsx",
    ".ppt": "pptx",
    ".odp": "pptx",
}

ACCEPTED_SUFFIXES = frozenset(NATIVE_FORMATS) | IMAGE_SUFFIXES | frozenset(OFFICE_CONVERSIONS)

# One sentence, shared by every rejection, so the list can never drift from
# ACCEPTED_SUFFIXES without a test noticing.
ACCEPTED_SENTENCE = (
    "Upload a PDF, a Word file (.docx, .doc, .rtf, .odt), a PowerPoint file (.pptx, .ppt, .odp), "
    "an Excel file (.xlsx, .xls, .ods), a web page (.html), or an image "
    "(.png, .jpg, .gif, .bmp, .tiff, .webp)."
)


class IntakeInfo(BaseModel):
    """What intake did to the upload, for the response. Present only when
    the file was converted; a native upload has no intake record."""

    model_config = ConfigDict(extra="forbid")

    originalFormat: str
    convertedTo: str
    converter: str
    note: str
    # Images only: whether text recognition will run when the file is fixed.
    ocrAvailable: Optional[bool] = None


@dataclass(frozen=True)
class IntakeResult:
    path: Path                     # the file the pipeline parses and writes from
    format: str                    # its effective format (pdf/docx/pptx/xlsx/html)
    original_path: Path            # the bytes the customer uploaded
    original_suffix: str
    converter: Optional[str] = None
    note: Optional[str] = None
    ocr_available: Optional[bool] = None

    @property
    def converted(self) -> bool:
        return self.converter is not None

    def info(self) -> Optional[IntakeInfo]:
        if not self.converted:
            return None
        return IntakeInfo(
            originalFormat=self.original_suffix.lstrip("."),
            convertedTo=self.format,
            converter=self.converter or "",
            note=self.note or "",
            ocrAvailable=self.ocr_available,
        )


def upload_suffix(filename: Optional[str]) -> str:
    """The lower-case extension of an upload we accept, or a friendly 400."""
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    if not suffix:
        raise HTTPException(
            status_code=400,
            detail=(
                "That file has no extension, so we can't tell what kind of document it is. "
                "Rename it with its extension (for example report.pdf) and upload it again."
            ),
        )
    if suffix not in ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"We can't check {suffix} files. {ACCEPTED_SENTENCE}",
        )
    return suffix


def effective_format(suffix: str) -> str:
    """The format a document with this extension is parsed, fixed, written
    and PRICED as. ``.htm`` is ``html`` (the honesty gate is keyed on
    ``html``: keyed on ``htm`` it counted every fix as not persisted and
    handed back the upload unchanged)."""
    s = (suffix or "").lower()
    if s in NATIVE_FORMATS:
        return NATIVE_FORMATS[s]
    if s in IMAGE_SUFFIXES:
        return "pdf"
    if s in OFFICE_CONVERSIONS:
        return OFFICE_CONVERSIONS[s]
    raise HTTPException(status_code=400, detail=f"We can't check {s or 'these'} files. {ACCEPTED_SENTENCE}")


def prepare_upload(path: Path, suffix: str) -> IntakeResult:
    """Convert ``path`` (an upload already signature-checked as ``suffix``)
    into the file the pipeline should parse. Native formats pass through.

    Converted output is written next to ``path`` with the new extension and
    the same stem, so a job directory keeps the customer's filename. Raises
    ``HTTPException`` with a sentence the customer can act on.
    """
    path = Path(path)
    s = (suffix or path.suffix).lower()
    if s in NATIVE_FORMATS:
        return IntakeResult(path=path, format=NATIVE_FORMATS[s], original_path=path, original_suffix=s)
    if s in IMAGE_SUFFIXES:
        from app.intake.images import image_to_pdf

        dest = path.with_suffix(".pdf")
        pages = image_to_pdf(path, dest)
        ocr = _ocr_available()
        what = "a one-page PDF" if pages == 1 else f"a {pages}-page PDF"
        if ocr:
            note = (
                f"We turned your image into {what} so it can be checked and fixed. When you fix it, "
                "we run text recognition (OCR) so a screen reader can read the words in it, and you "
                "get a PDF back."
            )
        else:
            note = (
                f"We turned your image into {what} so it can be checked. Text recognition (OCR) is "
                "not switched on for this service, so any words in the picture are still just pixels "
                "that a screen reader cannot read. If you have the original document (Word, or a PDF "
                "with real text), upload that instead."
            )
        return IntakeResult(
            path=dest,
            format="pdf",
            original_path=path,
            original_suffix=s,
            converter="image-to-pdf",
            note=note,
            ocr_available=ocr,
        )
    if s in OFFICE_CONVERSIONS:
        from app.intake.office import convert_office_file

        target = OFFICE_CONVERSIONS[s]
        dest = path.with_suffix("." + target)
        convert_office_file(path, s, target, dest)
        return IntakeResult(
            path=dest,
            format=target,
            original_path=path,
            original_suffix=s,
            converter="libreoffice",
            note=(
                f"We converted your {s} file to .{target} so we could check and fix it. "
                f"Your fixed file will be a .{target}, which opens in current versions of Office."
            ),
        )
    raise HTTPException(status_code=400, detail=f"We can't check {s} files. {ACCEPTED_SENTENCE}")


# What makes a picture of a document readable. Anything else we might write
# into the converted PDF (a title, a language) leaves it exactly as
# unreadable as the image the customer sent.
_IMAGE_VALUE_ACTIONS = frozenset({"ADD_OCR_TEXT_LAYER", "GENERATE_ALT_TEXT"})

HOLLOW_IMAGE_NOTE = (
    "Not applied: this picture still has no text a screen reader can read (text recognition "
    "did not run or found nothing, and it has no description), so a title or language alone "
    "would not make it accessible. We returned your original file and did not charge you."
)


def withhold_hollow_image_fix(result: Optional[IntakeResult], executions) -> bool:
    """For an image upload: True (and every success rewritten to SKIPPED with
    :data:`HOLLOW_IMAGE_NOTE`) when none of the RECONCILED successes made the
    picture readable. The caller then treats the run as having persisted
    nothing: no charge, original bytes back.

    A PDF wrapper with a filename-derived title around an unreadable scan is
    not clearly better than the image — charging for it would be selling the
    conversion, not a fix.
    """
    if result is None or result.converter != "image-to-pdf":
        return False
    successes = [e for e in executions if getattr(e.status, "value", e.status) == "success"]
    if not successes:
        return False
    if any(e.action_code.value in _IMAGE_VALUE_ACTIONS for e in successes):
        return False
    from app.services.remediators.base import ExecutionStatus

    for e in successes:
        e.status = ExecutionStatus.SKIPPED
        e.notes = HOLLOW_IMAGE_NOTE
    return True


def _ocr_available() -> bool:
    try:
        from app.services.ocr import get_ocr_provider

        return get_ocr_provider() is not None
    except Exception:
        return False


def cleanup(result: Optional[IntakeResult]) -> None:
    """Remove a converted file (the caller owns the original)."""
    if result is None or not result.converted:
        return
    try:
        result.path.unlink(missing_ok=True)
    except Exception:
        pass


__all__ = [
    "ACCEPTED_SUFFIXES",
    "ACCEPTED_SENTENCE",
    "IMAGE_SUFFIXES",
    "NATIVE_FORMATS",
    "OFFICE_CONVERSIONS",
    "IntakeInfo",
    "IntakeResult",
    "cleanup",
    "effective_format",
    "prepare_upload",
    "upload_suffix",
]
