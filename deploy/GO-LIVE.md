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
- `SMTP_*` (from your email sender) — **required, not optional.** Verification
  email is the only thing standing between the 25 free starter credits and an
  unbounded farm of invented addresses, so outside `APP_ENV=development` the
  grant refuses anyone with an unverified address. Leave `SMTP_HOST` empty and
  nobody can verify, so nobody gets their starter credits (and monitoring
  alerts go nowhere). The backend logs an error at boot if it's missing.
- `CF_TUNNEL_TOKEN` (from step 1) — then uncomment the `cloudflared`
  service block in `docker-compose.yml` and run `docker compose up -d cloudflared`
- `TRUST_PROXY_HEADERS=true` — **only** because this deployment reaches the API
  exclusively through the Cloudflare Tunnel. The per-IP brute-force limit on
  `/auth` keys on `CF-Connecting-IP` (which Cloudflare overwrites) or the last
  `X-Forwarded-For` hop (which your own proxy appends). If you ever publish
  port 8000, or put anything in front of the API that does **not** rewrite
  those headers, set this to `false` — otherwise an attacker sends the header
  themselves, gets a fresh rate-limit bucket per request, and password guessing
  against `/auth/sign-in` becomes unlimited. The default is `false`.

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
  This also decides what an uploaded **image** (.png .jpg .gif .bmp .tiff
  .webp) is worth: images are turned into a PDF page and take the scanned
  path. With OCR on, fixing one adds a real text layer (charged as a PDF).
  With OCR off, the customer is told plainly that the picture's words stay
  unreadable, and a run that could only add a title is not charged — they
  get their original image back. A large image can take a few hundred MB of
  RAM while it is converted, so at most 2 convert at once
  (`IMAGE_CONVERT_CONCURRENCY` in `.env`; lower it to 1 on a 1 GB server).
- **Excel workbooks (.xlsx):** checking or fixing one costs at most ~100-200
  MB above the idle backend, whatever its size (a 10 MB export of four dense
  sheets peaks at ~220 MB for the whole process), and at most 2 are worked
  on at once (`XLSX_CONCURRENCY` in `.env`; a third waits up to a minute,
  then is told the server is busy and not charged). `XLSX_CELL_BUDGET`
  (default 1,000,000) caps how many cells one workbook's scan holds; a sheet
  past its share is still followed to the end of its data, and anything left
  unread is disclosed on the report, never passed off as checked.
- **Legacy Office / OpenDocument uploads (.doc .xls .ppt .rtf .odt .ods
  .odp):** these are converted to .docx/.xlsx/.pptx with LibreOffice, which
  the image does NOT include by default because it costs ~450-600 MB of
  image size and ~200 MB of RAM per conversion. To turn it on, rebuild the
  backend with `docker compose build --build-arg INSTALL_LIBREOFFICE=true
  backend && docker compose up -d backend` (or put
  `args: { INSTALL_LIBREOFFICE: "true" }` under the backend's `build:` in
  docker-compose.yml). Optional tuning in `.env`:
  `OFFICE_CONVERT_TIMEOUT_SECONDS` (default 90), `OFFICE_CONVERT_CONCURRENCY`
  (default 2 simultaneous conversions), `SOFFICE_PATH` (only if soffice is
  not on PATH). Each conversion runs in its own throwaway directory and
  LibreOffice profile with macros disabled and none of the app's secrets in
  its environment; if you want belt-and-braces, also run the backend
  container without outbound network access to your internal services.
  Without LibreOffice those uploads get a clear "open it in Word/Excel/
  PowerPoint, Save As .docx/.xlsx/.pptx, and upload that" message instead —
  nothing breaks. Converted files are fixed, delivered and priced as the
  modern format (.doc -> .docx at 3 credits, .xls -> .xlsx at 3, .ppt ->
  .pptx at 4); if no fix persists, the customer gets their original file back
  (the conversion alone is not given away free).
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
