# LAUNCH.md — go-live runbook

The single "start here" page for putting 508 Agent into production as a **public,
self-serve SaaS**. It's the critical path plus the checks to run; the granular
click-by-click steps live in [`deploy/SHIP-CHECKLIST.md`](../deploy/SHIP-CHECKLIST.md).

---

## 0. What's already been verified (so you know what's solid)

These were validated in CI/dev and don't need re-checking — they tell you the app
itself is launch-ready:

- **Backend suite** green: `cd backend && python run_smoke.py` (full smoke suite)
  and `python -m pytest tests/ -q`.
- **Frontend production bundle** builds: `expo export -p web` succeeds and inlines
  `EXPO_PUBLIC_API_URL` correctly.
- **Migration chain** applies cleanly on a fresh DB (`alembic upgrade head` →
  `0011_analysis_results`).
- **The full user journey works over real HTTP** — sign-in → upload → analyze →
  remediate → download, with fixes (alt text, headings, links, table headers)
  baked into the downloaded file.

**The one thing only you can verify** is the Docker image build + `docker compose
up` on the host (no Docker in CI). Do that on the VPS in step 4.

---

## 1. Provision

- A small VPS (2 vCPU / 4 GB is plenty to start) with Docker + Docker Compose.
- A domain on Cloudflare. You'll expose two hostnames via a **Cloudflare Tunnel**
  (no inbound ports): `app.yourdomain.com` (frontend) and `api.yourdomain.com`
  (backend). See [`deploy/cloudflare-tunnel.md`](../deploy/cloudflare-tunnel.md).
- A Stripe account (live mode) for billing.

---

## 2. Configure `.env`

Copy the **production** template (NOT the repo-root dev one) and fill it in:

```bash
cp deploy/.env.example .env
```

Generate the secrets:

```bash
# APP_SECRET (HMAC for sessions + signed download URLs)
python -c "import secrets; print(secrets.token_urlsafe(48))"
# POSTGRES_PASSWORD
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Required keys (the compose file refuses to start without the first three):

| Key | Value |
| --- | --- |
| `APP_ENV` | `production` (enables alembic-only migrations, strict CORS, required secret) |
| `APP_SECRET` | the 48-char secret above |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `agent` / generated / `agentdb` |
| `DATABASE_URL` | `postgresql+psycopg://agent:<password>@db:5432/agentdb` |
| `EXPO_PUBLIC_API_URL` | `https://api.yourdomain.com` (build-time, public) |
| `CORS_ALLOW_ORIGINS` | `https://app.yourdomain.com` (exact origin — never `*`) |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | from Stripe (webhook secret is from step 5) |
| `STRIPE_PRICE_*` | the price ids for your packs + subscriptions |
| `ADMIN_EMAILS` | your email(s) — grants the Admin tab + audit log |
| `CF_TUNNEL_TOKEN` | from the Cloudflare tunnel (step 3) |

> **Leave `CLOUDFLARE_ACCESS_AUD` empty.** This is a public self-serve product;
> your users authenticate with the app's own accounts. Setting an Access AUD puts
> a Cloudflare login in front of the whole site and would 401 your customers, the
> Stripe webhook, and public certificate links. (Only set it for a private
> internal instance — see SHIP-CHECKLIST Step 3.)

Optional: `ANTHROPIC_API_KEY` (better alt-text than the built-in heuristic),
`SMTP_*` + `PUBLIC_BASE_URL` (email verification; logs to stdout without SMTP).

---

## 3. Cloudflare Tunnel

Create the tunnel and add two public hostnames (note the ports — the frontend
container listens on **8080**, not 80):

- `app.yourdomain.com` → `http://frontend:8080`
- `api.yourdomain.com` → `http://backend:8000`

Put the tunnel token in `.env` as `CF_TUNNEL_TOKEN` and uncomment the
`cloudflared` service in `docker-compose.yml`.

---

## 4. Bring up the stack (verify the build here)

```bash
docker compose up -d --build
docker compose ps            # db, backend, frontend, cloudflared all healthy
docker compose logs backend  # alembic should report upgrade to 0011_analysis_results
```

The backend container's healthcheck hits `/readyz` (DB-backed), so "healthy"
means it can actually serve.

---

## 5. Stripe products + webhook

Create these **exact** Prices in the Stripe dashboard (the billing page
advertises these amounts — a mismatch means the customer sees one number and
is charged another):

