"""Smoke: OCR text layer for scanned PDFs (provider-stubbed, byte-verified).

A scanned PDF is image-only — nothing for AT to read. With an OCR provider
available, ADD_OCR_TEXT_LAYER appends an INVISIBLE (render mode 3),
position-matched text layer per scanned page, and the PDF/UA tagger then
structures the recognized text. Pinned here with a stub provider (the
Tesseract adapter is a thin wrapper; deployments without it degrade):

  - OCR OFF: executor SKIPPED with an honest note; output unchanged; the
    scanned flag persists on re-parse (nothing silently claimed).
  - OCR ON (stub): executor SUCCESS; output text extractable ("ANNUAL
    REPORT…"); overlay stream uses 3 Tr (invisible); re-parse shows the
    SCANNED flag GONE and pdf_tagged True (tagger ran on the new text).

Usage:
    python -m app.devtools.smoke_ocr_layer
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_ocr_')}/s.db")

from PIL import Image  # noqa: E402
from pypdf import PdfReader, PdfWriter  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
)

from app.analyzers.registry import run_analyzers  # noqa: E402
from app.api.pipeline import _action_persists  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.ocr import OcrPageResult, OcrWord, set_ocr_provider_for_testing  # noqa: E402
from app.services.remediation_planner import RemediationPolicy, plan_remediations  # noqa: E402
from app.services.remediators.registry import execute_plans  # noqa: E402
from app.writers import write_remediated  # noqa: E402

APPLY = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)
FLAG = "SCANNED_DOCUMENT_NO_TEXT"


class StubOcr:
    name = "stub"

    def available(self) -> bool:
        return True

    def recognize(self, image_bytes: bytes):
        # A realistic page yields well over the parser's 50-char image-only
        # threshold: a title line + two body lines on a 1000x1294 scan.
        words = [
            OcrWord("ANNUAL", 80, 90, 220, 48),
            OcrWord("REPORT", 320, 90, 230, 48),
        ]
        body1 = "Operations summary for the reporting period (scanned original)".split()
        body2 = "All regional programmes met their accessibility milestones this year.".split()
        x = 80
        for token in body1:
            words.append(OcrWord(token, x, 220, 16 * len(token), 28))
            x += 16 * len(token) + 12
        x = 80
        for token in body2:
            words.append(OcrWord(token, x, 270, 16 * len(token), 28))
            x += 16 * len(token) + 12
        return OcrPageResult(width_px=1000, height_px=1294, words=words)


def _build_scanned_pdf(path: Path) -> None:
    """One page whose only content is a full-page JPEG (a classic scan)."""
    img = Image.new("RGB", (1000, 1294), (245, 242, 235))
    jpeg = io.BytesIO()
    img.save(jpeg, format="JPEG")
    data = jpeg.getvalue()

    w = PdfWriter()
    page = w.add_blank_page(width=612, height=792)
    xobj = StreamObject()
    xobj._data = data  # noqa: SLF001 — raw DCT bytes, no re-encode
    xobj.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1000),
            NameObject("/Height"): NumberObject(1294),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/DCTDecode"),
        }
    )
    xobj_ref = w._add_object(xobj)  # noqa: SLF001
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): xobj_ref})}
    )
    from pypdf.generic import DecodedStreamObject

    cs = DecodedStreamObject()
    cs.set_data(b"q 612 0 0 792 0 0 cm /Im0 Do Q")
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    with open(path, "wb") as fh:
        w.write(fh)


def main() -> int:
    failures = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    tmp = Path(tempfile.mkdtemp(prefix="ocr_"))
    src = tmp / "scan.pdf"
    _build_scanned_pdf(src)

    check("honesty matrix: ADD_OCR_TEXT_LAYER persists for pdf", _action_persists("ADD_OCR_TEXT_LAYER", "pdf"))

    # --- Case A: OCR disabled (default) — honest skip, nothing claimed -------
    set_ocr_provider_for_testing(None)
    res = parse_to_tree(str(src))
    run_analyzers(res.tree)
    flags = [f.code.value for f in res.tree.root.accessibility_flags]
    check("scanned PDF flagged", FLAG in flags, str(flags))
    plans = [p for p in plan_remediations(res.tree, APPLY) if p.flag.code.value == FLAG]
    check("OCR plan exists for the flag", len(plans) >= 1)
    execs = execute_plans(res.tree, plans)
    ocr_execs = [e for e in execs if e.action_code.value == "ADD_OCR_TEXT_LAYER"]
    check(
        "OCR OFF -> executor SKIPPED with honest note",
        len(ocr_execs) == 1 and ocr_execs[0].status.value == "skipped" and "not enabled" in (ocr_execs[0].notes or ""),
        str([(e.status.value, e.notes) for e in ocr_execs]),
    )
    out_off = tmp / "scan_off.pdf"
    write_remediated(src, res.tree, out_off, source_format="pdf")
    res_off = parse_to_tree(str(out_off))
    run_analyzers(res_off.tree)
    check(
        "OCR OFF -> scanned flag persists on re-parse (no silent claim)",
        FLAG in [f.code.value for f in res_off.tree.root.accessibility_flags],
    )

    # --- Case B: OCR available (stub) — full pipeline, byte-verified ----------
    set_ocr_provider_for_testing(StubOcr())
    try:
        res2 = parse_to_tree(str(src))
        run_analyzers(res2.tree)
        plans2 = [p for p in plan_remediations(res2.tree, APPLY) if p.flag.code.value == FLAG]
        execs2 = execute_plans(res2.tree, plans2)
        ocr2 = [e for e in execs2 if e.action_code.value == "ADD_OCR_TEXT_LAYER"]
        check(
            "OCR ON -> executor SUCCESS",
            len(ocr2) == 1 and ocr2[0].status.value == "success",
            str([(e.status.value, e.notes) for e in ocr2]),
        )
        out_on = tmp / "scan_on.pdf"
        report = write_remediated(src, res2.tree, out_on, source_format="pdf")
        kinds = [a.get("kind") for a in report.get("applied", [])]
        check("writer applied the ocr_text_layer", "ocr_text_layer" in kinds, str(report))

        r = PdfReader(str(out_on))
        text = r.pages[0].extract_text() or ""
        check("recognized text extractable from output", "ANNUAL" in text and "REPORT" in text, text[:120])

        # The overlay must be INVISIBLE: render mode 3 in the appended stream.
        contents = r.pages[0].get_contents()
        raw = contents.get_data() if hasattr(contents, "get_data") else b""
        check("overlay uses render mode 3 (invisible)", b"3 Tr" in raw)
        check("original image draw still present", b"/Im0 Do" in raw)

        res_on = parse_to_tree(str(out_on))
        run_analyzers(res_on.tree)
        on_flags = [f.code.value for f in res_on.tree.root.accessibility_flags]
        check("re-parse: SCANNED flag GONE", FLAG not in on_flags, str(on_flags))
        check("re-parse: output is a TAGGED pdf (tagger ran on OCR text)", bool(res_on.tree.root.metadata.properties.get("pdf_tagged")))
    finally:
        set_ocr_provider_for_testing(None)

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
