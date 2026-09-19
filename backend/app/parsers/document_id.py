"""How a parsed document gets its ``document_id``.

Every parser used to do ``path.stem or "doc"``, which made the id the *caller's
uploaded filename*. That is fine as a human-readable LABEL — it is what the
dashboard shows and what de-duplicates a re-analysis of the same file — but it
is attacker-controlled, so nothing downstream may ever treat it as proof of who
owns the document. Two rules follow, and both are enforced elsewhere:

  * the id is never a secret and never an authorization key — every read and
    write of per-document state is scoped by the authenticated owner (see
    ``manual_review.owner_id``); and
  * the id is never a path, a token, or unbounded — that is this module's job.

``derive_document_id`` strips the character classes that let a filename act as
something other than a label (path separators, control characters, the Windows
reserved set, leading dots) and caps the length so it fits the 128-char columns
that store it. Ordinary names — ``Quarterly_Budget_Review``, ``rapport-2026``,
non-ASCII titles — come through unchanged, so nothing about the dashboard's
``analysis_results`` de-duplication changes.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

# Anything that would let the id escape "a label": path separators, the
# Windows-reserved punctuation, and quoting characters.
_FORBIDDEN = set('/\\:*?"<>|')

# analysis_results.document_id / manual_review.doc_id are String(128); leave
# room for the "mr-"/owner prefixes callers build on top of it.
MAX_DOCUMENT_ID_LEN = 96

FALLBACK_DOCUMENT_ID = "doc"


def sanitize_document_id(raw: str) -> str:
    """Reduce ``raw`` to a safe, bounded label. Never raises."""

    cleaned: list[str] = []
    for ch in str(raw or ""):
        if ch in _FORBIDDEN:
            continue
        # Control and format characters (including the RTL overrides that make
        # a filename render as something it is not) have no place in an id.
        if unicodedata.category(ch) in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            continue
        cleaned.append(" " if ch.isspace() else ch)

    # Collapse runs of whitespace, then trim leading dots so the id can never
    # look like "." / ".." / a dotfile.
    label = " ".join("".join(cleaned).split()).lstrip(". ").strip()
    label = label[:MAX_DOCUMENT_ID_LEN].strip()
    return label or FALLBACK_DOCUMENT_ID


def derive_document_id(path: Path | str) -> str:
    """The ``document_id`` a parser reports for the file at ``path``."""

    return sanitize_document_id(Path(path).stem)
