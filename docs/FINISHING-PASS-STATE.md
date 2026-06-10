# Launch finishing pass — state (working tree, pre-verification)

> Delete this file once `finish-launch-pass.ps1` has run green and the PR is
> merged. It exists so no context is lost if the session ends.

## Where things stand

ALL code changes for the pre-sale finishing pass are **implemented and sitting
uncommitted in the working tree** (branch `feat/scanned-document-detection`,
which == merged main; ~40 files). They could NOT be executed/verified in the
authoring session because its command-safety classifier (bound to
`claude-fable-5[1m]`) was down all session — file edits worked, command
execution did not. A sonnet subagent statically verified all wiring (imports,
exports, registrations, column names): coherent.

## To verify + ship (one command, repo root)

    powershell -ExecutionPolicy Bypass -File .\finish-launch-pass.ps1

Cleans stray QA files → runs `smoke_launch_finishing` +
`smoke_detection_accuracy` + full smoke suite + pytest → `tsc --noEmit` +
production `expo export` → creates branch `feat/launch-finishing-pass`,
commits with `COMMIT_MSG_LAUNCH.txt`, pushes, opens PR with
`PR_BODY_LAUNCH.md`, then self-deletes the three helper files.
If a check fails: fix, re-run. Most-likely failure points (unverified
empirically): the new smoke assertions' exact strings, and audit.tsx (largest
diff: funnel retry, achievements, a11y labels).

## What changed (summary — full detail in COMMIT_MSG_LAUNCH.txt)

- Funnel: anonymous-upload 401 → SignInModal + auto-retry (audit.tsx,
  client.ts err.status); honest homepage copy; dev cards hidden (__DEV__ /
  backendUrlSource); customer-safe unreachable copy (useAppStore + audit.tsx).
- Money: billing.tsx truthful features/seats/refunds + legal links;
  _PLAN_PRICE_USD 99/499; Stripe price tables in deploy/.env.example +
  docs/LAUNCH.md.
- Trust: NEW LICENSE, SECURITY.md, app/terms.tsx, app/privacy.tsx;
  security.tsx + about.tsx rewritten to real auth/retention model.
- Password reset: backend /auth/request-password-reset + /auth/reset-password
  (pr_ tokens, 1h, single-use, no enumeration, cross-purpose guard);
  SignInModal "Forgot password?"; app/reset-password.tsx;
  account.ts requestPasswordReset/confirmPasswordReset; min-8 client-side.
- Engine: SkippedHeadingLevelAnalyzer + ReadingOrderAnalyzer +
  TableHeaderScopeAnalyzer deregistered (duplicate/dead);
  improve_link_text_executor shares analyzer predicate (bare URLs fixable);
  set_document_title_executor derives real titles; NEW PDF_UNTAGGED flag +
  UntaggedPdfAnalyzer + TAG_PDF_STRUCTURE action/executor (persisted for pdf;
  parser records pdf_tagged/page_count/total_text_chars/image_only_pages);
  DOCUMENT_NO_HEADINGS now DOCX-only; pdf images only flagged when DRAWN
  (_names_drawn_on_page); ua_tagger doc-wide heading levels
  (_collect_doc_heading_levels).
- Abuse/robustness: grant-starter needs verified email when SMTP set;
  pipeline analyze/remediate/writer in run_in_threadpool; pg advisory lock on
  overage.
- Frontend polish: admin audit log wired (account.ts getAuditLog);
  achievements wired (first_certificate replaces first_share_link; streak;
  triage counter); toast live region; Hero h1; labels for chips/switches/TOC/
  progress; catalog 7 autoFix entries made per-format truthful + PDF_UNTAGGED
  entry; cert naming "remediation certificate"; landing differentiators +
  pricing/terms/privacy footer; README pricing/subscriptions; SHIP-CHECKLIST
  step 9 de-conflicted; app.json "508 Agent"; console.logs + ErrorBoundary
  stack __DEV__-gated; demo "Sample" chip; manual-review link from audit;
  keyboard "?" fix; contrast.tsx @ts-ignore removed.

## Round 2 (also in this working tree, after the script was written)

- NEW TEXT_STYLED_AS_HEADING (WARNING, 1.3.1): docx_parser
  _looks_like_fake_heading (Title/Subtitle style, or short all-bold text with
  explicit >=14pt) marks properties["looks_like_heading"];
  TextStyledAsHeadingAnalyzer (heading_analyzer.py) flags it; registered;
  catalog card added; report disclosure updated; smoke_launch_finishing case
  4e covers fires/precision/Title-style.
- batch.tsx anonymous-401: friendly row message + one-time sign-in toast
  (was raw 401 JSON per row).
- SignInModal docstring updated to the real flow (password required, forgot-
  password link).

## Known remaining (after ship)

- Favicon still the Expo default (needs image generation).
- SLIDE_TITLE_MISSING shows 1 flag per deck (magnitude hidden).
- PPTX slide titles double-emit as Heading+Paragraph nodes (node-count
  inflation only).
- Journey-QA live-HTTP audit never completed (agent stopped); its scope is
  mostly covered by existing smokes + verify_live_site.
