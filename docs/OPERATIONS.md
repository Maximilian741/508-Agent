# Operations

## Runtime and Environment

Default runtime paths:

- DB: `backend/.runtime/508_agent.db`
- Uploads: `backend/.runtime/uploads`
- Fixed artifacts: `backend/.runtime/fixed`
- Reports: `backend/.runtime/results`

Backend URL handoff file (for frontend web):

- `backend/.runtime/backend_url.txt`
- mirrored to `frontend/frontend/public/backend_url.txt` by `backend/dev_run.py`

## Environment Variables

Current effective variables:

- `DATABASE_URL`
  - If set to `sqlite:///...`, path is used for sqlite3 DB.
- `DATABASE_PATH`
  - Fallback explicit sqlite file path.

If both unset, default is `./.runtime/508_agent.db` relative to backend working directory.

## Local Runbook

1. Start backend:

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python dev_run.py
```

2. Start frontend:

```bash
cd frontend/frontend
npm install
npm run web
```

3. Health checks:

- `GET /healthz`
- Upload + scan + apply-fixes cycle
- `GET /documents/{doc_id}/fix-report` after apply-fixes

## Troubleshooting Signals

- `fix-report 404`: expected before first apply-fixes for that doc/job.
- manual-review empty: no unresolved items currently persisted.
- artifact 404 (`pdf-fixed`/`file-fixed`): fixed artifact not generated yet or path not present.
- path confusion: confirm backend cwd and `.runtime` location.

## Production Readiness Checklist (Future)

## Data and storage

- Move DB from sqlite to Postgres (`DATABASE_URL` to managed Postgres).
- Move artifacts from local filesystem to object storage (S3-compatible).
- Keep policy snapshots, scoring, fix reports persisted in relational DB.

## Execution model

- Move long-running scan/fix operations to background queue workers.
- Add retry/backoff and dead-letter visibility.

## Security and reliability

- HTTPS termination (ALB/CloudFront/Ingress + cert management).
- Authn/authz for user/org/workspace separation.
- Audit logs and immutable evidence metadata.
- Rate limiting and upload validation.
- Monitoring/alerting (errors, latency, queue depth).

## Deployability

- Containerize backend and worker roles.
- Use managed secrets.
- Add health checks and rolling deploy strategy.
- Ensure migrations are applied before app startup.

## Backup and retention

- DB snapshots/backups with tested restore.
- Object storage lifecycle/retention policy.
- Evidence bundle retention rules by tenant/compliance needs.
