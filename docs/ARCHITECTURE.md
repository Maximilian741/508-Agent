# Architecture

## Principles

- Deterministic first: rule detection and safe fixes are reproducible.
- Human-in-the-loop for semantic ambiguity: unresolved or risky transformations are manual-review items.
- Preserve originals: apply-fixes always writes copy artifacts.
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

### Apply Fixes

1. `POST /documents/{doc_id}/apply-fixes` generates fixed artifact copy.
2. For PDF, optional rebuild mode may generate a rebuilt PDF.
3. Re-analysis runs on fixed/rebuilt artifact and stores `after` issues.
4. Delta is computed (`fixed`, `remaining`, `introduced`).
5. Fix report and manual review items are persisted.
6. Baseline/post-fix scores are updated for latest job.

### Manual Review

1. Queue endpoints expose unresolved items.
2. Human approves/rejects items.
3. Approved text (e.g., missing alt text) can be applied on next apply-fixes cycle for supported paths.
4. Optional AI assist (`POST /documents/{doc_id}/ai-review`) can propose/apply for a gated subset:
   - v1 scope: PDF missing alt text only.
   - AI never bypasses deterministic validators.
   - Applied decisions are auditable via persisted AI fields.

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
