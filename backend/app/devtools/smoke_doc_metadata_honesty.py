"""Smoke: document language and title are never GUESSED into a file.

Two document-level executors wrote low-confidence guesses as fixes and were
credited for them:

  * SET_DOCUMENT_LANGUAGE defaulted to "en" whenever the detector had
    nothing — so an all-Japanese PDF got /Lang en (heuristic, 0.25),
    SUCCESS, charged. A screen reader picks pronunciation rules from /Lang;
    a wrong tag is worse than none.
  * SET_DOCUMENT_TITLE fell back to "Untitled Document" — a string our own
    DocumentTitleAnalyzer flags as a placeholder — and wrote it as the
    /Title with DisplayDocTitle on, so viewers showed it in the title bar.
    Meanwhile the PDF parser's heading detection is text-shape only, so an
    18pt "Annual Report 2025" produced no HeadingNode and the real title was
    never considered.

Pinned here, through the real engine with the production apply policy:
  * Japanese/Korean/Arabic/Chinese samples are detected by SCRIPT at high
    confidence; numbers-only and empty samples make the detector ABSTAIN
  * a Japanese-only PDF is tagged ja, not en
  * a numbers-only PDF gets NO language written and the executor SKIPs
  * an untitled PDF whose page 1 has one clearly-larger line gets THAT as
    its title
  * an untitled PDF with no distinguishable title line and no usable
    filename gets NO title — the executor SKIPs instead of writing
    "Untitled Document"

Usage:
    python -m app.devtools.smoke_doc_metadata_honesty
"""

from __future__ import annotations

import io
import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='508_smoke_dmh_')}/s.db")
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ["SEMANTIC_PROVIDER"] = "heuristic"

from pathlib import Path  # noqa: E402

from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

from app.ai.semantic_inference import HeuristicProvider  # noqa: E402
from app.models.accessibility import ActionCode  # noqa: E402
from app.parsers import parse_to_tree  # noqa: E402
from app.services.remediation_engine import RemediationEngine  # noqa: E402
from app.services.remediation_planner import RemediationPolicy  # noqa: E402
from app.services.remediators.base import ExecutionStatus  # noqa: E402

_APPLY = RemediationPolicy(allow_ai_actions=True, require_human_review_for_all=False)


