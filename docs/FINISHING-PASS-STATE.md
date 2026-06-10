# Launch finishing loop — state (post-#26)

> Work queue for the self-paced finishing loop. PRs #24, #25, #26 are MERGED.
> Current working branch: feat/launch-polish-4 (PR #27).

## Done in PR #27 so far

1. Tagged-PDF structure analysis (iteration 18) — tagged-but-badly-tagged
   PDFs (gov archives are full of them) no longer coast:
   - NEW app/pdf/tag_reader.py: walks the EXISTING StructTreeRoot
     (RoleMap-resolved, one hop), recovers heading text from MCID spans,
     maps Figure /Alt to the XObject drawn inside the figure's MCID, and
     itemises tagged tables (TR/TH/TD names per row).
   - pdf_parser: when the tree declares headings, tags are AUTHORITATIVE
     (text heuristic suppressed); HeadingNodes carry tag levels + MCID text
     -> HEADING_LEVEL_JUMP now fires on tag-level jumps. Tagged tables emit
     typed TableNodes -> TABLE_MISSING_HEADERS fires when no TH exists.
   - FP FIX: a Figure whose /Alt lives on the StructElem (the standards-
     correct place) was falsely flagged missing-alt because only the
     XObject dict was consulted; tree alt now honoured (name-normalized).
   - Report disclosure updated (tagged-PDF analysis described).
   - smoke_tagged_pdf_analysis (9 asserts; suite 55) incl. a DOGFOOD case:
     a PDF tagged by our own ua_tagger re-parses through the tag reader
     with correct headings and zero false jumps.

## Done in PR #26 so far

1. OCR for scanned PDFs (iteration 16) — the #1 hard failure becomes
   auto-fixable where the deployment allows it:
   - app/services/ocr.py: OcrProvider protocol; TesseractOcrProvider
     (lazy pytesseract import; available() requires the binary);
     set_ocr_provider_for_testing for stub injection.
   - OCR_ENABLED setting (default false) + deploy/.env.example block +
     docs/LAUNCH.md runbook section (apt-get tesseract-ocr; degrades
     gracefully when absent).
   - NEW ActionCode ADD_OCR_TEXT_LAYER mapped to SCANNED_DOCUMENT_NO_TEXT
     (auto) with FLAG_FOR_MANUAL_REVIEW fallback; executor gates on provider
     availability (SKIPPED with honest note when off — score never claims).
   - pdf_writer step 3.5 _apply_ocr_text_layer: for each image-only page
     (<50 extractable chars), recognize the largest embedded image, append
     ONE invisible BT..ET overlay (render mode 3, per-word Tm positions
     mapped image-px -> page-space, Helvetica /F508OCR resource) as an
     EXTRA content stream — originals untouched. Runs BEFORE tag_pdf so the
     recognized text gets structure-tagged.
   - ADD_OCR_TEXT_LAYER added to _PERSISTED_ACTIONS["pdf"] (executor only
     succeeds when a provider exists, so counting is honest).
   - pytesseract==0.3.13 in requirements (pure wrapper; harmless w/o binary).
   - Catalog SCANNED_DOCUMENT_NO_TEXT copy: per-deployment honest phrasing.
   - NEW smoke_ocr_layer (12 asserts; suite 54): OFF -> skipped + flag
     persists; ON (stub) -> success, text extractable from output bytes,
     "3 Tr" invisible overlay, original /Im0 Do intact, re-parse shows
     scanned flag GONE + pdf_tagged True.

## Known remaining

- Host-side once Max enables OCR: install tesseract, set OCR_ENABLED=true,
  run the LAUNCH.md verification (upload scan -> approve -> searchable).
- Stripe live-mode test card run (Max, SHIP-CHECKLIST).
- Docker image build verify-on-host (Max).

## Verification gates

- backend: `python run_smoke.py` (54/54), `python -m pytest tests/ -q`
- frontend: `node_modules/.bin/tsc --noEmit`, `npx expo export -p web`
- live: `python -m uvicorn app.main:app --port 8099` +
  `python -m app.devtools.verify_live_site --api-url http://127.0.0.1:8099`
