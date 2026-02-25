# Production Foundations

## Target Architecture

- FastAPI backend
- Relational DB via `DATABASE_URL` (sqlite in dev, Postgres target in prod)
- Artifact/object storage via storage provider abstraction:
  - `local` for dev
  - `s3` for prod

## Deployment Checklist (Staging/Prod)

1. Set `APP_ENV=production`
2. Configure explicit `CORS_ALLOW_ORIGINS` (no wildcard)
3. Set `MAX_UPLOAD_MB` to approved policy limit
4. Set `STORAGE_PROVIDER=s3`
5. Set `AWS_REGION`, `S3_BUCKET`, optional `S3_PREFIX`
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
