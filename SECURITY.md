# Security Policy

508 Agent processes customer documents, so security reports get priority
attention.

## Reporting a vulnerability

- Email **security@508-agent.app** with a description, reproduction steps, and
  (if applicable) a proof-of-concept. Please do not open a public GitHub issue
  for security reports.
- You will get an acknowledgement within **72 hours** and a status update at
  least every 7 days until resolution.
- Please give us a reasonable window to fix the issue before public
  disclosure. We credit reporters in the fix notes unless you prefer
  otherwise.

## Scope

In scope: this repository (backend API, document engine, web frontend, deploy
configuration) and the hosted service built from it — especially anything
touching authentication/session handling, tenant isolation (one account
reading another's documents), the Stripe billing flow, signed download URLs,
certificate issuance/verification integrity, and document parsing (malformed
PDF/DOCX/PPTX handling).

Out of scope: volumetric denial of service, social engineering, and issues in
third-party services (Stripe, Cloudflare) themselves.

## What we already do

- Passwords hashed with salted scrypt (N=2^15); never stored or logged in
  plaintext.
- Session tokens are HS256 JWTs with issuer/audience checks and a 7-day TTL.
- Per-user document ownership enforced server-side; cross-tenant requests
  return 404.
- Stripe webhooks verified by signature with a replay window; credit grants
  are idempotent.
- Uploads validated by magic bytes + real OOXML structure checks, with
  decompression (zip-bomb) caps.
- Remediated artifacts expire from hosted storage after 24 hours
  (`PIPELINE_ARTIFACT_TTL_SECONDS`).
- Documents are never used to train AI models.

## Supported versions

The `main` branch and the latest deployed release. Older commits are not
patched retroactively.
