"""Smoke: expanded ALT_TEXT_NOT_DESCRIPTIVE detection.

is_nondescriptive_alt() now also catches URLs / file paths / data-URIs, raw pixel
dimensions, whole-alt filler ("n/a", "tbd", …), and more role-only words
("logo", "banner", "icon", …) — so more junk alt gets auto-regenerated.

The make-or-break property is ZERO false positives: a true positive triggers
GENERATE_ALT_TEXT to OVERWRITE the existing alt, so flagging a genuinely good
short description would destroy human-written alt. This pins a large negative
set (good alts that must NOT flag) alongside the new positives.

Usage:
    python -m app.devtools.smoke_alt_quality_detection
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_altq_')}/s.db")

from app.analyzers.image_analyzer import NonDescriptiveAltTextAnalyzer, is_nondescriptive_alt  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    AccessibilityFlagCode,
    AccessibilityTree,
    ContentKind,
    DocumentNode,
    ImageNode,
    NodeContent,
    NodeMetadata,
)

# --- alts that SHOULD be flagged (junk that needs a real description) --------
BAD = [
    # filenames (existing)
    "image1.png", "DSC_0042.JPG", "logo.svg", "report final.jpeg",
    # placeholder / role-only words (existing + new)
    "image", "Picture 1", "img_2", "Graphic", "Untitled", "Chart",
    "logo", "Banner", "icon", "Avatar", "Thumbnail", "illustration",
    "Artwork", "clip art", "graphics", "Headshot", "snapshot", "photograph",
    "Logo 2", "Icon #3", "Figure 3",
    # camera defaults (existing)
    "Screenshot 2024", "screen shot 5", "DSCN1234",
    # URLs / data-URI / paths (new)
    "http://example.com/cat.jpg", "https://cdn.site.com/a", "www.example.com/pic",
    "data:image/png;base64,iVBORw0KGgo", "C:\\Users\\me\\Pictures\\hero",
    "/var/www/uploads/file", "../assets/hero",
    # raw dimensions (new)
    "1024x768", "300 x 250", "640×480 px", "12x12",
    # whole-alt filler (new)
    "n/a", "N/A", "none", "NULL", "blank", "TBD", "todo", "test", "temp",
    "asdf", "xxx", "no alt text", "alt text", "description",
]

# --- alts that MUST NOT be flagged (real descriptions — FP would clobber) -----
GOOD = [
    "Red car", "Map of Europe", "CEO portrait", "A red car on a wet street",
    "Q3 revenue grew 12 percent", "Bar chart of sales by region",
    "Company logo: blue circle with the letter A", "Acme Corp logo",
    "Our new logo, a blue swirl", "Photograph of the new campus entrance",
    "Diagram of the request lifecycle", "Screenshot of the dashboard with 3 widgets",
    "Icon indicating a successful save", "Banner reading Grand Opening Saturday",
    "A 1024x768 monitor on a desk", "300 attendees fill the auditorium",
    "10 x 10 grid of coloured tiles", "and/or decision flowchart",
    "Thumbnail showing the cover of the 2025 annual report",
    "Illustration of a cat chasing a laser pointer",
    "Headshot of Dr. Maria Chen, Chief Scientist",
]


def _img_tree(alt: str) -> AccessibilityTree:
    img = ImageNode(
        id="img-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx", properties={}),
        children=[],
        accessibility_flags=[],
        is_decorative=False,
        alt_text=alt or None,
    )
    root = DocumentNode(
        id="doc-1",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="docx", properties={}),
        children=[img],
        accessibility_flags=[],
    )
    return AccessibilityTree(root=root, metadata={}), img


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    # --- pure function ---
    for bad in BAD:
        check(f"BAD  {bad!r} flagged non-descriptive", is_nondescriptive_alt(bad))
    for good in GOOD:
        check(f"GOOD {good!r} NOT flagged", not is_nondescriptive_alt(good))

    # empty alt is the MISSING_ALT_TEXT case, not this one
    check("empty alt is not non-descriptive", not is_nondescriptive_alt(""))
    check("whitespace-only alt is not non-descriptive", not is_nondescriptive_alt("   "))

    # --- analyzer end-to-end (flag attaches for bad, not for good) ---
    analyzer = NonDescriptiveAltTextAnalyzer()

    def flagged(alt: str) -> bool:
        tree, img = _img_tree(alt)
        analyzer.analyze(tree)
        return any(f.code == AccessibilityFlagCode.ALT_TEXT_NOT_DESCRIPTIVE for f in img.accessibility_flags)

    check("analyzer flags 'logo'", flagged("logo"))
    check("analyzer flags a URL alt", flagged("https://x.com/a.png"))
    check("analyzer flags raw dimensions", flagged("1024x768"))
    check("analyzer does NOT flag 'Acme Corp logo'", not flagged("Acme Corp logo"))
    check("analyzer does NOT flag a real description", not flagged("A red car on a wet street"))

    # decorative image with junk alt is handled by the decorative analyzer, not here
    tree, img = _img_tree("logo")
    img.is_decorative = True
    analyzer.analyze(tree)
    check("decorative image is not flagged non-descriptive",
          not any(f.code == AccessibilityFlagCode.ALT_TEXT_NOT_DESCRIPTIVE for f in img.accessibility_flags))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
