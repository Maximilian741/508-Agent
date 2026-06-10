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

6. In-cell DOCX hyperlinks + w:fldSimple field links — parser now emits
   LinkNodes for both (shared _link_elements_in_paragraph helper drives
   parser AND writer, killing two latent id-drift bugs: text-less anchors
   desynced the paragraph index; heading-embedded links were counted by the
   writer but never emitted by the parser). Merged cells dedupe by w:tc.
   smoke_link_text_writer grew 10 assertions incl. an alignment torture doc.

7. PDF coloured-background contrast FP — _pdf_text_colors now skips any page
   that PAINTS a non-white fill (f/F/f*/b/B variants) or shading (sh): the
   white-bg assumption doesn't hold there, so no guess and no false flag.
   White painted backgrounds keep recall. 3 new smoke cases.
8. PPTX fake-list conversion — typed "- item" lines in plain TEXT BOXES
   (placeholders excluded: bullets inherit invisibly from layout/master) are
   flagged via the same fake_list_run_ids contract; the pptx writer adds real
   a:buChar / a:buAutoNum bullets per line + strips markers.
   FIX_LIST_STRUCTURE persists for pptx. Also fixed a latent writer id-drift:
   the pptx id-mirror still minted slide-N-p for title shapes after the
   iteration-1 parser change (harmless until paragraphs were indexed — which
   this feature does). smoke_fake_lists now 24 cases (8 pptx).

9. Realistic-corpus end-to-end smoke (smoke_realistic_corpus, 30 asserts,
   suite now 48): dirty DOCX report / PPTX deck / untagged PDF + clean
   control. Pins the buyer invariants: pipeline never crashes on mixed
   content; violations STRICTLY DECREASE after remediate-all; headline codes
   (title/alt/lists/tables/PDF_UNTAGGED) GONE from outputs; NO new violation
   codes appear post-fix; clean doc stays at zero before AND after.
   Sweep verdict: engine correct everywhere it was probed (the one "failure"
   was a fixture artifact — python-pptx stamps descr=filename, which the
   engine rightly calls ALT_TEXT_NOT_DESCRIPTIVE and auto-fixes).
10. Sales surfaces synced to reality: README "eight analyzers" -> the actual
   19 with capability summary; landing "Fixes, not just findings" now names
   typed-list conversion.

## Known remaining

- PDF fake-list conversion via this action (PDF lists are already rebuilt by
  the ua_tagger when tagging untagged PDFs — separate path, works today).

## Verification gates (run before every push)

- backend: `python run_smoke.py` (expect 46/46), `python -m pytest tests/ -q`
- frontend: `node_modules/.bin/tsc --noEmit`, `npx expo export -p web`
- live (optional, for API-visible changes):
  `python -m uvicorn app.main:app --port 8099` +
  `python -m app.devtools.verify_live_site --api-url http://127.0.0.1:8099`
