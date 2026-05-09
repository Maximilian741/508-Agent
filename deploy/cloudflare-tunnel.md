# Cloudflare Tunnel + Access deploy runbook

Single-VPS deployment with a Cloudflare Tunnel terminating TLS, Cloudflare
Access enforcing identity, and Cloudflare R2 as the object store. No public
ports on the host.

## One-time Cloudflare setup

1. **Create the tunnel.** Zero Trust dashboard -> *Networks -> Tunnels ->
   Create a tunnel*. Pick *Cloudflared*. Name it `508-agent`. After creation,
   copy the `--token <...>` value from the install snippet — that is
   `CF_TUNNEL_TOKEN`.
2. **Add public hostnames.** On the same tunnel, add two public hostnames:
   - `app.example.com` -> service `http://frontend:80`
   - `api.example.com` -> service `http://backend:8000`

   These names resolve internally on the compose network — do NOT bind ports
   on the host.
3. **Create the Access application.** Zero Trust -> *Access -> Applications ->
   Add an application -> Self-hosted*. Cover both hostnames (or set up two
   apps with the same policy). Add an Allow policy for the email addresses
   that should reach the app.
4. **Grab the AUD tag.** Open the Access application's *Overview* tab. Copy
   the *Application Audience (AUD) Tag* — that is `CLOUDFLARE_ACCESS_AUD`.
   The team domain (e.g. `yourteam.cloudflareaccess.com`) is
   `CLOUDFLARE_ACCESS_TEAM_DOMAIN`.

## Cloudflare R2 setup

1. *R2 -> Create bucket* `508-agent` (or whatever you set in `S3_BUCKET`).
2. *R2 -> Manage R2 API Tokens -> Create API token* with **Object Read &
   Write** scoped to that bucket. Copy the access key id and secret into
   `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.
3. The endpoint URL is shown on the bucket page. Set
   `S3_ENDPOINT_URL=https://<accountid>.r2.cloudflarestorage.com` and
   `AWS_REGION=auto`.

## VPS env file

```bash
cp deploy/.env.example .env
# edit .env — fill APP_SECRET, CF_TUNNEL_TOKEN, CLOUDFLARE_ACCESS_AUD,
# CLOUDFLARE_ACCESS_TEAM_DOMAIN, AWS_*, S3_BUCKET, S3_ENDPOINT_URL,
# CORS_ALLOW_ORIGINS=https://app.example.com
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # APP_SECRET
```

## Day-1 deploy

```bash
docker compose pull
docker compose up -d --build
docker compose logs -f cloudflared
```

You should see `Registered tunnel connection` lines from `cloudflared` and
`[auth] CF Access enforcement: ENABLED (aud=...)` from the backend.
Browse to `https://app.example.com`, authenticate via Access, and confirm a
test upload round-trips through the API.

## Day-2 ops cheat sheet

- **Rotate APP_SECRET** -> existing signed URLs become invalid; that is the
  intended blast radius. Restart only `backend`.
- **Rotate R2 keys** -> update `.env`, `docker compose up -d backend`.
- **Inspect runtime artifacts** -> `docker compose exec backend ls
  /app/.runtime/materialized/pipeline`. Auto-swept hourly per
  `PIPELINE_ARTIFACT_TTL_SECONDS`.
- **CF Access denied loops** -> verify `CLOUDFLARE_ACCESS_AUD` matches the
  Access app exactly; the backend logs the rejected token reason.
