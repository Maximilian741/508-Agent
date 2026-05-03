"""Command-line interface for the 508 Agent backend.

This package provides a headless entry point that runs the same audit
pipeline used by the FastAPI web UI but from the terminal, suitable for
batch processing and CI gates.

Usage::

    python -m app.cli audit path/to/file.docx
    python -m app.cli audit path/to/folder
    python -m app.cli audit file.docx --json out.json
    python -m app.cli audit file.docx --html out.html
    python -m app.cli audit file.docx --apply --output remediated.docx

The implementation lives in :mod:`app.cli.__main__` so that the package can
be invoked with ``python -m app.cli``.  The :func:`main` callable is
re-exported here so library callers (``from app.cli import main``) and
``setuptools`` entry-points can both wire up to the same function.
"""

from __future__ import annotations

from app.cli.__main__ import main

__all__ = ["main"]
