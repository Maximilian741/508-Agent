# Launch finishing loop — LAUNCH REPORT (2026-06-10)

> Work queue for the self-paced finishing loop. The queue is now EMPTY —
> this file doubles as the launch report. Delete after PR #25 merges.

## Bottom line

**The product is ready to sell.** Seven loop iterations on top of merged
PR #24 closed every known-remaining item, every disclosed engine gap that
was closeable, and proved the whole loop empirically — twice (in-process
corpus smoke + live HTTP against a running server).

- Suite: **48/48 smokes**, pytest 27 passed, tsc clean, production
  `expo export` green.
- Live HTTP (booted uvicorn): `verify_live_site` 5/5; deep journey 14/14
  (signed downloads, authz boundaries); journey v2 11/11 — fake list
  detected over the wire, fixed in the download, **gone on re-upload**
  (the exact before/after a customer will see).
- Corpus invariants pinned (smoke_realistic_corpus): never crashes on
  mixed content; violations strictly decrease; headline codes GONE from
  outputs; NO new codes appear post-fix; clean doc stays at zero.

## What PR #25 (feat/launch-polish-2) contains — merge this

1. SLIDE_TITLE_MISSING magnitude — per-slide flags w/ slide numbers.
2. PPTX slide-title double-emit fixed (shape_id-matched; contrast kept).
3. Branded app icons (ember #C2410C "508") — favicon/icon/adaptive/splash.
4. Live-HTTP journey QA evidence (14/14) — closed the stopped-agent item.
5. DOCX fake-list conversion — typed "- item"/"1. item" runs become REAL
   Word lists (numbering.xml + w:numPr, markers stripped); precision guards;
   FIX_LIST_STRUCTURE persists for docx; 16-case smoke.
6. DOCX link coverage complete — fldSimple HYPERLINK fields + in-cell
   hyperlinks detected AND remediated; 2 latent id-drift bugs killed via a
   shared parser/writer link predicate; alignment torture smoke.
7. PDF coloured-background contrast FP fixed — pages painting non-white
   fills/shading are skipped (no guess, no false flag); white-bg recall kept.
8. PPTX fake-list conversion — real a:buChar/a:buAutoNum per typed line in
   plain text boxes (placeholders excluded); FIX_LIST_STRUCTURE persists for
   pptx; fixed a latent pptx writer paragraph-id drift.
9. smoke_realistic_corpus — 30-assert end-to-end buyer-invariant proof.
10. Sales surfaces synced: README 19-analyzer reality; landing typed-list
    differentiator; catalog LIST_STRUCTURE_INVALID card truthful per format.
11. PDF/UA link tagging (iteration 8): every /Link annotation on a tagged
    page is nested in a /Link StructElem via OBJR (Matterhorn 28-011), annot
    gets /StructParent with an ascending ParentTree entry resolving straight
    to the elem, ParentTreeNextKey raised above annot keys, /Contents
    accessible description falls back to the URI action (28-012); explicit
    Contents preserved; non-link annots + no-annot docs untouched.
    smoke_pdf_links (13 asserts; suite 49).

## Engine capability statement (all verified in suite)

19 analyzers; persisted auto-fixes per format:
- DOCX: title, language (incl. styles.xml w:lang), heading normalize, alt
  text (+overwrite of filename/placeholder alt), decorative alt removal,
  table headers (promote + synthesize), link text (incl. fldSimple +
  in-cell), typed fake lists -> real numbering.
- PPTX: title, language (per-run), alt text + decorative (real descr +
  adec:decorative), table headers (band + synthesized row), link text
  (split-run safe), typed fake lists -> real bullets.
- PDF: title, language, alt text (/Figure + /Alt in struct tree), full
  structure-tree reconstruction on untagged PDFs (headings/lists/tables/
  artifacts/ParentTree/XMP), scanned-PDF hard error.
Detect-only (honest pendingManual): contrast (w/ theme + PDF text, FP-safe),
fake bold-text headings, form-field labels, slide titles (auto-fix n/a),
heading jumps on PDF, reading order (no parser support — deregistered).

## Known remaining (none blocking sale)

- PDF typed-list conversion via FIX_LIST_STRUCTURE n/a — untagged PDFs get
  real /L/LI structure from ua_tagger during TAG_PDF_STRUCTURE instead.
- Docker image build still verify-on-host (no docker in sandbox).
- Stripe live-mode purchase needs one manual test card run (SHIP-CHECKLIST).

## Verification gates

- backend: `python run_smoke.py` (48/48), `python -m pytest tests/ -q`
- frontend: `node_modules/.bin/tsc --noEmit`, `npx expo export -p web`
- live: `python -m uvicorn app.main:app --port 8099` +
  `python -m app.devtools.verify_live_site --api-url http://127.0.0.1:8099`