def _pdf(lines, path: Path) -> None:
    """lines = [(size, text)], drawn top-down on one page (Latin-1 text only —
    the Standard-14 font cannot carry CJK, so non-Latin cases below are built
    as trees and fed straight to the engine)."""
    w = PdfWriter()
    font = DictionaryObject()
    font.update({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = w._add_object(font)  # noqa: SLF001
    res = DictionaryObject()
    res[NameObject("/Font")] = fonts
    page = w.add_blank_page(width=612, height=792)
    ops = []
    y = 740
    for size, text in lines:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(b"BT /F1 %d Tf 72 %d Td (%s) Tj ET" % (size, y, safe.encode("latin-1")))
        y -= int(size * 1.6)
    cs = DecodedStreamObject()
    cs.set_data(b"\n".join(ops))
    page[NameObject("/Contents")] = w._add_object(cs)  # noqa: SLF001
    page[NameObject("/Resources")] = res
    buf = io.BytesIO()
    w.write(buf)
    path.write_bytes(buf.getvalue())


def _run(path: Path):
    res = parse_to_tree(str(path))
    eng = RemediationEngine(policy=_APPLY)
    eng.detect_violations(res.tree)
    results = eng.execute(res.tree)
    by_action = {r.action_code: r for r in results}
    return res.tree, by_action


def main() -> int:
    failures = 0

    def check(name, cond, extra=""):
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- detector ----------------------------------------------------------
    h = HeuristicProvider()
    for name, text, want in [
        ("japanese", "年次予算報告書 この報告書は本年度の予算執行状況をまとめたものです", "ja"),
        ("korean", "연차 예산 보고서 이 보고서는 올해의 예산 집행 상황을 정리한 것입니다", "ko"),
        ("arabic", "تقرير الميزانية السنوية يلخص هذا التقرير حالة تنفيذ الميزانية", "ar"),
        ("chinese", "年度预算报告 本报告总结了本年度的预算执行情况", "zh"),
        ("english", "The annual budget report summarises the state of execution for the year", "en"),
    ]:
        r = h.document_language({"sample": text})
        check(f"detector: {name} -> {want} at >= 0.4", r.text == want and r.confidence >= 0.4, f"{r.text!r} {r.confidence:.2f}")
    for name, text in [("numbers only", "2023 4,500 12.5% 300 7,000"), ("empty", "")]:
        r = h.document_language({"sample": text})
        check(f"detector: {name} -> ABSTAINS (empty, 0.0)", r.text == "" and r.confidence == 0.0, f"{r.text!r} {r.confidence:.2f}")
    r = h.document_language({"sample": "The report was prepared by 田中 for the board and the committee"})
    check("detector: an English page with one CJK name is still en", r.text == "en", f"{r.text!r}")

    tmp = Path(tempfile.mkdtemp(prefix="508_dmh_"))

    # ---- language: Japanese document -> ja, not en -------------------------
    # Built as a tree rather than a PDF: the Standard-14 font in the fixture
    # helper cannot carry CJK. The executor's ONLY input is the tree's text,
    # so this exercises exactly the path the audit flagged — an all-Japanese
    # document reaching the language executor — with the real engine/policy.
    from app.models.accessibility import (
        AccessibilityTree, ContentKind, DocumentNode, NodeContent, NodeMetadata, ParagraphNode,
    )

    def _para(i, text):
        return ParagraphNode(id=f"p{i}", content=NodeContent(kind=ContentKind.TEXT, text=text),
                             metadata=NodeMetadata(source_format="pdf", page=1), children=[], accessibility_flags=[])

    ja_root = DocumentNode(
        id="doc", content=NodeContent(kind=ContentKind.NONE),
        metadata=NodeMetadata(source_format="pdf", language=None,
                              properties={"title": "T", "page_count": 1, "total_text_chars": 60}),
        children=[_para(1, "年次予算報告書"), _para(2, "この報告書は本年度の予算執行状況をまとめたものです"),
                  _para(3, "予算の執行率は前年度比で改善しました")],
        accessibility_flags=[],
    )
    ja_tree = AccessibilityTree(root=ja_root)
    eng = RemediationEngine(policy=_APPLY)
    eng.detect_violations(ja_tree)
    ja_acts = {r.action_code: r for r in eng.execute(ja_tree)}
    lang = ja_acts.get(ActionCode.SET_DOCUMENT_LANGUAGE)
    check("Japanese document: language executor SUCCEEDS", lang is not None and lang.status == ExecutionStatus.SUCCESS, getattr(lang, "notes", None))
    check("Japanese document: language is 'ja' (was 'en' at 0.25)", ja_tree.root.metadata.language == "ja", repr(ja_tree.root.metadata.language))
    check("...at high confidence via script detection", lang is not None and "0.9" in (lang.notes or ""), getattr(lang, "notes", None))

    # ---- language: numbers-only PDF -> nothing written -----------------------
    nums = tmp / "nums.pdf"
    _pdf([(11, "2023 4,500 12.5%"), (11, "300 7,000 88"), (11, "1,250 9.75 42")], nums)
    tree, acts = _run(nums)
    lang = acts.get(ActionCode.SET_DOCUMENT_LANGUAGE)
    check("numbers-only PDF: language executor SKIPS (refuses to guess)",
          lang is not None and lang.status == ExecutionStatus.SKIPPED, getattr(lang, "notes", None))
    check("numbers-only PDF: NO language written", not tree.root.metadata.language, repr(tree.root.metadata.language))
    check("...and the note tells the user to set it themselves", lang is not None and "left for you to set" in (lang.notes or "").lower(), getattr(lang, "notes", None))

    # ---- title: page-1 largest line becomes the title ----------------------
    titled = tmp / "9f3a2c.pdf"  # filename stem is junk, so it cannot rescue the title
    _pdf([(18, "Annual Report 2025"), (11, "The board presents the consolidated results for the year."),
          (11, "Revenue grew in every region and costs were held flat.")], titled)
    tree, acts = _run(titled)
    ttl = acts.get(ActionCode.SET_DOCUMENT_TITLE)
    check("titled PDF: parser recorded the 18pt line as a title candidate",
          (tree.root.metadata.properties or {}).get("title_candidate") == "Annual Report 2025",
          repr((tree.root.metadata.properties or {}).get("title_candidate")))
    check("titled PDF: title executor SUCCEEDS", ttl is not None and ttl.status == ExecutionStatus.SUCCESS, getattr(ttl, "notes", None))
    check("titled PDF: the title IS the page-1 headline, not 'Untitled Document'",
          (tree.root.metadata.properties or {}).get("title") == "Annual Report 2025",
          repr((tree.root.metadata.properties or {}).get("title")))

    # ---- title: nothing stands out -> no placeholder --------------------------
    flat = tmp / "7b1d0e.pdf"
    _pdf([(11, "The board presents the consolidated results for the year."),
          (11, "Revenue grew in every region and costs were held flat."),
          (11, "Further detail is available in the appendices.")], flat)
    tree, acts = _run(flat)
    ttl = acts.get(ActionCode.SET_DOCUMENT_TITLE)
    check("flat PDF: no title candidate recorded (nothing stands out)",
          not (tree.root.metadata.properties or {}).get("title_candidate"),
          repr((tree.root.metadata.properties or {}).get("title_candidate")))
    check("flat PDF: title executor SKIPS rather than writing 'Untitled Document'",
          ttl is not None and ttl.status == ExecutionStatus.SKIPPED, getattr(ttl, "notes", None))
    check("flat PDF: NO title written", not (tree.root.metadata.properties or {}).get("title"),
          repr((tree.root.metadata.properties or {}).get("title")))

    print(f"\nRESULT: {'all passed' if failures == 0 else str(failures) + ' FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
