"""Smoke: link-text remediation now PERSISTS to the output (DOCX + PPTX).

Previously IMPROVE_LINK_TEXT mutated only the in-memory tree (no writer), so the
fix never reached the downloaded file. Now the DOCX and PPTX writers rewrite the
hyperlink's display text — preserving the link target and formatting — so the
rewrite round-trips.

Usage:
    python -m app.devtools.smoke_link_text_writer
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_link_')}/s.db"

from docx import Document  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Inches  # noqa: E402

from app.models.accessibility import ContentKind, LinkNode, NodeContent, iter_reading_order  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.writers import write_remediated  # noqa: E402

_NEW = "Visit example.com"
_HREF = "https://example.com/very/long/path"


def main() -> int:
    failures = 0

    def check(name: str, cond: bool) -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name)
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp())

    def _links(path: Path):
        res = parse_to_tree(str(path))
        return res, [n for n in iter_reading_order(res.tree.root) if isinstance(n, LinkNode)]

    def _rewrite_and_check(src: Path, fmt: str) -> None:
        res, links = _links(src)
        check(f"{fmt}: hyperlink 'click here' detected", bool(links) and links[0].content.text == "click here")
        if not links:
            return
        links[0].content = NodeContent(kind=ContentKind.TEXT, text=_NEW)
        out = src.with_name(src.stem + "_out" + src.suffix)
        write_remediated(src, res.tree, out, source_format=res.format)
        _, links2 = _links(out)
        check(f"{fmt}: link text rewritten in output", bool(links2) and links2[0].content.text == _NEW)
        check(f"{fmt}: link target preserved", bool(links2) and bool(links2[0].target) and "example.com" in links2[0].target)
        # not corrupt
        (Document if fmt == "docx" else Presentation)(str(out))
        check(f"{fmt}: output reopens cleanly", True)

    # DOCX
    dp = tmp / "in.docx"
    d = Document(); para = d.add_paragraph("See ")
    rid = d.part.relate_to(_HREF, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
    hl = OxmlElement("w:hyperlink"); hl.set(qn("r:id"), rid)
    r = OxmlElement("w:r"); t = OxmlElement("w:t"); t.text = "click here"; r.append(t); hl.append(r)
    para._p.append(hl); d.save(str(dp))
    _rewrite_and_check(dp, "docx")

    # PPTX
    pp = tmp / "in.pptx"
    prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[6])
    tb = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    run = tb.text_frame.paragraphs[0].add_run(); run.text = "click here"
    run.hyperlink.address = _HREF
    prs.save(str(pp))
    _rewrite_and_check(pp, "pptx")

    # --- DOCX multi-link id alignment (3 links get distinct text, no cross-assignment) ---
    md = tmp / "multi.docx"
    d2 = Document()
    for i in range(3):
        para = d2.add_paragraph(f"Item {i}: ")
        rid = d2.part.relate_to(f"https://example.com/{i}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True)
        hl = OxmlElement("w:hyperlink"); hl.set(qn("r:id"), rid)
        r = OxmlElement("w:r"); t = OxmlElement("w:t"); t.text = "click here"; r.append(t); hl.append(r)
        para._p.append(hl)
    d2.save(str(md))
    res, links = _links(md)
    for i, ln in enumerate(links):
        ln.content = NodeContent(kind=ContentKind.TEXT, text=f"LINK-{i}")
    mo = tmp / "multi_out.docx"; write_remediated(md, res.tree, mo, source_format=res.format)
    _, links2 = _links(mo)
    check("docx: 3 links each get their own text (no cross-assignment)", [l.content.text for l in links2] == ["LINK-0", "LINK-1", "LINK-2"])

    # --- PPTX split-run hyperlink: one visual link across 2 same-address runs -> no doubled text ---
    sp = tmp / "split.pptx"
    prs2 = Presentation(); slide2 = prs2.slides.add_slide(prs2.slide_layouts[6])
    tb2 = slide2.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    pp2 = tb2.text_frame.paragraphs[0]
    a = pp2.add_run(); a.text = "click "; a.hyperlink.address = "https://example.com/same"
    b = pp2.add_run(); b.text = "here"; b.hyperlink.address = "https://example.com/same"
    prs2.save(str(sp))
    res, links = _links(sp)
    check("pptx: split-run link coalesced into one node", len(links) == 1 and links[0].content.text == "click here")
    if links:
        links[0].content = NodeContent(kind=ContentKind.TEXT, text=_NEW)
    so = tmp / "split_out.pptx"; write_remediated(sp, res.tree, so, source_format=res.format)
    import zipfile
    sx = b""
    with zipfile.ZipFile(so) as z:
        for nm in z.namelist():
            if nm.startswith("ppt/slides/slide") and nm.endswith(".xml"):
                sx += z.read(nm)
    check("pptx: split-run rewrite not duplicated", sx.count(_NEW.encode()) == 1)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
