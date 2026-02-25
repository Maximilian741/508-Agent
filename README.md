# 508-Agent

A web-first accessibility remediation platform for PDF, DOCX, and PPTX with deterministic analysis, safe automated fixes, and human-in-the-loop manual review.

## Quickstart

### Prerequisites

- Python 3.13+
- Node.js 18+

### Backend

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python dev_run.py
```

Notes:
- `dev_run.py` finds a free port (usually `8000`) and writes it to:
  - `backend/.runtime/backend_url.txt`
  - `frontend/frontend/public/backend_url.txt`

### Frontend (Expo web)

```bash
cd frontend/frontend
npm install
npm run web
```

Open the URL shown by Expo (commonly `http://localhost:8081`).

## Core Workflow (Happy Path)

1. Upload a document (`.pdf`, `.docx`, `.pptx`) in the Documents screen.
2. Start scan.
3. Review issues in Scan Results.
4. Apply deterministic fixes.
5. Review Fix Report (before/after/delta).
6. Resolve manual-review items.
7. Re-apply fixes when manual approvals should be applied (for supported rules such as approved PDF alt text updates).

## Repository Layout

- `backend/`: FastAPI service and deterministic remediation engine.
- `backend/app/api/`: API routes (`documents`, `manual_review`, `scan`, `remediate`, `policies`, `health`).
- `backend/app/persistence/`: sqlite3 persistence layer (`db.py`).
- `backend/app/parsers/`: format parsers (PDF/DOCX/PPTX).
- `backend/app/devtools/`: smoke suites and API contract checks.
- `backend/tests/`: unit tests.
- `frontend/frontend/`: Expo React Native app (web-first UI).
- `docs/`: architecture, development, policies, and operations docs.

## Persistence and Artifacts

Runtime state is persisted by default in:

- SQLite DB: `backend/.runtime/508_agent.db`

Generated files are stored in:

- uploads: `backend/.runtime/uploads/<doc_id>/...`
- fixed outputs: `backend/.runtime/fixed/<doc_id>/...`
- reports/tag trees: `backend/.runtime/results/<doc_id>/...`

Source of truth is DB-backed persistence; filesystem stores binary artifacts and exported JSON reports.

## API Overview

Base routes (current backend):

### Health
- `GET /health`
- `GET /healthz`

### Documents / Jobs
- `POST /documents/upload`
- `GET /documents`
- `POST /documents/{doc_id}/scan`
- `GET /jobs/{job_id}`
- `POST /jobs/{job_id}/policy`
- `GET /jobs/{job_id}/score`
- `GET /documents/{doc_id}/issues`
- `GET /documents/{doc_id}/manual-review`
- `POST /documents/{doc_id}/apply-fixes`
- `GET /documents/{doc_id}/summary`
- `GET /documents/{doc_id}/tag-tree`
- `GET /documents/{doc_id}/fix-report`
- `GET /documents/{doc_id}/debug-issues`
- `GET /documents/{doc_id}/diff`

### Downloads
- `GET /documents/{doc_id}/download`
- `GET /documents/{doc_id}/pdf`
- `HEAD /documents/{doc_id}/pdf`
- `GET /documents/{doc_id}/pdf-fixed`
- `HEAD /documents/{doc_id}/pdf-fixed`
- `GET /documents/{doc_id}/file-fixed`
- `GET /documents/{doc_id}/pdf-rebuilt`
- `HEAD /documents/{doc_id}/pdf-rebuilt`

### Manual Review
- `GET /manual-review`
- `PATCH /manual-review/{item_id}`
- `DELETE /manual-review`

### Policies
- `GET /api/policies`
- `GET /api/policies/{policy_id}`

### Legacy/Tree APIs
- `POST /scan`
- `POST /remediate`

## Tests and Smoke Commands

From `backend/`:

```bash
python -B -m app.devtools.run_smoke_suite
python -B -m app.devtools.smoke_api_contract
python -B -m unittest tests.test_policies_api tests.test_job_policy_snapshot -v
```

From `frontend/frontend/`:

```bash
npm exec tsc -- --noEmit
```

## Troubleshooting

### `GET /documents/{doc_id}/fix-report` returns 404

Expected until `POST /documents/{doc_id}/apply-fixes` has run at least once for that document/job.

### Manual review page is empty

This is valid when no unresolved items exist. Also confirm you are on the intended scope (current document vs all unresolved).

### Runtime files seem missing

All runtime DB/artifacts are under `backend/.runtime/` by default. If you run backend from a different working directory, verify env vars and paths:

- `DATABASE_URL`
- `DATABASE_PATH`

### Backend starts but frontend cannot reach it

Run `python dev_run.py` from `backend/` so URL files are written to frontend public assets.

### `ModuleNotFoundError: No module named 'docx'`

Install backend requirements again:

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
```
