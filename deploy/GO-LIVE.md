# Go Live — 508 Agent (single server + Docker)

The fastest real path to a public, paying site. ~Half a day, most of it waiting
on Stripe to verify your business. For the exhaustive version see
[SHIP-CHECKLIST.md](./SHIP-CHECKLIST.md).

---

## What you buy / create first (only you can — accounts + money)

| Thing | Where | Cost | Why |
|---|---|---|---|
| **Domain** | Cloudflare Registrar / Namecheap | ~$12/yr | your address, e.g. `yourdomain.com` |
| **A server (VPS)** | Hetzner CPX21 / DigitalOcean | ~$6–12/mo | runs the whole stack (needs ~2 GB RAM) |
| **Cloudflare account** | cloudflare.com (free) | $0 | TLS + WAF + the tunnel (no open ports) |
| **Stripe account** | stripe.com | per-txn fee | take payments (bank account attached) |
| **Email sender** | Resend / Postmark (free tier) | $0 | verification + password-reset emails |

You do **not** need separate file storage to launch — uploads are processed and
auto-deleted within ~24h on the server's disk. (Add Cloudflare R2 later if you
want offsite storage; the env vars are already there.)

---

## The steps

**1. Point your domain at Cloudflare** (change nameservers — Cloudflare walks
you through it). Then create a **Tunnel** (Zero Trust → Networks → Tunnels),
copy the token, and add two public hostnames:
- `app.yourdomain.com` → `http://frontend:8080`
- `api.yourdomain.com` → `http://backend:8000`

**2. Create your Stripe products** (7 prices): Team & Business monthly + annual,
and the 3 credit packs. Copy each price ID. Get your **live secret key** and add
a **webhook** → `https://api.yourdomain.com/billing/webhook` (copy its signing
secret). All the variable names are in [`.env.example`](./.env.example).

**3. On the server** (fresh Ubuntu):
```bash
git clone <your-repo-url> 508-agent && cd 508-agent
cp deploy/.env.example .env
nano .env          # fill the values below, then save
bash deploy/bootstrap.sh
```
The values to fill in `.env` (the script generates secrets + starts everything):
- `EXPO_PUBLIC_API_URL=https://api.yourdomain.com`
- `PUBLIC_BASE_URL=https://yourdomain.com`
- `CORS_ALLOW_ORIGINS=https://yourdomain.com`
- `ADMIN_EMAILS=you@yourdomain.com`  *(so you can reach `/admin`)*
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, the 7 `STRIPE_PRICE_*`
- `SMTP_*` (from your email sender)
- `CF_TUNNEL_TOKEN` (from step 1) — then uncomment the `cloudflared`
  service block in `docker-compose.yml` and run `docker compose up -d cloudflared`

`bootstrap.sh` auto-generates `APP_SECRET` and `POSTGRES_PASSWORD`, builds the
images, runs DB migrations on boot, and waits until the backend is healthy.

**4. Go / no-go** (no money spent):
```bash
docker compose exec -T backend python -m app.devtools.verify_live_site \
  --api-url https://api.yourdomain.com
```
Expect `ALL CHECKS PASSED`. Then open `https://yourdomain.com`, sign up, and run
the in-app **System check** (Dashboard → System check) — it should be all green.

**5. Money test:** buy a Starter pack with Stripe **test** card
`4242 4242 4242 4242` (in Stripe test mode), confirm your credit balance
updates, then flip Stripe to live mode.

You're live.

---

## Optional, after launch
- **OCR for scanned PDFs:** on the server, `apt-get install -y tesseract-ocr`
  and set `OCR_ENABLED=true` (the admin dashboard will show "OCR: Active").
- **Offsite storage:** set the `S3_*` / `AWS_*` vars to a Cloudflare R2 bucket.
- **Updates:** `git pull && bash deploy/bootstrap.sh` re-deploys safely
  (your `.env` and database volume are preserved).

## If something's red
- `docker compose ps` — which container is unhealthy?
- `docker compose logs backend --tail=80` — backend errors
- `docker compose logs frontend --tail=40` — web build/serve errors
- Frontend can't reach the API → check `EXPO_PUBLIC_API_URL` (baked at build
  time — change it, then `docker compose up -d --build frontend`) and that
  `CORS_ALLOW_ORIGINS` includes your app domain.
