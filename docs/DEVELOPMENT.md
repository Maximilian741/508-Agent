# Development

## Rule Development Guidelines

A new rule should be:

- Deterministic and testable.
- Format-aware (PDF/DOCX/PPTX constraints differ).
- Mapped to a stable `ruleId`.
- Expressed in normalized issue shape used by frontend.

Issue shape requirements:

- `id`
- `ruleId`
- `title`
- `severity` (`error|warning|info`)
- `description`
- `locationHint`
- `recommendation`
- `evidence` (optional dict with anchors/page/slide/node identifiers where available)

## Adding a Rule

1. Add parse/analyzer logic in `backend/app/api/documents.py` analyzer path (`_analyze_pdf`, `_analyze_docx`, `_analyze_pptx`) or parser helpers.
2. Reuse normalized issue model.
3. Ensure rule IDs are stable and human-readable.
4. Add or update tests/smoke coverage for regression risk.

## Rule ID and Severity Conventions

- Keep `ruleId` stable once released.
- Prefer snake_case in current pipeline conventions.
- Use severity as signal of remediation urgency:
  - `error`: strong compliance blocker
  - `warning`: likely accessibility gap
  - `info`: low-severity observation

Policy packs may remap severity for scoring/reporting; raw analyzer output should still be consistent and deterministic.

## Adding a Safe Fix

1. Implement deterministic fix in apply-fixes path by doc type (`_apply_pdf_fixes`, `_apply_docx_fixes`, `_apply_pptx_fixes`).
2. Never modify original source file in place.
3. Record applied fix details in fix report (`appliedFixes`).
4. If confidence is insufficient, generate manual-review item instead of forcing fix.

## Idempotence Expectations

- Re-running scan without content changes should produce stable issue keys.
- Re-running apply-fixes should not endlessly duplicate structural edits.
- Delta computation should remain stable for equivalent inputs.

## Auditability Expectations

Every remediation operation should preserve traceability:

- before/after issue snapshots persisted
- fix report persisted
- manual-review decisions persisted
- artifact paths and metadata persisted
- policy snapshot persisted per job

## Local Validation Workflow

From `backend/`:

```bash
python -B -m app.devtools.run_smoke_suite
python -B -m unittest tests.test_policies_api tests.test_job_policy_snapshot -v
```

From `frontend/frontend/`:

```bash
npm exec tsc -- --noEmit
```

Prefer adding focused tests for every new rule/fix path that can regress existing behavior.