| Price id env var | Type | Amount | Grants |
| --- | --- | --- | --- |
| `STRIPE_PRICE_STARTER` | one-time | **$5.00** | 50 credits |
| `STRIPE_PRICE_PRO` | one-time | **$15.00** | 250 credits |
| `STRIPE_PRICE_STUDIO` | one-time | **$50.00** | 1,300 credits |
| `STRIPE_PRICE_TEAM` | recurring monthly | **$99.00/mo** | 1,000 credits/mo |
| `STRIPE_PRICE_TEAM_ANNUAL` | recurring yearly | **$990.00/yr** | 12,000 credits up front |
| `STRIPE_PRICE_BUSINESS` | recurring monthly | **$499.00/mo** | 6,000 credits/mo |
| `STRIPE_PRICE_BUSINESS_ANNUAL` | recurring yearly | **$4,990.00/yr** | 72,000 credits up front |

Then add a webhook endpoint:

- URL: `https://api.yourdomain.com/billing/webhook`
- Events: `checkout.session.completed`, `invoice.payment_succeeded`,
  `customer.subscription.updated`, `customer.subscription.deleted`

Copy the signing secret into `STRIPE_WEBHOOK_SECRET` and restart the backend:
`docker compose up -d backend`.

---

## 6. Post-deploy smoke (automated)

Run the live-site smoke against your public API. It uses a throwaway account and
does **not** spend money:

```bash
cd backend
python -m app.devtools.verify_live_site --api-url https://api.yourdomain.com
```

It checks `/healthz`, `/readyz`, a throwaway sign-in, and a real
`/pipeline/analyze` of a generated document. Exit code 0 = good; wire it into
your deploy script if you like.

Then the **one manual check** it can't do (needs a card): open the app, buy a
Starter pack with the Stripe test card `4242 4242 4242 4242`, and confirm your
credit balance updates and the webhook recorded the grant.

---

## 7. Backups, retention & alerting

- **Backups:** add a nightly `pg_dump` cron (SHIP-CHECKLIST Step 10). Do this
  before you take real customers.
- **R2 retention:** if using Cloudflare R2 for storage, add a **1-day object
  lifecycle rule** on the bucket. The app's cleanup task sweeps the local
  pipeline directory after `PIPELINE_ARTIFACT_TTL_SECONDS` (24h), and the
  lifecycle rule makes the same guarantee hold for R2 objects — the privacy
  page promises "deleted after ~24 hours", so this rule is required.
- **Alerting (5 minutes, do not skip):** point a free uptime monitor
  (UptimeRobot, Cloudflare Health Checks, etc.) at
  `https://api.yourdomain.com/readyz` with an email/SMS alert. There is no
  built-in alerting; without this, the first person to tell you the site is
  down will be a customer.
- **Support mailboxes:** create `support@`, `privacy@`, and `security@` on
  your domain (aliases to your inbox are fine) — the Help, Privacy, Terms and
  SECURITY.md pages reference them.

---

## Common failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Billing endpoints return `503 billing_not_configured` | `STRIPE_SECRET_KEY` unset | set the Stripe keys, restart backend |
| Customers / webhook / cert links get `401` | `CLOUDFLARE_ACCESS_AUD` is set | leave it **empty** for public launch |
| Tunnel hostname returns `502` | tunnel points at `frontend:80` | use `frontend:8080` |
| Container "healthy" but requests error | DB down / `DATABASE_URL` wrong | check `/readyz`, `docker compose logs db` |
| Sessions/links break after restart | `APP_SECRET` unset (random per-process) | set a fixed `APP_SECRET` |
| Web app calls `localhost` in prod | `EXPO_PUBLIC_API_URL` unset at build | set it before `docker compose build` |

---

See also: [`deploy/SHIP-CHECKLIST.md`](../deploy/SHIP-CHECKLIST.md) (full steps),
[`deploy/cloudflare-tunnel.md`](../deploy/cloudflare-tunnel.md),
[`docs/deploy.md`](deploy.md), [`docs/OPERATIONS.md`](OPERATIONS.md).

## OCR for scanned PDFs (optional feature)

Scanned, image-only PDFs are detected out of the box (SCANNED_DOCUMENT_NO_TEXT)
and queued for manual remediation. To let the pipeline FIX them automatically
(invisible position-matched text layer + structure tagging):

1. Install Tesseract on the backend host/image: `apt-get install -y tesseract-ocr`
   (the `pytesseract` wrapper ships in requirements.txt already).
2. Set `OCR_ENABLED=true` in the backend environment.
3. Verify: upload a scanned PDF, approve the OCR fix, download — the output is
   searchable and tagged; re-uploading it shows the scanned flag resolved.

Without the binary the feature degrades gracefully: the action is skipped with
an explicit note and the score never claims it.

