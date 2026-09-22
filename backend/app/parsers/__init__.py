"""Document parsers.

Public entry points:

* :class:`PDFParser`, :class:`DOCXParser`, :class:`PPTXParser` — format-specific
  classes; each one exposes a ``parse_to_tree`` method returning a
  :class:`~app.models.accessibility.ParserResult`.
* :func:`parse_to_tree` — format-detecting convenience function.
"""

from __future__ import annotations

from pathlib import Path

from app.models.accessibility import ParserResult
from app.parsers.docx_parser import DOCXParser
from app.parsers.html_parser import HTMLParser
from app.parsers.pdf_parser import PDFParser
from app.parsers.pptx_parser import PPTXParser
from app.parsers.xlsx_parser import XLSXParser

__all__ = ["PDFParser", "DOCXParser", "PPTXParser", "XLSXParser", "HTMLParser", "parse_to_tree"]


def _ext(path: str) -> str:
    return Path(path).suffix.lower().lstrip(".")


def parse_to_tree(file_path: str, *, source_format: str | None = None) -> ParserResult:
    """Parse ``file_path`` to an :class:`AccessibilityTree`.

    The format can be supplied explicitly via ``source_format`` or inferred
    from the extension.  Unsupported formats raise :class:`ValueError`.
    """

    fmt = (source_format or _ext(file_path) or "").lower()
    if fmt == "pdf":
        return PDFParser().parse_to_tree(file_path)
    if fmt == "docx":
        return DOCXParser().parse_to_tree(file_path)
    if fmt == "pptx":
        return PPTXParser().parse_to_tree(file_path)
    if fmt == "xlsx":
        return XLSXParser().parse_to_tree(file_path)
    if fmt in {"html", "htm"}:
        return HTMLParser().parse_to_tree(file_path)
    raise ValueError(f"Unsupported source format: {fmt!r}")
