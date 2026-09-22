"""Smoke: a PDF link is named by the words printed under it, and internal
(GoTo) links are not "broken".

Before, the parser read only ``/A /URI``:
  * every link's accessible name was its URI (flagged as a bare URL) or the
    literal "(link)" — the visible link text was never read — so EVERY PDF
    link was reported LINK_TEXT_NON_DESCRIPTIVE;
  * every internal link (``/Dest`` or ``/A /S /GoTo`` — every table of
    contents) had no target and was reported LINK_TARGET_BROKEN: a five-line
    contents page produced ten manual-review items for links that work;
  * the tagger's /Link /Contents fallback was the raw URI.

Now (parser + tagged output read back):
  1. "annual report" under a URI link names it; a TOC GoTo link resolves to
     "#page-N"; a TOC of GoTo links raises no link finding at all;
  2. a GoTo to a named destination that does not exist is still BROKEN, and a
     link with nothing under it, no /Contents and no URI is LINK_NAME_MISSING
     (not "(link)" passed off as a name);
  3. links carry bbox = their /Rect and the page size;
  4. the tagger writes the visible words as the annotation /Contents and puts
     each /Link element right after the text on its line (not at the end of
     the page).

Usage:
    python -m app.devtools.smoke_pdf_links_named_by_page
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.devtools import _pdf_fixture_kit as K

_TMP = K.isolated_env("508_smoke_links_")

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf._text_extraction._layout_mode._font_widths import STANDARD_WIDTHS  # noqa: E402

_W = STANDARD_WIDTHS["Helvetica"]


def width(s: str, size: float = 11) -> float:
    return sum(_W.get(ch, 556) for ch in s) * size / 1000.0


def build() -> bytes:
    w = PdfWriter()
    f = K.helvetica(w)
    c = K.bt("F1", 20, 72, 740, K.lit("Road Salt Program"))
    c += K.bt("F1", 11, 72, 700, K.lit("For details, read the annual report online."))
    c += K.bt("F1", 11, 72, 680, K.lit("See Appendix B"))
    for i in range(5):
        c += K.bt("F1", 11, 72, 640 - 14 * i, K.lit("Crews restock the depots after every storm this winter season."))
    p1 = K.add_page(w, c, {"F1": f})
    p2 = K.add_page(w, K.bt("F1", 16, 72, 720, K.lit("Appendix B")) + K.bt("F1", 11, 72, 690, K.lit("Depot locations and capacities are listed here.")), {"F1": f})
    x0 = 72 + width("For details, read the ")
    uri = K.link_annot(w, (x0, 696, x0 + width("annual report"), 710), uri="https://example.gov/annual-report.pdf")
    goto = K.link_annot(w, (72, 676, 72 + width("See Appendix B"), 690), dest_page=p2.indirect_reference)
    bad = K.link_annot(w, (400, 300, 480, 312), named="no-such-destination")
    p1[K.NameObject("/Annots")] = K.ArrayObject([uri, goto, bad])
    return K.to_bytes(w)


def build_toc() -> bytes:
    w = PdfWriter()
    f = K.helvetica(w)
    entries = [("1. Introduction", 2), ("2. Salt Supply", 2), ("3. Plow Routes", 3), ("4. Budget", 3), ("5. Outlook", 3)]
    c = K.bt("F1", 18, 72, 740, K.lit("Contents"))
    y = 700
    for t, _pg in entries:
        c += K.bt("F1", 11, 72, y, K.lit(t))
        y -= 20
    toc = K.add_page(w, c, {"F1": f})
    pages = [K.add_page(w, K.bt("F1", 16, 72, 720, K.lit(f"Chapter page {i}")) + K.bt("F1", 11, 72, 690, K.lit("Body text for this chapter of the report.")), {"F1": f}) for i in (2, 3)]
    annots = []
    y = 700
    for t, pg in entries:
        annots.append(K.link_annot(w, (72, y - 4, 72 + width(t), y + 10), dest_page=pages[pg - 2].indirect_reference))
        y -= 20
    toc[K.NameObject("/Annots")] = K.ArrayObject(annots)
    return K.to_bytes(w)


def parse(data: bytes, name: str):
    from app.parsers.pdf_parser import PDFParser

    p = os.path.join(_TMP, name)
    with open(p, "wb") as fh:
        fh.write(data)
    return p, PDFParser().parse(p)


def main() -> int:
    from app.models.accessibility import LinkNode, iter_reading_order
    from app.services.remediation_engine import RemediationEngine
    from app.writers.pdf_writer import write_remediated_pdf

    check = K.Checker()
    src, res = parse(build(), "links.pdf")
    links = [n for n in iter_reading_order(res.tree.root) if isinstance(n, LinkNode)]
    check("three links parsed", len(links) == 3, str(len(links)))
    uri, goto, bad = links + [None] * (3 - len(links))
    check("URI link named by the words under it", uri and uri.content.text == "annual report", repr(uri and uri.content.text))
    check("GoTo link resolves to its page", goto and goto.target == "#page-2", repr(goto and goto.target))
    check("GoTo link named by its printed text", goto and goto.content.text == "See Appendix B")
    check("missing named destination has no target", bad and not bad.target, repr(bad and bad.target))
    check("link bbox = its /Rect, page size recorded",
          goto and goto.metadata.properties.get("bbox") == [72.0, 676.0, round(72 + width("See Appendix B"), 1), 690.0]
          and goto.metadata.properties.get("page_size") == [612.0, 792.0], str(goto and goto.metadata.properties))
    viol = RemediationEngine().detect_violations(res.tree)
    by_node = {}
    for v in viol:
        by_node.setdefault(v.location.node_id, set()).add(v.rule_id)
    check("the named links raise no link finding",
          not (by_node.get(uri.id, set()) | by_node.get(goto.id, set())) & {"LINK_TEXT_NON_DESCRIPTIVE", "LINK_TARGET_BROKEN", "LINK_NAME_MISSING"},
          str({k: v for k, v in by_node.items() if "link" in k}))
    check("the dangling GoTo is still BROKEN and nameless",
          {"LINK_TARGET_BROKEN", "LINK_NAME_MISSING"} <= by_node.get(bad.id, set()), str(by_node.get(bad.id)))

    _p, res_toc = parse(build_toc(), "toc.pdf")
    rules = [v.rule_id for v in RemediationEngine().detect_violations(res_toc.tree)]
    link_rules = [r for r in rules if r.startswith("LINK_")]
    check("a table of contents of GoTo links raises NO link findings (was 10)", link_rules == [], str(link_rules))

    # ---- tagged output ---------------------------------------------------------
    root = res.tree.root
    root.metadata.properties["tag_structure_requested"] = True
    out = Path(_TMP) / "out.pdf"
    wr = write_remediated_pdf(Path(src), res.tree, out)
    check("tagger named 2 links from the page", (wr.get("pdfua") or {}).get("linksNamedFromPage") == 2, str(wr.get("pdfua")))
    r = PdfReader(str(out))
    contents = [str(a.get_object().get("/Contents") or "") for a in r.pages[0]["/Annots"]]
    check("annotation /Contents = the visible words (not the URI)",
          contents[:2] == ["annual report", "See Appendix B"], str(contents))
    order = [s for d, s, _e in K.struct_elems(r) if d == 1]
    # H1, P(annual report line), Link, P(See Appendix B), Link, P x5, Link(dangling: no line) ...
    check("each /Link follows the text on its own line",
          order[:5] == ["/H1", "/P", "/Link", "/P", "/Link"], str(order[:8]))
    return check.done()


if __name__ == "__main__":
    sys.exit(main())
