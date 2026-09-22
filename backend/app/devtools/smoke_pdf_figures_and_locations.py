"""Smoke: every PDF image is in the structure tree, captions feed alt text,
and every flaggable node says WHERE it is on the page.

Before:
  * an image without alt was left as raw, untagged content — not /Figure, not
    /Artifact — so it was absent from the reading order, PDF/UA 7.1 was
    violated, and our own output summary read "figures=0, missing=0" (clean);
  * a "Figure 1. ..." caption printed right under a chart never reached the
    alt-text step (the PDF parser never set properties["caption"], which the
    DOCX and HTML parsers do), so it was refused as "no nearby caption";
  * no PDF node carried a position, so a finding could not show where it is.

Now (pinned against the parser, the executor's inputs and the tagged output
read back from the file):
  1. the undescribed chart is tagged /Figure WITHOUT /Alt (never invented),
     with a /Layout /BBox read off its CTM, counted as figuresWithoutAlt and
     disclosed in writer.skipped; the re-analysis still reports its missing alt;
  2. a decorative logo is wrapped /Artifact (out of the reading order);
  3. the chart's ImageNode carries caption="Figure 1. ...",
     caption_source="figure_label", and GENERATE_ALT_TEXT hands that caption
     to the provider; the logo (no caption under it) gets none;
  4. images, paragraphs/headings and links carry properties["bbox"] (PDF user
     space, origin bottom-left of the visible page box) and ["page_size"], and
     undescribed images carry a small PNG thumbnail; a CropBox offset is
     normalised away.

Usage:
    python -m app.devtools.smoke_pdf_figures_and_locations
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from app.devtools import _pdf_fixture_kit as K

_TMP = K.isolated_env("508_smoke_figloc_")

from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import ArrayObject, FloatObject, NameObject  # noqa: E402

CAPTION = "Figure 1. Tons of rock salt applied per winter season, 2021-2025."


def build(crop_offset: float = 0.0, rotate: int = 0) -> bytes:
    w = PdfWriter()
    f = K.helvetica(w)
    chart = K.gray_image(w, 60, 30, 90)
    logo = K.gray_image(w, 10, 10, 200)
    c = K.bt("F1", 20, 72, 720, K.lit("Salt Use Report"))
    c += K.bt("F1", 11, 72, 690, K.lit("The chart below shows tons of salt applied each winter."))
    c += b"q 300 0 0 150 100 500 cm /Im1 Do Q\n"
    c += K.bt("F1", 10, 100, 485, K.lit(CAPTION))
    c += b"q 20 0 0 20 500 740 cm /Im2 Do Q\n"
    for i in range(6):
        c += K.bt("F1", 11, 72, 440 - 14 * i, K.lit("Depots restock after every storm so that crews can reload quickly."))
    page = K.add_page(w, c, {"F1": f}, xobjects={"Im1": chart, "Im2": logo})
    if crop_offset:
        page[NameObject("/CropBox")] = ArrayObject(
            [FloatObject(v) for v in (crop_offset, crop_offset, 612 - crop_offset, 792 - crop_offset)]
        )
    if rotate:
        page[NameObject("/Rotate")] = K.NumberObject(rotate)
    return K.to_bytes(w)


def two_charts(layout: str) -> bytes:
    """Two charts, each with its own "Figure N." caption printed BELOW it.

    "side": side by side, both captions on ONE baseline (they used to merge
    into one line, and both charts got "Figure 1 ... Figure 2 ...").
    "stacked": chart 2 sits 16pt under caption 1, its own caption 20pt below
    it (chart 2 used to take caption 1 as its "caption above").
    "wrap": one chart, a two-line 9pt caption, then 11pt body text 13pt below
    (the first body line used to be swallowed into the caption).
    """
    w = PdfWriter()
    f = K.helvetica(w)
    a = K.gray_image(w, 20, 20, 90)
    b = K.gray_image(w, 20, 20, 150)
    c = K.bt("F1", 18, 72, 760, K.lit("Winter Charts"))
    xo = {"ImA": a, "ImB": b}
    if layout == "side":
        c += b"q 200 0 0 150 72 500 cm /ImA Do Q\nq 200 0 0 150 320 500 cm /ImB Do Q\n"
        c += K.bt("F1", 9, 72, 488, K.lit("Figure 1. Snowfall by month"))
        c += K.bt("F1", 9, 320, 488, K.lit("Figure 2. Salt use by month"))
        y = 440
    elif layout == "stacked":
        c += b"q 300 0 0 150 72 580 cm /ImA Do Q\n"
        c += K.bt("F1", 9, 72, 566, K.lit("Figure 1. Snowfall by month"))
        c += b"q 300 0 0 150 72 400 cm /ImB Do Q\n"
        c += K.bt("F1", 9, 72, 380, K.lit("Figure 2. Salt use by month"))
        y = 330
    else:
        c += b"q 300 0 0 150 72 580 cm /ImA Do Q\n"
        c += K.bt("F1", 9, 72, 566, K.lit("Figure 1. Snowfall by month at the airport"))
        c += K.bt("F1", 9, 72, 555, K.lit("station, 2021-2025 seasons."))
        y = 542
        xo = {"ImA": a}
    for i in range(6):
        c += K.bt("F1", 11, 72, y - 14 * i, K.lit("Depots restock after every storm so crews can reload quickly."))
    K.add_page(w, c, {"F1": f}, xobjects=xo)
    return K.to_bytes(w)


def parse(data: bytes, name: str = "fig.pdf"):
    from app.parsers.pdf_parser import PDFParser

    p = os.path.join(_TMP, name)
    with open(p, "wb") as fh:
        fh.write(data)
    return p, PDFParser().parse(p)


def main() -> int:
    from app.models.accessibility import HeadingNode, ImageNode, ParagraphNode, iter_reading_order
    from app.services.remediation_engine import RemediationEngine
    from app.writers.pdf_writer import write_remediated_pdf

    check = K.Checker()
    data = build()
    src, res = parse(data)
    nodes = list(iter_reading_order(res.tree.root))
    imgs = {n.metadata.properties.get("xobject"): n for n in nodes if isinstance(n, ImageNode)}
    chart, logo = imgs.get("/Im1"), imgs.get("/Im2")
    check("both images parsed", chart is not None and logo is not None, str(list(imgs)))

    # ---- 3. captions ---------------------------------------------------------
    cp = chart.metadata.properties
    check("chart carries its printed caption", cp.get("caption") == CAPTION, repr(cp.get("caption")))
    check("...marked as a figure-label caption", cp.get("caption_source") == "figure_label")
    check("logo (nothing under it) gets no caption", "caption" not in logo.metadata.properties)

    def caps(layout: str):
        _p, r = parse(two_charts(layout), f"{layout}.pdf")
        return {n.metadata.properties.get("xobject"): n.metadata.properties.get("caption")
                for n in iter_reading_order(r.tree.root) if isinstance(n, ImageNode)}

    side = caps("side")
    check("side-by-side charts: each gets ITS OWN caption",
          side == {"/ImA": "Figure 1. Snowfall by month", "/ImB": "Figure 2. Salt use by month"}, str(side))
    stacked = caps("stacked")
    check("stacked charts: chart 2 is not handed chart 1's caption",
          stacked == {"/ImA": "Figure 1. Snowfall by month", "/ImB": "Figure 2. Salt use by month"}, str(stacked))
    wrap = caps("wrap")
    check("a wrapped caption is whole, and the body text under it is not swallowed",
          wrap == {"/ImA": "Figure 1. Snowfall by month at the airport station, 2021-2025 seasons."}, str(wrap))

    seen = {}

    class _Spy:
        provider_name = "spy"
        cost_capped = False

        def suggest_alt_text(self, **kw):
            seen.update(kw)
            from types import SimpleNamespace

            return SimpleNamespace(text="", provider="spy", confidence=0.0)

    from app.services.remediation_planner import RemediationPlan  # noqa: F401
    from app.services.remediators.generate_alt_text_executor import GenerateAltTextExecutor

    engine = RemediationEngine()
    viols = engine.detect_violations(res.tree)
    plans = [p for p in _plans(res.tree) if p.target_node_id == chart.id and p.flag.code.value == "MISSING_ALT_TEXT"]
    check("a MISSING_ALT_TEXT plan exists for the chart", bool(plans))
    if plans:
        GenerateAltTextExecutor(client=_Spy()).execute(plans[0], res.tree)
    check("GENERATE_ALT_TEXT hands the caption to the provider", seen.get("caption") == CAPTION, repr(seen.get("caption")))

    # ---- 4. locations --------------------------------------------------------
    check("chart bbox = its CTM placement", cp.get("bbox") == [100.0, 500.0, 400.0, 650.0], str(cp.get("bbox")))
    check("chart page_size", cp.get("page_size") == [612.0, 792.0], str(cp.get("page_size")))
    thumb = cp.get("thumbnail") or ""
    check("undescribed image carries a PNG data-URI thumbnail", thumb.startswith("data:image/png;base64,") and len(thumb) < 90_000)
    # A TALL image: the thumbnail is at most 240 px on its longer side.
    import base64

    from PIL import Image

    wt = PdfWriter()
    ft = K.helvetica(wt)
    tall = K.gray_image(wt, 120, 900, 60)
    ct = K.bt("F1", 11, 72, 740, K.lit("A tall infographic follows.")) + b"q 100 0 0 600 72 100 cm /ImT Do Q\n"
    K.add_page(wt, ct, {"F1": ft}, xobjects={"ImT": tall})
    _pt, rt = parse(K.to_bytes(wt), "tall.pdf")
    tn = [n for n in iter_reading_order(rt.tree.root) if isinstance(n, ImageNode)]
    tt = (tn[0].metadata.properties.get("thumbnail") or "") if tn else ""
    dims = Image.open(io.BytesIO(base64.b64decode(tt.split(",", 1)[1]))).size if tt.startswith("data:image/png;base64,") else None
    check("a tall image's thumbnail is <= 240 px on its longer side", bool(dims) and max(dims) <= 240, str(dims))
    texts = [n for n in nodes if isinstance(n, (ParagraphNode, HeadingNode))]
    boxed = [n for n in texts if n.metadata.properties.get("bbox")]
    check("every text node is located", texts and len(boxed) == len(texts), f"{len(boxed)}/{len(texts)}")
    first = boxed[0].metadata.properties["bbox"] if boxed else [0, 0, 0, 0]
    check("the first text box contains the title's baseline (y=720) at x=72",
          first[0] <= 72.5 and first[1] <= 720 <= first[3], str(first))
    _src2, res2 = parse(build(crop_offset=36.0), "crop.pdf")
    c2 = [n for n in iter_reading_order(res2.tree.root) if isinstance(n, ImageNode) and n.metadata.properties.get("xobject") == "/Im1"][0]
    check("CropBox offset normalised (bbox relative to the visible page)",
          c2.metadata.properties.get("bbox") == [64.0, 464.0, 364.0, 614.0]
          and c2.metadata.properties.get("page_size") == [540.0, 720.0], str(c2.metadata.properties.get("bbox")))

    _src3, res3 = parse(build(rotate=90), "rot.pdf")
    c3 = [n for n in iter_reading_order(res3.tree.root) if isinstance(n, ImageNode) and n.metadata.properties.get("xobject") == "/Im1"][0]
    check("a /Rotate 90 page: bbox stays in PDF user space, and the rotation is recorded beside it",
          c3.metadata.properties.get("bbox") == [100.0, 500.0, 400.0, 650.0]
          and c3.metadata.properties.get("page_rotate") == 90
          and "page_rotate" not in cp, str(c3.metadata.properties.get("page_rotate")))

    # ---- 1 + 2. the tagged output --------------------------------------------
    root = res.tree.root
    root.metadata.properties["tag_structure_requested"] = True
    root.metadata.properties["title"] = "Salt Use Report"
    root.metadata.language = "en-US"
    logo.is_decorative = True
    out = Path(_TMP) / "out.pdf"
    wr = write_remediated_pdf(Path(src), res.tree, out)
    ua = wr.get("pdfua") or {}
    check("tagger: 1 figure without alt, 1 decorative image", ua.get("figuresWithoutAlt") == 1 and ua.get("decorativeImages") == 1, str(ua))
    check("'figures' still means figures WITH alt (0 here)", ua.get("figures") == 0, str(ua))
    reasons = " ".join(s.get("reason", "") for s in wr.get("skipped", []))
    check("disclosed: figures tagged but still need a description", "pdfua_figures_without_alt: 1" in reasons, reasons[:300])
    r = PdfReader(str(out))
    figs = [e for _d, s, e in K.struct_elems(r) if s == "/Figure"]
    check("the chart is a /Figure in the structure tree", len(figs) == 1, str(len(figs)))
    if figs:
        fe = figs[0]
        check("...with NO invented /Alt", "/Alt" not in fe)
        bb = [float(v) for v in fe["/A"].get_object()["/BBox"]] if "/A" in fe else []
        check("...and a /Layout /BBox from its placement", bb == [100.0, 500.0, 400.0, 650.0], str(bb))
    tags = K.marked_tags(r, 0)
    check("content stream: /Figure marked, logo wrapped /Artifact", "/Figure" in tags and "/Artifact" in tags, str(tags))
    xmp = r.trailer["/Root"]["/Metadata"].get_object().get_data()
    check("no PDF/UA claim with an undescribed figure", b"pdfuaid:part" not in xmp)
    _p, res_out = parse(out.read_bytes(), "reparse.pdf")
    rules = [v.rule_id for v in RemediationEngine().detect_violations(res_out.tree)]
    check("re-analysis still (honestly) reports the chart's missing alt", rules.count("MISSING_ALT_TEXT") == 1, str(rules))
    tag_heads = [n for n in iter_reading_order(res_out.tree.root) if isinstance(n, HeadingNode) and n.metadata.properties.get("from_tags")]
    hb = tag_heads[0].metadata.properties.get("bbox") if tag_heads else None
    check("headings read from TAGS are located too", bool(hb) and hb[1] <= 720 <= hb[3], str(hb))
    return check.done()


def _plans(tree):
    from app.services.remediation_planner import RemediationPolicy, plan_remediations

    return plan_remediations(tree, RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False))


if __name__ == "__main__":
    sys.exit(main())
