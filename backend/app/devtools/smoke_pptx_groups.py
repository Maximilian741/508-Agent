"""Smoke: PPTX images inside GROUP shapes are detected and alt-text aligns.

Grouped pictures were previously invisible to the analyzer (it iterated only
top-level ``slide.shapes``), so a deck with grouped images was falsely reported
as having no missing alt text. This pins the recursive-walk fix AND that the
parser/writer id alignment survives (each image keeps its own alt — no
cross-assignment).

Usage:
    python -m app.devtools.smoke_pptx_groups
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_grp_')}/s.db"

from PIL import Image  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402

from app.models.accessibility import ImageNode, iter_reading_order  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.writers import write_remediated  # noqa: E402


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (16, 16), (0, 128, 0)).save(b, "PNG")
    return b.getvalue()


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp())
    src = tmp / "groups.pptx"

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.shapes.add_picture(io.BytesIO(_png()), Inches(1), Inches(1), Inches(1), Inches(1))  # top-level
    group = slide.shapes.add_group_shape()
    group.shapes.add_picture(io.BytesIO(_png()), Inches(3), Inches(1), Inches(1), Inches(1))  # nested
    prs.save(str(src))

    res = parse_to_tree(str(src))
    imgs = [n for n in iter_reading_order(res.tree.root) if isinstance(n, ImageNode)]
    check("both top-level and grouped images detected (2)", len(imgs) == 2)

    for i, n in enumerate(imgs):
        n.alt_text = f"ALT_{i}"
        n.is_decorative = False
    out = tmp / "out.pptx"
    write_remediated(src, res.tree, out, source_format=res.format)

    res2 = parse_to_tree(str(out))
    alts = {n.alt_text for n in iter_reading_order(res2.tree.root) if isinstance(n, ImageNode)}
    check("each image keeps its own alt (id alignment preserved)", alts == {"ALT_0", "ALT_1"})

    xml = b""
    with zipfile.ZipFile(out) as z:
        for nm in z.namelist():
            if nm.startswith("ppt/slides/slide") and nm.endswith(".xml"):
                xml += z.read(nm)
    check("both descr written to slide XML", b"ALT_0" in xml and b"ALT_1" in xml)

    Presentation(str(out))  # raises if corrupt
    check("output re-opens (not corrupt)", True)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
