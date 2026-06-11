# 508 Agent — Owner's Handoff Guide

> The one document to read to understand what this product is, how it works,
> how to run it, and how to operate it as a business. Written for the owner
> (you), not for developers. Last updated: 2026-06-10.

---

## 1. What this is (the 60-second version)

**508 Agent is a self-serve SaaS that makes documents accessible.** A customer
uploads a PDF, Word (DOCX), or PowerPoint (PPTX) file. The engine analyzes it
against WCAG 2.1 / Section 508 / PDF-UA rules (20 automated analyzers),
shows every issue with a plain-English explanation, and — this is the moat —
**writes the approved fixes back into the file itself**. The customer
downloads a remediated copy, can re-audit it for free to see the improvement,
and can issue a verifiable remediation certificate with a public
verification link.

What actually gets FIXED in the file (not just flagged):

| Format | Fixes baked into the download |
|---|---|
| DOCX | title, language, heading levels, image alt text, table header rows, link text (incl. links in tables/text boxes/footnotes), typed "- item" lists → real Word lists |
| PPTX | title, language, alt text + decorative marking, table header rows, link text, typed lists → real bullets |
| PDF | title, language, image alt; **untagged PDFs get a full reconstructed structure tree** (headings, lists, tables, figures, links, form fields, header/footer artifacts); **scanned PDFs get an invisible OCR text layer** (when OCR is enabled on the host) |

What gets DETECTED and queued for human review (honestly, never silently
claimed): colour contrast, fake bold-text headings, unlabeled form fields,
untitled slides, slide reading order, heading jumps in tagged PDFs,
header-less tagged tables, malformed tagged lists.

**The honesty invariant** (your strongest sales point): the conformance score
only counts fixes that *genuinely persist into the downloaded file*. The
certificate is server-generated from server-verified analysis. Nothing is
overclaimed. The whole engine is regression-locked by a 55-test smoke suite
that includes byte-level verification of every fix.

---

## 2. The business model

- **Free**: analysis (unlimited uploads, every finding visible). This is the
  funnel.
- **Credits** (one-time packs): remediation costs credits per document —
  PDF 5, DOCX 3, PPTX 4; certificate 2. Packs: 50 / 250 / 1300 credits.
  New accounts get 25 free credits (after email verification when SMTP is
  configured).
- **Subscriptions**: Team $99/mo (3 seats) and Business $499/mo (10 seats),
  annual variants, certificates included, shared credit wallet across the
  team, optional usage-based overage auto-top-up.
- **Certificates**: every issued certificate has a public verification URL
  (`/verify?cert=...`) an auditor can check — a viral/trust loop.

Money flows through Stripe (Checkout + webhooks, replay-protected,
idempotent). The admin dashboard (`/admin`, admin emails only) shows MRR,
subs by plan, credits sold/spent, certificates, teams, and deployment state.

---

## 3. Architecture in one screen

```
frontend/frontend/        Expo Router (React Native Web) app
  app/                    screens: index, audit, batch, billing, teams,
                          admin, verify, help, terms, privacy, security,
                          reset-password, manual-review, landing
  src/api/client.ts       typed API client
  src/domain/             account/billing/issueCatalog/achievements logic

backend/                  FastAPI (Python 3.13)
  app/api/                routes: auth, pipeline, documents, billing
                          (stripe_billing), credits, teams, admin_metrics,
                          audit_log
  app/parsers/            pdf_parser, docx_parser, pptx_parser
                          → AccessibilityTree (shared node model)
  app/analyzers/          20 analyzers (registry.py)
  app/services/
    remediation_planner   flags → planned actions (policy-gated)
    remediators/          executors per action (alt text, titles, lists,
                          links, OCR gate, ...)
    ocr.py                OCR provider abstraction (Tesseract adapter)
  app/writers/            docx_writer, pptx_writer, pdf_writer — apply the
                          mutated tree back onto a COPY of the file
  app/pdf/
    ua_tagger.py          untagged PDF → full PDF/UA structure tree
    tag_reader.py         tagged PDF → analysis of the EXISTING tree
  app/db/ + alembic/      SQLAlchemy models + migrations 0001–0011
  app/devtools/           the 55-smoke suite + verify_live_site CLI
```

Pipeline per document: `parse → analyze (flags) → plan (policy) → user
approves in UI → execute (tree mutations) → write (file mutations) → score
(only persisted actions counted) → signed download URL (24h TTL)`.

Tenancy and security: scrypt password hashing, 7-day HS256 JWT, per-user
document ownership (404 on cross-tenant), HMAC-signed download URLs,
rate limiting, upload validation (zip-bomb/OOXML sniffing), global error
handler (no stack traces to clients), GDPR self-service (export + delete
account on the Account page), audit log of every sensitive action.

---

## 4. Run it locally (10 minutes)

Prereqs: Python 3.13, Node 20+, git.

```bash
# Backend
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m uvicorn app.main:app --port 8000
# → http://127.0.0.1:8000/healthz should return ok

# Frontend (second terminal)
cd frontend/frontend
npm install
npx expo start --web
# → opens the app; Settings shows the backend URL (defaults to :8000)
```

Dev mode notes: SQLite database file is created automatically; no APP_SECRET
needed (a random one is generated, sessions reset on restart); SMTP absent →
email verification is skipped and starter credits grant immediately; Stripe
absent → the billing page works in test/mock paths only.

