"""Per-finding fix guidance for the free URL / whole-site scan.

A live web page cannot be remediated by us — we can't write back to someone
else's site. So the scan's value is telling a developer EXACTLY what to change.
Two sources of guidance, and the distinction is deliberate and surfaced to the
user (``source``):

``writer``
    A real before/after diff produced by the SAME engine that rewrites uploaded
    documents. Only emitted when the writer's own ``applied`` list confirms it
    actually made the change (see ``scan_fixes.derive_scan_fixes``), so a diff
    is never shown for a fix that didn't happen.

``guidance``
    A hand-written pattern for findings the engine deliberately does NOT
    auto-fix (we'd have to invent human content — a link's purpose, an image's
    meaning). These carry no ``before``: they are examples, not diffs, and are
    flagged ``requiresHumanVerification`` so the UI can't present them as
    machine-verified.

Never invent a ``before`` snippet. If we didn't read it off the page, it isn't
shown as one.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# flag code -> guidance shown when the engine can't safely auto-fix it.
_GUIDANCE: Dict[str, Dict[str, str]] = {
    "LINK_NAME_MISSING": {
        "after": '<a href="/menu" aria-label="Open menu"><svg …></svg></a>',
        "note": (
            "Give the link a name describing where it goes or what it does. Use "
            "aria-label on the <a>, real alt text on an icon <img>, or a <title> "
            "inside an inline <svg>. Name the action, not the picture "
            "(“Search”, not “magnifying glass”)."
        ),
    },
    "LINK_TEXT_NON_DESCRIPTIVE": {
        "after": '<a href="/report.pdf">Download the 2025 annual report (PDF)</a>',
        "note": (
            "Replace generic text like “click here” or a bare URL with wording "
            "that makes sense read on its own — many screen reader users browse a "
            "list of every link on the page, out of context."
        ),
    },
    "MISSING_ALT_TEXT": {
        "after": '<img src="chart.png" alt="Bar chart: 2025 revenue by region">',
        "note": (
            "Describe what the image conveys in context. If it is purely "
            "decorative, use alt=\"\" so screen readers skip it. We don't guess "
            "alt text for a live page — upload the document and our vision AI "
            "will draft it for you to review."
        ),
    },
    "ALT_TEXT_NOT_DESCRIPTIVE": {
        "after": '<img src="DSC_0042.jpg" alt="Mayor Chen signing the housing bill">',
        "note": (
            "The current alt text looks like a filename or placeholder. Describe "
            "the content and purpose of the image instead."
        ),
    },
    "HEADING_TEXT_EMPTY": {
        "after": "<h2>Quarterly results</h2>",
        "note": (
            "The heading element has no text, so it creates an empty entry in "
            "the page outline. Give it real text, or remove the element if it "
            "exists only for spacing."
        ),
    },
    "DOCUMENT_NO_HEADINGS": {
        "after": "<h1>Page title</h1>\n<h2>Section</h2>",
        "note": (
            "The page has no headings, so screen reader users can't navigate it "
            "by structure. Add one <h1> for the page topic, then <h2>/<h3> for "
            "sections, stepping one level at a time."
        ),
    },
    "TABLE_CAPTION_MISSING": {
        "after": "<table>\n  <caption>Q3 revenue by region</caption>\n  …\n</table>",
        "note": (
            "Add a <caption> as the table's first child so users know what the "
            "table contains before the row-by-row read-out begins."
        ),
    },
    "LINK_TARGET_BROKEN": {
        "after": '<a href="https://example.gov/current-page">…</a>',
        "note": "Point the link at a working destination, or remove it.",
    },
    "TABLE_NESTED": {
        "after": "<!-- split the inner table out into its own sibling table -->",
        "note": (
            "Nested tables are announced confusingly. Flatten the structure or "
            "split the inner table out."
        ),
    },
    "TABLE_COMPLEX_NEEDS_SUMMARY": {
        "after": "<caption>Budget by department and year. …</caption>",
        "note": (
            "A complex table benefits from a short summary of its layout so "
            "users know how rows and columns relate before navigating it."
        ),
    },
}


def guidance_for(rule_id: str, evidence: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Static, human-verifiable guidance for ``rule_id`` (None when we have none).

    ``LOW_CONTRAST_TEXT`` is special-cased: the analyzer already computed a
    concrete AA-passing colour, so we can hand over a real CSS one-liner rather
    than generic advice.
    """
    ev = evidence or {}
    if rule_id == "LOW_CONTRAST_TEXT":
        suggested = ev.get("suggested_fg") or ev.get("suggestedForeground")
        fg, bg = ev.get("fg") or ev.get("foreground"), ev.get("bg") or ev.get("background")
        ratio = ev.get("ratio") or ev.get("contrast_ratio")
        if suggested:
            detail = f"color: #{str(suggested).lstrip('#')};"
            note = (
                f"Text colour #{str(fg).lstrip('#')} on #{str(bg).lstrip('#')} "
                f"has a contrast ratio of {ratio}:1, below the WCAG AA minimum of 4.5:1. "
                f"#{str(suggested).lstrip('#')} is the nearest shade that passes."
                if fg and bg and ratio
                else "This colour pair is below the WCAG AA minimum of 4.5:1."
            )
            return {
                "source": "guidance",
                "kind": "css",
                "after": detail,
                "note": note,
                "requiresHumanVerification": True,
            }
        return {
            "source": "guidance",
            "kind": "advice",
            "after": None,
            "note": "Increase the contrast between this text and its background to at least 4.5:1 (WCAG AA).",
            "requiresHumanVerification": True,
        }

    entry = _GUIDANCE.get(rule_id)
    if not entry:
        return None
    return {
        "source": "guidance",
        "kind": "advice",
        "after": entry["after"],
        "note": entry["note"],
        "requiresHumanVerification": True,
    }
