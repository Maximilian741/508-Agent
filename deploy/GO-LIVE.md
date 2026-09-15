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
- `PUBLIC_BASE_URL=https://app.yourdomain.com`  *(the app's address from step 1 — Stripe
  receipts, password resets, invites and certificate links all point here)*
- `CORS_ALLOW_ORIGINS=https://app.yourdomain.com`  *(must match exactly, or every page
  loads but can't talk to the API)*
- `ADMIN_EMAILS=you@yourdomain.com`  *(the only address allowed into `/admin`. Listing it grants
  nothing by itself, and neither does signing up with it: step 4 unlocks it.)*
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
Expect `ALL CHECKS PASSED`. Then make yourself admin, on the server (no `-T`: it
prompts for a password twice):
```bash
docker compose exec backend python -m app.devtools.bootstrap_admin you@yourdomain.com
```
Use the exact address from `ADMIN_EMAILS`. It creates that account with the password you
type, or takes it over if it already exists: the password is replaced, the email is marked
verified, the account is made admin, and every other session on it is signed out. So if anyone
registered your address before you, they are locked out. It works with or without SMTP. Then open
`https://app.yourdomain.com`, sign in with that address and password, open
`https://app.yourdomain.com/admin`, and run the in-app **System check** (Dashboard → System
check). It should be all green.

That command is the **only** way to become admin, and it only accepts an address listed in
`ADMIN_EMAILS`. Nothing reachable from the web grants admin: not signing up with a listed
address, not changing an account's email to one, not clicking a verify link. Changing your own
email later removes admin; run the command again for a listed address to get it back. To revoke
admin, take the address out of `ADMIN_EMAILS` and restart the backend.

People will also type the bare `yourdomain.com`. In Cloudflare → Rules →
Redirect Rules, add one: *hostname equals `yourdomain.com`* → dynamic redirect to
`concat("https://app.yourdomain.com", http.request.uri.path)`, status 301.

**5. Money test:** work through **[`PAYMENTS-CHECKLIST.md`](./PAYMENTS-CHECKLIST.md)**
top to bottom — it is the literal click-by-click bring-up: test-mode products,
the webhook (the step everyone gets wrong), the `4242 4242 4242 4242` purchase,
replay-safety check, subscription + cancel, portal, then the flip to live and
one real refunded purchase. It also lists what is already machine-verified so
you don't re-test it, and a symptom→fix table for when something is red.

You're live.

---

## Optional, after launch
- **Get into Google (do this launch week):** every page already ships its own
  title, description, structured data, robots.txt and sitemap. What only you
  can do: go to https://search.google.com/search-console, add your domain as
  a property (verify via the DNS record Cloudflare makes this one click),
  then Sitemaps → submit `https://app.yourdomain.com/sitemap.xml`. Indexing
  starts in days; ranking for competitive queries ("pdf accessibility
  checker", "508 converter") builds over weeks and is helped most by other
  sites linking to you — the free scanner pages are the thing people link to.
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