**Verify any running instance** (local or production) without spending money:

```bash
cd backend
python -m app.devtools.verify_live_site --api-url http://127.0.0.1:8000
# 6 checks: healthz, readyz, sign-in, analyze, expected findings, fake-list
```

**Full regression** (run before/after any change):

```bash
cd backend && python run_smoke.py          # expect 55/55
cd backend && python -m pytest tests/ -q   # expect 27 passed
cd frontend/frontend && node_modules/.bin/tsc --noEmit
cd frontend/frontend && npx expo export -p web   # production bundle
```

---

## 5. Deploy to production (the short path)

Everything detailed lives in **deploy/SHIP-CHECKLIST.md** (step-by-step) and
**docs/LAUNCH.md** (env matrix + failure modes). The shape:

1. **Host**: any Docker host (the repo has `docker-compose.yml`: postgres +
   backend + frontend static server on 8080).
2. **Secrets**: copy `deploy/.env.example` → `.env`; set `APP_SECRET`
   (random 64 hex), `POSTGRES_PASSWORD` (random), `APP_ENV=production`,
   `EXPO_PUBLIC_API_URL` (your API URL — required at build time).
3. **Stripe**: create the products/prices listed in the env example
   (4 subscription prices + 3 credit packs), set `STRIPE_SECRET_KEY`,
   `STRIPE_WEBHOOK_SECRET`, price IDs. Point the webhook at
   `/billing/webhook`.
4. **Edge**: Cloudflare Tunnel (config in `deploy/`) → TLS, WAF, no open
   ports. Leave `CLOUDFLARE_ACCESS_AUD` EMPTY for a public launch.
5. **SMTP** (recommended): set `SMTP_*` so email verification gates the free
   credits (abuse control) and password reset emails send.
6. **OCR** (optional, recommended): in the backend image/host run
   `apt-get install -y tesseract-ocr`, set `OCR_ENABLED=true`. The admin
   dashboard will show "OCR: Active". Without it, scanned PDFs are honestly
   queued for manual work instead.
7. **Migrations** run automatically on boot (`alembic upgrade head`).
8. **Go/no-go**: `python -m app.devtools.verify_live_site --api-url
   https://your-domain` → ALL CHECKS PASSED, then one manual Stripe
   purchase with test card `4242 4242 4242 4242`.

Admin access: set `ADMIN_EMAILS=you@yourdomain` in the backend env; sign in
with that email; visit `/admin`.

---

## 6. Operating it

- **Support**: support@508-agent.app (referenced throughout the UI), privacy@
  for GDPR, security@ for disclosures (SECURITY.md promises 72h ack).
  Point these at real mailboxes before launch.
- **Refunds**: policy text is on the billing page (unused credit packs within
  14 days; subscriptions cancel at period end).
- **Retention**: uploaded/remediated artifacts auto-delete after ~24h
  (TTL + storage lifecycle). Documents are never used for training.
- **Monitoring**: `/healthz` (liveness), `/readyz` (DB), admin dashboard
  (business + deployment state), audit log (`/admin`, bottom section).
- **Costs that scale**: storage is transient (24h), compute is per-analysis
  (CPU-bound, threadpooled). The only metered third party is Stripe; AI
  alt-text suggestions use the configured AI key (optional, per-job cost cap
  via `MAX_AI_COST_PER_JOB_USD`).

## 7. What's verified vs. what's known-limited

**Verified** (all regression-locked): the 55-smoke suite covers every fix
path byte-level (alt text round-trips, lists appear in numbering.xml, links
rewritten in footnotes, OCR layer extractable, tagged trees parsed, scores
honest); plus realistic-corpus invariants (issues strictly decrease, clean
docs stay clean, nothing new appears post-fix); plus malformed-input
fuzzing (garbage uploads always 4xx, never 500); plus three live-HTTP
journey suites (sign-in → analyze → remediate → signed download → re-upload
shows issues resolved).

**Known limitations** (all disclosed in-product, none silently claimed):
- Colour contrast assumes white background for PDFs (coloured-background
  pages are skipped rather than guessed) and skips theme-inherited colours
  it can't resolve.
- PDF heading levels on untagged PDFs come from font-size heuristics.
- Reading-order detection covers the PowerPoint stacked-shapes case only.
- OCR output should be proofread (recognition errors on poor scans).
- DOCX headers/footers are treated as page furniture (not analyzed), by
  design — mirrors how PDF artifacts are excluded.

## 8. Where to change common things

| Want to change | File |
|---|---|
| Prices / credit packs | `backend/app/api/stripe_billing.py` (`_PLAN_PRICE_USD`, pack definitions) + Stripe dashboard + `app/billing.tsx` copy |
| Credit costs per format | `backend/app/api/pipeline.py` (`_charge_credits`) + `app/help.tsx` FAQ |
| Issue explanations (customer-facing) | `frontend/frontend/src/domain/issueCatalog.ts` |
| Landing page copy | `frontend/frontend/app/landing.tsx` |
| Add an admin | `ADMIN_EMAILS` env var |
| Free starter credits | `backend/app/api/auth.py` (grant_starter) |
| Smoke suite | `backend/app/devtools/smoke_*.py`, run via `run_smoke.py` |
