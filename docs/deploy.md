# Deploy 508 Agent in 30 minutes

This is the practical, step-by-step deploy runbook. Skip nothing the first time.

---

## What you need before you start

1. A domain you own (any registrar, $12/year).
2. A VPS - any of these work:
   - Hetzner CX21 (~5 EUR / month) - smallest box that's comfortable.
   - DigitalOcean Basic Droplet ($6/month).
   - AWS Lightsail $5 instance.
   - Linode Nanode 1 GB ($5/month).
3. A free [Cloudflare](https://dash.cloudflare.com) account with your domain added.
4. An [Anthropic API key](https://console.anthropic.com) (primary AI provider).
5. A [Stripe account](https://dashboard.stripe.com) (for billing).

Total monthly run cost for a single-instance deploy serving up to ~1000 docs/month: roughly **$5-10** in infra plus AI usage (about $0.04 per document on average).

---

## Step 1 - Spin up the server (5 min)

Pick a Linux image: **Ubuntu 22.04 LTS** is the assumption throughout. SSH in as root.

Install Docker and Docker Compose:

```
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin
```

Confirm:

```
docker --version
docker compose version
```

Create a non-root deploy user (don't run the app as root):

```
adduser --disabled-password --gecos "" agent
usermod -aG docker agent
su - agent
```

---

## Step 2 - Pull the repo (2 min)

```
cd ~
git clone https://github.com/Maximilian741/508-Agent.git
cd 508-Agent
```

---

## Step 3 - Set up the environment file (5 min)

```
cp .env.example .env
nano .env
```

Fill in **at minimum**:

```
APP_ENV=production
APP_SECRET=<run: openssl rand -hex 32>
POSTGRES_PASSWORD=<run: openssl rand -hex 24>
ANTHROPIC_API_KEY=sk-ant-...
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
CORS_ALLOW_ORIGINS=https://yourdomain.com
EXPO_PUBLIC_API_URL=https://api.yourdomain.com
PUBLIC_BASE_URL=https://yourdomain.com
ADMIN_EMAILS=you@yourdomain.com
```

Notes:

- `APP_SECRET` signs session JWTs - generate it once, never share it, never commit it. The backend refuses to start outside development without it.
- `POSTGRES_PASSWORD` is **required** - `docker compose` will not start without it. `DATABASE_URL` is auto-derived from it (db name `agentdb`); set `DATABASE_URL` explicitly only if you use a managed/external Postgres.
- `EXPO_PUBLIC_API_URL` is the public URL the browser uses to reach the backend (see step 6). It is baked into the frontend bundle at **build time** and is **required**.
- `PUBLIC_BASE_URL` is used to build links in verification emails. `ADMIN_EMAILS` (comma-separated) grants the audit-log/admin surface.
- The Stripe webhook secret comes from step 7 below; leave it blank for now and add it after the webhook is registered.
- `OPENAI_API_KEY` is optional (fallback provider). Anthropic alone is fine.

---

## Step 4 - Set up the database (3 min)

The `docker-compose.yml` in the repo already includes a Postgres service. On first boot the database is empty - the backend applies the schema via **Alembic migrations** (the Docker entrypoint runs `alembic upgrade head` before uvicorn starts; a failed migration aborts startup by design).

```
docker compose up -d db
docker compose logs -f db
```

Wait until you see `database system is ready to accept connections`. Ctrl-C to stop tailing.

---

## Step 5 - Build and start the backend (3 min)

```
docker compose build backend
docker compose up -d backend
docker compose logs -f backend
```

You should see Alembic apply the migrations, then uvicorn boot. Ctrl-C to detach.

The backend is not published on a host port (the tunnel reaches it on the compose network), so sanity-check it from inside the container:

```
docker compose exec backend curl -fsS http://localhost:8000/healthz
```

Expect `{"ok":true}`.

---

## Step 6 - Set up the Cloudflare Tunnel (8 min)

This is the cleanest way to expose the server without opening any ports on your VPS.

In your Cloudflare dashboard:

1. **Zero Trust** -> **Networks** -> **Tunnels** -> **Create a tunnel**.
2. Name it `508-agent`. Save.
3. On the connector page, copy the install command for **Linux 64-bit deb** and run it on your VPS as root.
4. Back in the dashboard, you should see your VPS connected within ~30 seconds.
5. Add a public hostname for the **API**:
   - **Subdomain**: `api`.
   - **Domain**: yourdomain.com.
   - **Service**: `HTTP` and `backend:8000` (when the `cloudflared` service runs on the compose network; use `localhost:8000` only if you publish the backend port on the host).
6. Save. (You'll add the web-app hostname in step 8.)

Open `https://api.yourdomain.com/healthz` in your browser - you should see `{"ok":true}`.

---

## Step 7 - Register the Stripe webhook (3 min)

The backend has a webhook endpoint at `/billing/webhook`. Stripe needs to know about it to credit user accounts after a successful checkout.

In the Stripe dashboard:

1. **Developers** -> **Webhooks** -> **Add endpoint**.
2. Endpoint URL: `https://api.yourdomain.com/billing/webhook`.
3. Events to send: `checkout.session.completed`, `invoice.payment_succeeded`, `customer.subscription.deleted`, `customer.subscription.updated` (the last three power subscription renewals + cancellations).
4. Save. Stripe shows you a **Signing secret** (`whsec_...`) on the next page.
5. Copy it into your `.env` as `STRIPE_WEBHOOK_SECRET=whsec_...`.
6. Restart the backend so it picks up the new env var:

```
docker compose restart backend
```

Test the webhook from Stripe's dashboard ("Send test webhook") and confirm a 200 in the backend logs.

---

## Step 8 - Build and deploy the frontend (3 min)

The frontend is a static Expo Web build served by nginx (which is already in `docker-compose.yml`).

```
docker compose build frontend
docker compose up -d frontend
```

The frontend container builds the static bundle and nginx serves it on port 8080. Add a **second** Cloudflare Tunnel public hostname for the web app (the API hostname `api.yourdomain.com` was added in step 6):

- `yourdomain.com`      -> `HTTP` `frontend:8080`  (the web app)
- `api.yourdomain.com`  -> `HTTP` `backend:8000`   (the API, from step 6)

This subdomain split matches `EXPO_PUBLIC_API_URL=https://api.yourdomain.com`: the browser loads the app from `yourdomain.com` and calls the API at `api.yourdomain.com` (which is why `CORS_ALLOW_ORIGINS` must list `https://yourdomain.com`). Use the compose service names (`frontend:8080` / `backend:8000`) when `cloudflared` runs on the compose network.

---

## Step 9 - Smoke test the deploy (3 min)

From a different machine (your laptop):

1. Open `https://yourdomain.com` - landing page should render.
2. Click **Run your first audit**, drop in a small PDF.
3. Confirm findings show up.
4. Click **Sign in**, create an account.
5. Click **Apply fixes**, confirm Stripe checkout opens (in test mode if you're using test keys).
6. Pay with `4242 4242 4242 4242` (Stripe test card) for a Starter pack.
7. Confirm the credit balance updates and a remediated file downloads.

If any step fails:

- **Sign-in fails with 500**: migrations may not have run. Check `docker compose logs backend` for an Alembic error (a failed migration aborts startup by design).
- **`docker compose up` exits immediately**: `POSTGRES_PASSWORD` or `EXPO_PUBLIC_API_URL` is unset - both are required.
- **Stripe checkout 404s**: webhook secret is missing or the Stripe public key in the frontend env doesn't match.
- **Remediated file 403s**: signed URLs are expiring too fast - check your server clock with `timedatectl`.

---

## Step 10 - Lock the admin surface (5 min)

The audit-log API is already gated server-side by `ADMIN_EMAILS` (only those accounts get admin access; everything else fails closed). As optional defense-in-depth you can also put the frontend `/admin` page behind Cloudflare Access:

1. In Cloudflare **Zero Trust** -> **Access** -> **Applications** -> **Add an application**.
2. Type: **Self-hosted**.
3. Application domain: `yourdomain.com`, path: `/admin`.
4. Add identity: **One-time PIN** + your email.
5. Save.

Visit `https://yourdomain.com/admin` from incognito - you should hit a Cloudflare Access challenge before the page renders.

---

## What you skipped that you'll want eventually

- **Backups.** Add a nightly `pg_dump` to S3 / B2.
- **Object storage.** Right now files live on the VPS disk. Switch to S3 / R2 / B2 once you cross ~10K documents.
- **Background workers.** Long remediations (50+ page PDFs with images) block the request thread. Add Celery + Redis when you start to feel it.
- **Per-job AI cost cap.** Already wired (see backend config: `MAX_AI_COST_PER_JOB_USD`). Default is $0.50; raise or lower based on your tier mix.
- **Email transactional.** Without `SMTP_HOST`, verification emails log to stdout. Set `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM` (Postmark, Resend, SES, ...) before charging real users.

---

## Rolling out updates

```
cd ~/508-Agent
git pull
docker compose build backend frontend
docker compose up -d backend frontend
docker compose logs -f backend frontend
```

Watch the logs for errors during the rollout. The deploy is zero-downtime for the frontend (nginx switches to the new bundle on container restart) but the backend has ~5 seconds of unavailability during restart.

---

## Day-to-day commands

| What | Command |
|---|---|
| Tail backend logs | `docker compose logs -f backend` |
| Tail frontend logs | `docker compose logs -f frontend` |
| Database shell | `docker compose exec db psql -U agent -d agentdb` |
| Restart backend | `docker compose restart backend` |
| Stop everything | `docker compose down` |
| Run smoke suite in prod | `docker compose exec backend python run_smoke.py` |

---

## Cost ballpark

| Scale | Compute | AI calls | Total/month |
|---|---|---|---|
| 100 docs/month | $5 (1 VPS) | $4 (Anthropic) | **~$10** |
| 1K docs/month | $5 (1 VPS) | $40 | **~$45** |
| 10K docs/month | $30 (3 VPSes + LB) | $400 | **~$430** |
| 100K docs/month | $200 (small fleet) | $4000 | **~$4200** |

Revenue at $0.30/PDF average: 100K docs = $30K, leaving ~85% gross margin.

---

If you get stuck, open an issue at [github.com/Maximilian741/508-Agent/issues](https://github.com/Maximilian741/508-Agent/issues) with the failing log line and your `docker compose ps` output.
