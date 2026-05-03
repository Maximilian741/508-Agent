"""Writers that materialize :class:`AccessibilityTree` mutations back to source files.

Each format-specific writer is responsible for taking a *source* document (the
file that was originally parsed) and a (mutated) :class:`AccessibilityTree`
and producing a remediated copy.  Writers MUST never edit the source file
in-place — they always copy first and write to ``output_path``.

The shared signature is::

    def write_remediated_<fmt>(
        source_path: Path,
        tree: AccessibilityTree,
        output_path: Path,
    ) -> dict:
        # returns { 'applied': [...], 'skipped': [...] }

Writers are defensive — they log via :mod:`logging` and return entries in
``skipped`` for anything that couldn't be applied, rather than raising.
"""

from pathlib import Path
from typing import Any, Dict

from app.models.accessibility import AccessibilityTree
from app.writers.docx_writer import write_remediated_docx
from app.writers.pdf_writer import write_remediated_pdf
from app.writers.pptx_writer import write_remediated_pptx

__all__ = [
    "write_remediated_docx",
    "write_remediated_pdf",
    "write_remediated_pptx",
    "write_remediated",
]


def write_remediated(
    source_path: Path,
    tree: AccessibilityTree,
    output_path: Path,
    *,
    source_format: str | None = None,
) -> Dict[str, Any]:
    """Format-detecting facade.

    Routes to the format-specific writer based on the source extension or an
    explicit ``source_format`` (one of ``pdf`` / ``docx`` / ``pptx``).
    """

    fmt = (source_format or source_path.suffix.lstrip(".")).lower()
    if fmt == "pdf":
        return write_remediated_pdf(source_path, tree, output_path)
    if fmt == "docx":
        return write_remediated_docx(source_path, tree, output_path)
    if fmt == "pptx":
        return write_remediated_pptx(source_path, tree, output_path)
    return {
        "applied": [],
        "skipped": [
            {"target_id": str(source_path), "reason": f"unsupported_format: {fmt!r}"}
        ],
    }
