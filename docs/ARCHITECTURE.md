# Architecture

## Principles

- Deterministic first: rule detection and safe fixes are reproducible.
- Human-in-the-loop for semantic ambiguity: unresolved or risky transformations are manual-review items.
- Preserve originals: remediation always writes a copy artifact; the upload is never modified.
- Auditability: before/after issues, fix reports, policy snapshots, and manual-review decisions are persisted.
- Additive evolution: keep API shape stable and extend capabilities without breaking existing PDF flow.

## Data Model (Current)

Core persisted entities (sqlite3 in `backend/.runtime/508_agent.db`):

- `documents`: uploaded docs and artifact paths.
- `scan_jobs`: job status/progress.
- `issues`: normalized issues by phase (`before`, `after`).
- `fix_reports`: full fix report payload per document.
- `manual_review`: manual review queue items and resolution state.
- `policy_packs`: versioned policy definitions.
- `job_policy_snapshot`: immutable policy snapshot per job.
- `job_scoring`: stored score outputs by pass type.

## Flows

### Upload

1. `POST /documents/upload` stores original file under `.runtime/uploads/<doc_id>/...`.
2. Document metadata/path is persisted.

### Scan

1. `POST /documents/{doc_id}/scan` queues job.
2. Policy snapshot is attached to job at start.
3. Analyzer runs by doc type (PDF/DOCX/PPTX) and stores `before` issues.
4. Baseline score can be computed from policy snapshot + issues.

### Apply Fixes (retired)

`POST /documents/{doc_id}/apply-fixes`, `/finalize`, `/ai-review` and the fixed/rebuilt download routes return 410. They wrote fixes (including a filename-guessed title and an assumed en-US language) and served the result with no credit check, and `ai-review` called OpenAI outside the per-job cost cap. Remediation runs only through `POST /pipeline/remediate`, which prices the job before any work, runs the approved fixes through the executors and format writers, and charges only for fixes the writer persisted into the output file.

### Manual Review

1. Queue endpoints expose unresolved items, including findings rejected during `/pipeline/remediate`.
2. Human approves/rejects items. The decision is recorded for audit; it does not change any file.

### Policy Snapshot + Scoring

1. Job points to immutable `job_policy_snapshot`.
2. Score engine uses policy thresholds, severity weights, overrides, and coverage settings.
3. `GET /jobs/{job_id}/score` returns stored/computed pass outputs (`baseline`, `post_fix`, `post_manual` if available).

## Fixable vs Manual Review Boundaries

## Automatically fixable (safe deterministic)

- Metadata defaults (title/language where supported).
- Some PDF structural/metadata operations under strict deterministic constraints.
- Explicit approved manual alt-text injection into PDF structure where anchors resolve.

## Manual review required

- Semantic intent decisions (meaningful alt text authoring, reading order semantics in ambiguous content).
- Complex heading/content restructuring in DOCX/PPTX where deterministic safety is not guaranteed.
- Anything likely to alter meaning, not just structure.

## AI Gating Model

- Deterministic analyzers/fixers remain source of truth.
- AI is optional and policy-safe:
  - propose phase writes suggestions + confidence + validator state only.
  - apply phase only approves when deterministic validators pass and confidence threshold is met.
- Failed validation or weak context is explicitly escalated back to manual review.

Policy and UI should present these boundaries clearly so users know what is guaranteed and what needs human validation.
