# 508 Agent

**A document-accessibility auditor and remediator** for PDF, Word, and PowerPoint files. The agent runs deterministic structural analysis against WCAG 2.1, Section 508, and PDF/UA, then walks the user through every finding with approve / edit / reject controls before baking only the approved fixes into a remediated copy. Ships as a hosted web app (Expo Router frontend, FastAPI backend) with billing, accounts, and a deterministic-plus-AI remediation pipeline.

---

## What this is

508 Agent is a self-serve document accessibility platform. Upload a PDF, DOCX, or PPTX; the backend extracts structure, runs eight analyzers, and proposes concrete fixes with provenance. The user reviews each issue one at a time, approves what they want, and downloads a remediated file plus a printable conformance certificate.

---

## Live demo / screenshots

Demo: https://508-agent.example.com (gated by Cloudflare Access in production)

Screenshots live under `docs/screenshots/`:

- `docs/screenshots/home.png` -- landing page with WebGL shader hero
- `docs/screenshots/audit.png` -- one-issue-at-a-time review queue
- `docs/screenshots/certificate.png` -- printable conformance certificate
- `docs/screenshots/billing.png` -- credits / pricing page
- `docs/screenshots/contrast.png` -- standalone WCAG contrast checker

---

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI + SQLAlchemy, uvicorn, Python 3.13 |
| Frontend | Expo Router (React Native Web), TypeScript, Vite-style web build |
| UI accents | Custom WebGL fragment shaders (smoke / pixel-art) |
| AI fallback | Anthropic Claude (vision) primary, OpenAI GPT-4o-mini secondary, deterministic heuristics by default |
| Billing | Stripe Checkout + Webhooks (one-time credit packs and Team/Business subscriptions) |
| Storage | Local filesystem in dev, S3-compatible in prod (configurable) |
| Database | SQLite in dev, Postgres in prod |
| Edge | Cloudflare Tunnel in production (Cloudflare Access optional, for private instances only) |
| Reverse proxy | nginx serves the static web bundle; `api.*` tunnels straight to uvicorn |

---

## Quick start (dev)

Four commands. Python 3.13 and Node 20+ required.

```bash
cp .env.example .env
cd backend && python dev_run.py
# in a second terminal:
cd frontend/frontend && npx expo start --web
```

Open the URL Expo prints (typically `http://localhost:8081`). The backend writes its live URL to `backend/.runtime/backend_url.txt` and the frontend reads it on first load, so you do not need to hard-code ports.

**Sanity check (one command):** from `backend/`, run `python run_smoke.py` (or `make smoke`). It boots an in-process FastAPI TestClient, walks the full sign-in -> grant-starter -> credits -> set-password -> sign-in-with-password -> profile -> healthz -> billing-config -> sign-out path across every `smoke_*.py` under `app/devtools/`, and prints a pass/fail table. Exit 0 means the ship path is healthy.

---

## Going to production

Production runs behind Cloudflare: a named tunnel routes `app.*` to nginx (which serves the static Expo web bundle) and `api.*` directly to uvicorn (FastAPI); uvicorn talks to Postgres over the compose network. Every API route requires an authenticated session JWT, and documents are owner-scoped; Stripe webhooks land on a public path protected by signature + timestamp verification. AI keys, Stripe keys, `APP_SECRET`, and the database URL are injected via environment variables -- there is no config baked into the build.

Full step-by-step deploy guide, env-var matrix, secret-rotation runbook, and a pre-flight checklist live in **[`deploy/SHIP-CHECKLIST.md`](./deploy/SHIP-CHECKLIST.md)**. Read it once top-to-bottom before your first deploy.

---

## Architecture overview

```
                +-------------+
                |   browser   |
                +------+------+
                       |
                       | https
                       v
                +------+------+
                | Cloudflare  |  (Access policy + WAF)
                +------+------+
                       |
                       | cloudflared tunnel
                       v
                +------+------+
                |    nginx    |  (TLS terminated upstream)
                +--+-------+--+
                   |       |
       static Expo |       | /api/* reverse proxy
       web bundle  |       |
                   v       v
            +------+--+ +--+----------+
            | (assets)| |   uvicorn   |  (FastAPI app)
            +---------+ +------+------+
                              |
                              | psycopg / asyncpg
                              v
                       +------+------+
                       |  postgres   |
                       +-------------+
```

---

## Project layout

```
508-agent/
  backend/             FastAPI app, parsers, analyzers, executors, writers, CLI
    app/               application package (api, models, services, ai, ...)
    requirements.txt   pinned Python deps
    dev_run.py         dev launcher (writes backend_url.txt)
  frontend/
    frontend/          Expo Router app (the actual project lives one level deep)
  deploy/              nginx.conf, cloudflare-tunnel.md, SHIP-CHECKLIST.md, .env.example
  docs/                ARCHITECTURE.md, DEVELOPMENT.md, OPERATIONS.md, PRODUCTION.md, POLICIES.md
  .env.example         root-level env template (copy to .env for dev)
  README.md            this file
```

---

## Pricing model

One-time credit packs (no expiry games — credits stay on the account until
used) plus optional Team/Business subscriptions with monthly allowances,
seats, and included certificates.

| Pack    | Price | Credits |
|---------|-------|---------|
| Starter | $5    | 50      |
| Pro     | $15   | 250     |
| Studio  | $50   | 1300    |

| Plan     | Price | Monthly credits | Seats |
|----------|-------|-----------------|-------|
| Team     | $99/mo ($990/yr) | 1,000 | 3 |
| Business | $499/mo ($4,990/yr) | 6,000 | 10 |

Each remediation deducts credits based on format:

| Format | Credits per remediation |
|--------|-------------------------|
| PDF    | 5                       |
| DOCX   | 3                       |
| PPTX   | 4                       |

Analysis is free; credits are only spent when the user clicks **Apply approved fixes** and a remediated file is produced.

---

## Contributing

Contributions are welcome. Please open an issue describing the change before sending a PR for anything larger than a typo. Run `python -B -m app.devtools.run_smoke_suite` from `backend/` and `npm exec tsc -- --noEmit` from `frontend/frontend/` before pushing -- both must be green. New analyzers, executors, or writers should ship with a smoke test under `backend/app/devtools/`. Keep commits small, describe the user-visible effect in the message, and avoid mixing unrelated changes.

---

## License

MIT. See [`LICENSE`](./LICENSE) for the full text.
