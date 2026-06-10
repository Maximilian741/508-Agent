# Launch finishing loop — state

> Living work-queue for the self-paced finishing loop. Each iteration picks
> the highest-impact item here (or runs a fresh self-audit when the list is
> empty), verifies, and pushes to the open PR branch.

## Where things stand (2026-06-10)

- **PR #24** (`feat/launch-finishing-pass`) — MERGED. Funnel, password reset,
  trust pack, engine accuracy (PDF_UNTAGGED, TEXT_STYLED_AS_HEADING, bare-URL
  fix agreement, derived titles), abuse hardening, frontend polish.
- **PR #25** (`feat/launch-polish-2`) — OPEN, the loop's working branch.
  Iterations stack verified commits here so nothing waits on a merge.

## Done in PR #25 so far

1. SLIDE_TITLE_MISSING magnitude — parser marks each untitled slide's
   SectionNode (`missing_title`); analyzer flags per-slide (3 untitled →
   3 issues with slide numbers), deck-level fallback kept.
2. PPTX slide-title double-emit — title placeholder no longer re-surfaces as
   a ParagraphNode (matched by `shape_id`; hyperlinks still collected;
   contrast/order moved onto the HeadingNode).
3. Branded app icons — favicon/icon/adaptive/splash now the ember `#C2410C`
   "508" mark (replaces Expo defaults); confirmed in production `dist/`.
4. Live-HTTP journey QA (the item the stopped agent never finished) — booted
   uvicorn, `verify_live_site` 5/5, then a 14/14 deep journey: PPTX per-slide
   flags over the wire (slides 2/3/4), DOCX analyze → approve → remediate →
   signed download → derived title verified IN the output bytes, unsigned +
   tampered-signature downloads rejected (403). Evidence-only; no code change.
5. DOCX fake-list conversion (closed the oldest disclosed writer gap) — typed
   "- item" / "1. item" paragraph runs are detected (LIST_STRUCTURE_INVALID,
   one per typed list; precision guards for prose/years/stray lines/real
   lists), FIX_LIST_STRUCTURE converts them, and the writer persists REAL
   Word lists: w:numPr per paragraph + numbering.xml (created if absent, ids
   above existing max), literal markers stripped. FIX_LIST_STRUCTURE added to
   _PERSISTED_ACTIONS["docx"]; smoke_fake_lists (16 cases incl. byte-level +
   idempotency proofs); smoke_score_honesty re-pinned (suite now 47).

## Known remaining

- In-cell DOCX hyperlinks + w:fldSimple links not remediated (disclosed).
- PPTX/PDF fake-list conversion (DOCX-only for now; PDF tagger already
  handles real bullet glyphs when tagging untagged PDFs).
- PDF coloured-background contrast false-positive (disclosed).

## Verification gates (run before every push)

- backend: `python run_smoke.py` (expect 46/46), `python -m pytest tests/ -q`
- frontend: `node_modules/.bin/tsc --noEmit`, `npx expo export -p web`
- live (optional, for API-visible changes):
  `python -m uvicorn app.main:app --port 8099` +
  `python -m app.devtools.verify_live_site --api-url http://127.0.0.1:8099`
