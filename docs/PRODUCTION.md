# Production Foundations

## Target Architecture

- FastAPI backend
- Relational DB via `DATABASE_URL` (sqlite in dev, Postgres supported in prod/dev)
- Artifact/object storage via storage provider abstraction:
  - `local` for dev
  - `s3` for prod

## Deployment Checklist (Staging/Prod)

1. Set `APP_ENV=production`
2. Configure explicit `CORS_ALLOW_ORIGINS` (no wildcard)
3. Set `MAX_UPLOAD_MB` to approved policy limit
4. Set `STORAGE_PROVIDER=s3`
5. Set `AWS_REGION`, `S3_BUCKET`, optional `S3_PREFIX`
6. Set `PRESIGN_EXPIRES_SECONDS` (short TTL recommended)
6. Keep S3 bucket private; use short-lived presigned URLs
7. Run migrations before app startup (`alembic upgrade head`)
8. Enable centralized logs and request-id tracing
9. Enforce TLS at ingress/load balancer
10. Add edge rate limiting/WAF policy

## Security Baseline

- Upload validation (extension + MIME hint + signature)
- Request-size guard (`413` on oversized uploads)
- Secure headers middleware
- Request IDs on every response (`X-Request-Id`)
- Structured request log line with status and latency

## AWS Hardening Notes

- Use IAM least privilege for S3 access
- Keep bucket policy private (no public objects)
- Use KMS encryption at rest for S3 and RDS
- Rotate secrets in AWS Secrets Manager
- Back up DB and test restore procedures
- Put API behind ALB/CloudFront with TLS termination

## Non-goals in this sprint

- Auth/workspaces
- Worker queue migration
- Full IaC provisioning (CDK/Terraform)

## DATABASE_URL formats

- sqlite: `sqlite:///./.runtime/508_agent.db`
- postgres: `postgresql+psycopg://user:pass@host:5432/dbname`

## Docker Compose Postgres

```bash
docker compose up --build
```

Compose now runs backend with Postgres + MinIO-backed S3 mode.
MinIO console: `http://localhost:9001`.

## No AWS Fees (local mode)

- Local dev can run with sqlite + local storage only.
- MinIO/localstack can emulate S3 locally if needed.
- This compose setup uses MinIO only (no AWS billing).
- AWS costs are only incurred when using real AWS resources (RDS/S3/etc).

## Additional Staging Env Vars

- `S3_ENDPOINT_URL` (MinIO/localstack endpoint)
- `S3_FORCE_PATH_STYLE=true` (recommended for MinIO)
- `PRESIGN_EXPIRES_SECONDS=3600`
- `ENABLE_DEV_STORAGE_ENDPOINT=false` (should remain false in production)

## Troubleshooting

- Downloads return `302`: expected in S3 mode; clients follow redirect to presigned URL.
- Files missing after restart in S3 mode: confirm DB stores `storage_key:` refs, not local paths.
- `/storage/{key}` unavailable in prod: expected unless explicitly enabled for dev diagnostics.
