"""Smoke: we never write placeholder alt text and call it a fix.

The heuristic alt provider — the fallback when no AI key is configured, or
when the per-job AI cost cap trips partway through a large document — emits
"Image page-3-img2 shown in page 3." when it has no caption or nearby text to
derive a description from. That string names WHERE the image is, not WHAT it
shows. It was being written into the customer's file, reported as SUCCESS,
and credited — and it slipped past our own non-descriptive-alt check, so a
re-audit of our own output called it fine. On a large image-heavy PDF the
$0.50 cap meant most images got exactly this.

Pinned here, driving the REAL engine (detect -> plan -> execute):
  * is_nondescriptive_alt recognizes the location-only shape (and still
    leaves real short descriptions and caption-derived alts alone)
  * with the heuristic provider and NO caption, the alt executor SKIPS with a
    reason naming why, and the node's alt stays empty
  * with the heuristic provider and a caption, it still succeeds (the
    caption-derived alt is a genuine description)
  * a whole-document run credits ZERO alt fixes for caption-less images

Usage:
    python -m app.devtools.smoke_alt_placeholder_refused
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_altph_')}/s.db")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["SEMANTIC_PROVIDER"] = "heuristic"

from app.analyzers.image_analyzer import is_nondescriptive_alt  # noqa: E402
from app.models.accessibility import (  # noqa: E402
    AccessibilityTree,
    ActionCode,
    ContentKind,
    DocumentNode,
    ImageNode,
    NodeContent,
    NodeMetadata,
)
from app.services.remediation_engine import RemediationEngine  # noqa: E402
from app.services.remediation_planner import RemediationPolicy  # noqa: E402
from app.services.remediators.base import ExecutionStatus  # noqa: E402

# The APPLY policy /pipeline/remediate uses once the user has approved fixes.
# The default is the conservative preview policy, which routes alt-text to
# manual review before an executor ever runs.
_APPLY_POLICY = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)


def _img(node_id: str, page: int, caption: str | None = None) -> ImageNode:
    props = {"xobject": "Im0"}
    if caption:
        props["caption"] = caption
    return ImageNode(
        id=node_id,
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="pdf", page=page, properties=props),
        children=[],
        accessibility_flags=[],
        alt_text=None,
        is_decorative=False,
    )


def _tree(*images: ImageNode) -> AccessibilityTree:
    root = DocumentNode(
        id="doc",
        content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="pdf", language="en", properties={"title": "T"}),
        children=list(images),
        accessibility_flags=[],
    )
    return AccessibilityTree(root=root)


def _alt_results(tree: AccessibilityTree) -> dict:
    """Run the real engine and return GENERATE_ALT_TEXT results by target id."""
    eng = RemediationEngine(policy=_APPLY_POLICY)
    eng.detect_violations(tree)
    results = eng.execute(tree)
    return {r.target_node_id: r for r in results if r.action_code == ActionCode.GENERATE_ALT_TEXT}


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- classifier ------------------------------------------------------
    for s in ("Image page-1-img3 shown in page 1.", "Image slide-4-img1 shown in slide 4.", "Picture on slide 3",
              # The old heuristic's "<node id> — <nearby text>" shape: an
              # internal id no reader can use, then whatever text sat nearby.
              "Image page-1-img3 — Quarterly revenue chart", "Uploaded image shown in image."):
        check(f"location-only placeholder is non-descriptive: {s!r}", is_nondescriptive_alt(s))
    for s in ("Red car", "Map of Europe", "Photo of the team on page 2 of the brochure",
              "Quarterly revenue chart", "Graphic of sales by region"):
        check(f"a real description is NOT flagged: {s!r}", not is_nondescriptive_alt(s))

    # ---- no caption -> refuse ---------------------------------------------
    bare = _img("page-1-img1", 1)
    res = _alt_results(_tree(bare)).get(bare.id)
    check("heuristic + no caption: the engine SKIPS rather than writing the placeholder",
          res is not None and res.status == ExecutionStatus.SKIPPED,
          f"{getattr(res, 'status', None)} {getattr(res, 'notes', None)}")
    check("...the node's alt text is left EMPTY (stays in manual review)", not bare.alt_text, repr(bare.alt_text))
    check("...and the note says why, in plain words",
          res is not None and "caption" in (res.notes or "").lower() and "not charged" in (res.notes or "").lower(),
          getattr(res, "notes", None))

    # ---- caption -> a real, caption-derived alt is fine --------------------
    # A PDF parser records no caption source, so only a caption that carries
    # its own figure label counts as written FOR the picture.
    cap = _img("page-2-img1", 2, caption="Figure 2: Quarterly revenue by region")
    res2 = _alt_results(_tree(cap)).get(cap.id)
    check("heuristic + caption: the engine SUCCEEDS with a caption-derived alt",
          res2 is not None and res2.status == ExecutionStatus.SUCCESS and bool(cap.alt_text),
          f"{getattr(res2, 'status', None)} {cap.alt_text!r}")
    check("...and that alt is not itself non-descriptive",
          cap.alt_text is not None and not is_nondescriptive_alt(cap.alt_text), repr(cap.alt_text))
    check("...and it is the caption's words, without the label", cap.alt_text == "Quarterly revenue by region",
          repr(cap.alt_text))
    unlabeled = _img("page-3-img1", 3, caption="Quarterly revenue by region")
    res3 = _alt_results(_tree(unlabeled)).get(unlabeled.id)
    check("heuristic + unlabeled nearby text of unknown origin: refused, not guessed",
          res3 is not None and res3.status == ExecutionStatus.SKIPPED and not unlabeled.alt_text,
          f"{getattr(res3, 'status', None)} {unlabeled.alt_text!r}")

    # ---- whole-document: only real descriptions are credited ---------------
    imgs = [_img(f"page-{i}-img1", i) for i in range(1, 6)] + [_img("page-6-img1", 6, caption="Figure 6: Org chart")]
    results = _alt_results(_tree(*imgs))
    ok = sum(1 for r in results.values() if r.status == ExecutionStatus.SUCCESS)
    check("document with 5 caption-less + 1 captioned image credits exactly ONE alt fix", ok == 1,
          str({k: v.status.value for k, v in results.items()}))
    check("no caption-less image received a placeholder", all(not im.alt_text for im in imgs[:5]),
          str([im.alt_text for im in imgs[:5]]))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
