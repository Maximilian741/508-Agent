# Operations

## Runtime Paths

Default local runtime paths (relative to `backend/`):

- DB: `.runtime/508_agent.db`
- Uploads/quarantine/work files: `.runtime/uploads`
- Fixed/Rebuilt outputs: `.runtime/fixed`
- Reports: `.runtime/results`
- Local object storage root: `.runtime/storage`
- Evidence bundles: `.runtime/bundles`

## Environment Variables

- `APP_ENV` (`development` by default)
- `APP_VERSION` (`0.1.0` default)
- `DATABASE_URL`
  - default: `sqlite:///./.runtime/508_agent.db`
- `STORAGE_PROVIDER`
  - `local` (default) or `s3`
- `STORAGE_LOCAL_ROOT`
  - default: `backend/.runtime/storage`
- `AWS_REGION`
- `S3_BUCKET` (required when `STORAGE_PROVIDER=s3`)
- `S3_PREFIX` (optional)
- `S3_ENDPOINT_URL` (optional; MinIO/localstack)
- `S3_FORCE_PATH_STYLE` (`true` for MinIO)
- `PRESIGN_EXPIRES_SECONDS` (default `3600`)
- `ENABLE_DEV_STORAGE_ENDPOINT` (default `false`; dev-only local storage route)
- `OPENAI_API_KEY` (optional; enables AI manual-review proposals)
- `OPENAI_MODEL` (optional; default `gpt-4.1-mini`)
- `CORS_ALLOW_ORIGINS` (comma-separated)
- `MAX_UPLOAD_MB` (default `25`)
- `REQUIRE_STRICT_CORS` (`true` default)

## Local (sqlite + local storage)

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python dev_run.py
```

## Local Staging Compose (postgres + minio + backend)

```bash
docker compose up --build
```

MinIO console: `http://localhost:9001`  
Default local creds: `minioadmin / minioadmin` (local-only).

Run E2E smoke (against running backend):

```bash
cd backend
python -B -m app.devtools.e2e_smoke --base-url http://localhost:8000
```

## Health Checks

- `GET /healthz`
- Upload -> Scan -> Apply Fixes -> Manual Review -> Evidence Bundle
- `GET /documents/{doc_id}/fix-report` after apply-fixes
- `GET /documents/{doc_id}/status`

## Troubleshooting

- `fix-report 404`: expected until apply-fixes runs for that document.
- manual review empty: no unresolved items persisted.
- storage download 404: referenced key/path missing from storage backend.
- storage endpoint disabled: `/storage/{key}` is blocked in production unless `ENABLE_DEV_STORAGE_ENDPOINT=true`.
- upload rejected (400/413): failed allowlist/signature check or exceeded `MAX_UPLOAD_MB`.
- download returns 302: expected in S3 mode (presigned URL redirect).
- CORS blocked: verify `CORS_ALLOW_ORIGINS` has exact frontend origin.

## AI Manual Review (Optional, Gated)

- Endpoint: `POST /documents/{doc_id}/ai-review`
  - `mode=propose`: generates `aiDecision` and validation metadata for eligible pending items.
  - `mode=apply`: approves only proposals that pass deterministic validators and confidence threshold.
- Current scope is intentionally narrow: PDF `missing_alt_text` / figure-alt manual review only.
- Safety posture:
  - Deterministic validators gate application.
  - AI output is persisted for audit (`aiDecision`, confidence, validator status, model, timestamps).
  - Unsupported or low-confidence cases are escalated and remain pending.
