# SHIP-CHECKLIST.md

Single-document runbook for shipping 508-agent to a public domain behind
Cloudflare Tunnel + Access. Treat this as a checklist: top to bottom, no
skipping. Time budget: 60 to 90 minutes the first time.

## What you need before you start

- A registered domain name (any registrar, ~$10/year).
- A Cloudflare account (free tier).
- An Anthropic API key (https://console.anthropic.com/settings/keys).
- A laptop with `ssh`, `scp`, and `git` installed.
- A credit card for the VPS (Hetzner, DigitalOcean, Vultr; ~$5/month).

---

## Step 1. Cloudflare account + add the domain

1. Sign up at https://dash.cloudflare.com/sign-up.
2. Click `Add a site`, paste your domain, pick the Free plan.
3. At your domain registrar, change the nameservers to the two Cloudflare
   nameservers shown on the setup page.
4. Wait for the green `Active` badge on the Cloudflare dashboard. DNS
   propagation is usually under 5 minutes; can take up to 24 hours.

Docs: https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/

### What could go wrong

- **Registrar refuses the nameserver change.** Some registrars require you to
  unlock the domain first. Toggle `Domain Lock: Off`, save, then retry.
- **Cloudflare keeps showing `Pending Nameserver Update`.** Run
  `dig NS yourdomain.com +short` from your laptop. If it does not return the
  Cloudflare names, the registrar has not propagated yet. Wait an hour.
- **Existing DNS records were not auto-imported.** Open `DNS -> Records` in
  Cloudflare and re-add anything missing (MX for email is the common miss).

---

## Step 2. Create a Cloudflare Tunnel

1. Open `Zero Trust` from the left sidebar in the Cloudflare dashboard
   (https://one.dash.cloudflare.com/).
2. Go to `Networks -> Tunnels -> Create a tunnel`. Pick `Cloudflared`.
3. Name it `508-agent`, click `Save tunnel`.
4. On the install screen, copy the long token from the install command. It
   is the value after `--token`. Save it as `CF_TUNNEL_TOKEN`.
5. Add two public hostnames on the tunnel page:
   - `app.yourdomain.com` -> Service `HTTP` -> URL `frontend:8080`
   - `api.yourdomain.com` -> Service `HTTP` -> URL `backend:8000`
6. Click `Save`. The tunnel will show `Inactive` until Step 7 brings up
   `cloudflared` on the VPS.

Docs: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-remote-tunnel/

### What could go wrong

- **Lost the token.** Delete the tunnel and recreate. Tokens are not
  retrievable after the install screen closes.
- **Hostname conflict (`Record already exists`).** A leftover A or CNAME on
  the apex is shadowing the tunnel hostname. Delete it under
  `DNS -> Records`.
- **Wrong service URL.** Use the docker compose service name
  (`frontend`, `backend`), not `localhost`. `localhost` resolves inside the
  cloudflared container, not the compose network.

---

## Step 3. Cloudflare Access policy — SKIP for a public launch

> **Public self-serve SaaS (the default model): SKIP this entire step and leave
> `CLOUDFLARE_ACCESS_AUD` EMPTY.** Your customers sign in with the app's own
> accounts. Putting Cloudflare Access in front of the site would 401 every
> customer, the Stripe webhook, and the public certificate-verification links.
>
> Only follow the steps below if you are deploying a **private / internal-only**
> instance that should be gated to a fixed allow-list of emails.

1. In Zero Trust, go to `Access -> Applications -> Add an application ->
   Self-hosted`.
2. Name: `508-agent`. Application domain: `app.yourdomain.com`. Add a second
   domain entry for `api.yourdomain.com`.
3. Identity providers: enable `One-time PIN` (no extra setup needed).
4. Create one policy:
   - Name: `team`
   - Action: `Allow`
   - Include: `Emails` -> add `max.casteel.mso@gmail.com` and any other
     allowed addresses.
5. Save. Open the application's `Overview` tab. Copy the
   `Application Audience (AUD) Tag` -> that is `CLOUDFLARE_ACCESS_AUD`.
6. Your team domain is shown at the top of Zero Trust
   (e.g. `yourteam.cloudflareaccess.com`) -> that is
   `CLOUDFLARE_ACCESS_TEAM_DOMAIN`.

Docs: https://developers.cloudflare.com/cloudflare-one/applications/configure-apps/self-hosted-public-app/

### What could go wrong

- **AUD tag is empty in the UI.** Refresh the page; it appears a few seconds
  after the application is saved.
- **One-time PIN email never arrives.** Check spam. Add
  `noreply@notify.cloudflare.com` to your allowlist.
- **Sign-in loops after Allow.** The policy was scoped to a wrong identity
  provider. Edit policy -> `Selector: Emails`, not `Email domain`, and
  re-save.

---

## Step 4. DNS records

Cloudflared auto-creates a CNAME for each public hostname when the tunnel
connects. You should not add A or CNAME records by hand.

1. After Step 7 boots `cloudflared`, open `DNS -> Records` and confirm two
   `CNAME` rows exist:
   - `app` -> `<uuid>.cfargotunnel.com` (Proxied, orange cloud)
   - `api` -> `<uuid>.cfargotunnel.com` (Proxied, orange cloud)
2. Both must show the orange cloud. Grey cloud = bypassing Cloudflare =
   Tunnel and Access do not work.

### What could go wrong

- **Records not created.** Tunnel never connected. Check `docker compose
  logs cloudflared` on the VPS for `Registered tunnel connection`.
- **Records exist but the site 1033s.** The hostname in Tunnel `Public
  Hostnames` does not match the DNS name. Recheck spelling.
- **Grey cloud.** Click the cloud icon to flip it to orange (proxied).

---

## Step 5. Provision the VPS

Pick one (any will do; instructions assume Ubuntu 24.04 LTS):

- Hetzner CX22 (~$4.50/month, EU/US) - https://www.hetzner.com/cloud
- DigitalOcean Basic Droplet (~$6/month) -
  https://cloud.digitalocean.com/droplets/new
- Vultr Cloud Compute (~$5/month) - https://my.vultr.com/deploy/

1. Create the server with Ubuntu 24.04, 2 GB RAM minimum.
2. Add your SSH public key during creation (paste contents of
   `~/.ssh/id_ed25519.pub`; generate with `ssh-keygen -t ed25519` if you do
   not have one).
3. Note the public IPv4 address. You will not point DNS at it; it is just
   for SSH.
4. SSH in and install Docker:

```bash
ssh root@YOUR_VPS_IP
```

```bash
curl -fsSL https://get.docker.com | sh
```

```bash
ufw allow 22/tcp && ufw --force enable
```

### What could go wrong

- **`Permission denied (publickey)`.** Key was not registered with the host.
  Re-add via the provider dashboard's `Access -> SSH Keys` panel and
  rebuild, or use `ssh-copy-id root@IP` with the temporary password they
  emailed.
- **`docker: command not found` after install.** Log out and back in; the
  installer adds you to the `docker` group on next session.
- **VPS runs out of memory during build.** 1 GB is too small for the
  frontend webpack build. Resize to 2 GB.

---

## Step 6. Copy the repo and configure `.env`

From your laptop:

```bash
scp -r ./508-agent root@YOUR_VPS_IP:/opt/508-agent
```

On the VPS:

```bash
cd /opt/508-agent && cp deploy/.env.example .env
```

Generate the app secret:

```bash
openssl rand -hex 32
```

Edit `.env` and fill in (use `nano .env` or `vi .env`):

- `APP_SECRET` = the hex string from `openssl rand -hex 32`
- `DATABASE_URL` = `postgresql+psycopg://agent:agent@db:5432/agentdb`
- `CORS_ALLOW_ORIGINS` = `https://app.yourdomain.com`
- `CLOUDFLARE_ACCESS_AUD` = the AUD tag from Step 3
- `CLOUDFLARE_ACCESS_TEAM_DOMAIN` = `yourteam.cloudflareaccess.com`
- `CF_TUNNEL_TOKEN` = the tunnel token from Step 2
- `ANTHROPIC_API_KEY` = `sk-ant-...`

### What could go wrong

- **`scp: Permission denied`.** SSH agent does not have your key loaded. Run
  `ssh-add ~/.ssh/id_ed25519` on the laptop, retry.
- **Forgot to escape `$` in `APP_SECRET`.** docker compose treats `$` as
  variable interpolation. Either use `$$` to escape or stick to the hex
  output of `openssl rand -hex 32` (no shell metacharacters).
- **Edited `.env.example` instead of `.env`.** docker compose only reads
  `.env`. Re-copy and edit the right file.

---

## Step 7. Bring up the stack

```bash
cd /opt/508-agent && docker compose up -d
```

Watch the tunnel connect:

```bash
docker compose logs -f cloudflared
```

You should see `Registered tunnel connection` four times (one per
Cloudflare edge POP). Ctrl-C out when satisfied.

Verify all containers:

```bash
docker compose ps
```

All four (`frontend`, `backend`, `db`, `cloudflared`) should show `running`
or `healthy`.

### What could go wrong

- **`Error response from daemon: pull access denied`.** A private image
  reference slipped into compose. Run `docker compose build` first, then
  `up -d`.
- **`backend` container restart loop.** Almost always a missing env var.
  `docker compose logs backend | head -50` will show
  `APP_SECRET must be set` or similar. Edit `.env`, then `docker compose up
  -d backend`.
- **`cloudflared` says `error="Unauthorized"`.** Token is wrong or the
  tunnel was deleted. Repaste `CF_TUNNEL_TOKEN`, then
  `docker compose up -d cloudflared`.

---

## Step 8. Run database migrations

```bash
docker compose exec backend alembic upgrade head
```

You should see `Running upgrade ... -> <revision>, <message>` lines and
end at `head`. Idempotent; safe to re-run.

### What could go wrong

- **`could not connect to server`.** The `db` container is not ready yet.
  `docker compose logs db | tail -20`; wait for
  `database system is ready to accept connections`, retry.
- **`relation "alembic_version" already exists` then immediately stops.**
  Old SQLite migration metadata leaked in. From inside the backend
  container: `alembic stamp head`, then `alembic upgrade head`.
- **`No such command "alembic"`.** Wrong service name. The compose service
  is `backend`, not `api`. Confirm with `docker compose ps`.

---

## Step 9. Smoke test

From your laptop:

```bash
curl -i https://api.yourdomain.com/healthz
curl -i https://api.yourdomain.com/readyz
```

Expected (public launch, Access skipped per Step 3): **HTTP 200** with
`{"ok":true}` / `{"ready":true}`. (Only a private instance that enabled
Access in Step 3 should see a 302 to `cloudflareaccess.com` here.)

Or run the automated post-deploy check, which also exercises sign-in and a
real analyze:

```bash
cd backend
python -m app.devtools.verify_live_site --api-url https://api.yourdomain.com
```

Now use the app like a customer:

1. Open `https://app.yourdomain.com`.
2. Create an account with email + password (the app's own sign-in sheet).
3. Upload `samples/sample.docx` (or any small `.docx`) and run a full audit.
4. Confirm the audit completes, issues render, and `Download remediated
   file` works.

### What could go wrong

- **Customers / webhook / cert links get 401 or a Cloudflare login.**
  `CLOUDFLARE_ACCESS_AUD` is set. Clear it for a public launch (Step 3) and
  restart `backend`.
- **UI loads but API calls return 401/403 CORS errors.** `CORS_ALLOW_ORIGINS`
  does not match the actual frontend origin. Set it to the exact URL
  including scheme: `https://app.yourdomain.com`. Restart `backend`.
- **Audit hangs or returns `provider_error`.** `ANTHROPIC_API_KEY` is wrong
  or out of credit. Check
  https://console.anthropic.com/settings/billing.

---

## Step 10. Backups

Add a nightly Postgres dump to `cron`. On the VPS:

```bash
mkdir -p /opt/508-agent/backups
```

```bash
( crontab -l 2>/dev/null; echo "0 3 * * * cd /opt/508-agent && docker compose exec -T db pg_dump -U agent agentdb | gzip > backups/agentdb-\$(date +\%F).sql.gz && find backups -name '*.sql.gz' -mtime +14 -delete" ) | crontab -
```

That dumps every night at 03:00, gzips, and prunes anything older than
14 days.

To restore:

```bash
gunzip -c backups/agentdb-2026-05-08.sql.gz | docker compose exec -T db psql -U agent agentdb
```

### What could go wrong

- **Cron silently fails.** `grep CRON /var/log/syslog | tail`. The most
  common cause is `%` in the date format not being escaped with `\%` in
  crontab.
- **Backups fill the disk.** The retention `find ... -mtime +14 -delete`
  catches this; verify with `ls -lh backups/`.
- **Restore complains about existing tables.** Drop and recreate the db
  first: `docker compose exec db psql -U agent -c 'DROP DATABASE agentdb;
  CREATE DATABASE agentdb OWNER agent;'`, then pipe the dump back in.

---

## Appendix: Costs to expect

| Item                | Monthly cost     | Notes                                  |
| ------------------- | ---------------- | -------------------------------------- |
| Cloudflare (Free)   | $0               | Tunnel + Access for up to 50 users.    |
| VPS (Hetzner CX22)  | ~$4.50           | 2 GB RAM, 40 GB disk, EU or US.        |
| Domain registration | ~$1.00           | Averaged from a $10/year `.com`.       |
| Anthropic API       | usage-based      | ~$0.003 per audit on Sonnet, varies    |
|                     |                  | with document size and image count.    |
| Backups storage     | $0               | Local to the VPS disk.                 |
| **Floor total**     | **~$6/month**    | Excludes Anthropic usage.              |

A single developer running ~100 audits/day on small documents lands around
$15 to $30/month all-in.

---

## Quick reference

```bash
docker compose ps
```

```bash
docker compose logs -f backend
```

```bash
docker compose restart backend
```

```bash
docker compose pull && docker compose up -d
```
