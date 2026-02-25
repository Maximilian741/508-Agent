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
- `CORS_ALLOW_ORIGINS` (comma-separated)
- `MAX_UPLOAD_MB` (default `25`)
- `REQUIRE_STRICT_CORS` (`true` default)

## Local (sqlite + local storage)

```bash
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python dev_run.py
```

## Local Compose (postgres container + backend)

```bash
docker compose up --build
```

Note: backend still defaults to sqlite in compose for compatibility. Postgres container is included for migration/testing groundwork.

## Health Checks

- `GET /healthz`
- Upload -> Scan -> Apply Fixes -> Manual Review -> Evidence Bundle
- `GET /documents/{doc_id}/fix-report` after apply-fixes
- `GET /documents/{doc_id}/status`

## Troubleshooting

- `fix-report 404`: expected until apply-fixes runs for that document.
- manual review empty: no unresolved items persisted.
- storage download 404: referenced key/path missing from storage backend.
- upload rejected (400/413): failed allowlist/signature check or exceeded `MAX_UPLOAD_MB`.
- CORS blocked: verify `CORS_ALLOW_ORIGINS` has exact frontend origin.
