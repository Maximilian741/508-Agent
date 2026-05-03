# 508 Agent

> **A document-accessibility auditor and remediator** for PDF, Word, and PowerPoint files.
> Open-source, local-first, opinionated about not lying to remediators.

The agent runs deterministic structural analysis against WCAG 2.1, Section 508, and PDF/UA, walks the user through every finding in plain English, lets them approve / edit / reject each fix individually, then bakes only the approved changes into a remediated copy of the document. It ships with a CLI, a web UI, a printable conformance certificate, and an in-app contrast checker.

---

## Why this exists

Most accessibility tools are *checkers* — they flag problems and stop there. Most remediation tools are opaque — they apply a black-box pass and hand back a "fixed" document. 508 Agent splits the difference: every finding gets a plain-English explanation and a concrete proposal, the user approves what they want, and the tool only mutates the document where given permission. The full audit trail is exportable as HTML, JSON, or CSV for compliance records.

---

## What's in the box

**Audit workflow** — Drop a PDF, DOCX, or PPTX. Get a one-screen, one-issue-at-a-time review queue with severity heatmap, navigable side panel, keyboard shortcuts (`j`/`k`/`a`/`r`/`e`/`u`), bulk actions, and a live score that moves as you decide.

**Remediation engine** — Eleven deterministic executors covering missing alt text, decorative-image cleanup, heading-level normalization, table header injection + scope inference, list structure repair, link rewriting, document title/language, and reading-order resolution. Each executor records provenance and confidence so heuristic suggestions can be flagged for review.

**Pluggable AI layer** — Heuristic fallback by default (no network). Set `ANTHROPIC_API_KEY` for Claude vision-based alt text, or `OPENAI_API_KEY` for GPT-4o-mini. Multimodal — image bytes are extracted by the parser and sent to vision models for high-quality alt text.

**Tree-aware writers** — DOCX, PPTX, and PDF writers that take an `AccessibilityTree` (after the executors mutate it) and bake the changes back into the source file. Each writer is defensive: it never raises, copies source → output before mutating, and reports a structured `{ applied: [...], skipped: [...] }` log so the UI can flag anything that didn't take.

**Standalone tools** — A WCAG color-contrast checker (`/tools/contrast`), a searchable in-app glossary of every rule the analyzer detects (`/help`), a privacy / data-handling statement (`/about`), and a Settings panel with one-click backend diagnostics, theme picker, audit-complete browser notifications, and Demo Mode toggle.

**Compliance deliverables** — Printable HTML conformance certificate (with cover, scorecard, signature block, and per-decision tables), JSON export of the full audit, CSV export of the issues table.

**Headless CLI** — `python -m app.cli audit path/to/file.docx [--json out.json] [--apply --output remediated.docx]`. Suitable for CI gating and batch processing.

---

## Quickstart

### Windows one-click

```cmd
setup.bat        :: install Python + Node deps (run once)
start.bat        :: launch backend + frontend in two terminals
```

### Cross-platform manual

```bash
# Backend
cd backend
python -m pip install -r requirements.txt -r requirements-dev.txt
python dev_run.py

# Frontend (separate terminal)
cd frontend/frontend
npm install
npm run web
```

Open the URL Expo prints (typically `http://localhost:8081`). The backend writes its own URL to `backend/.runtime/backend_url.txt`, which the frontend reads on first load.

For non-developer setup instructions, see [`GETTING-STARTED.md`](./GETTING-STARTED.md).

---

## Architecture

```
┌────────────────┐    upload     ┌───────────────────────┐
│  Expo web UI   │──────────────▶│  FastAPI / uvicorn    │
│  (audit.tsx)   │◀──────────────│  app.main             │
└────────────────┘   JSON+file   └──────────┬────────────┘
                                            │
                                            ▼
                       ┌────────────────────────────────────────┐
                       │            Pipeline                    │
                       │ ┌──────┐ ┌──────────┐ ┌──────────────┐ │
                       │ │ Parsers│▶│Analyzers│▶│   Planner   │ │
                       │ │ pdf   │ │ heading  │ │  policy +   │ │
                       │ │ docx  │ │ image    │ │  filter     │ │
                       │ │ pptx  │ │ table    │ └─────┬────────┘ │
                       │ └────┬──┘ │ link     │       │          │
                       │      │    │ list     │       ▼          │
                       │      │    │ doc-meta │ ┌──────────────┐ │
                       │      │    │ ordering │ │  Executors   │ │
                       │      │    └──────────┘ │ (11 actions) │ │
                       │      │                  └─────┬────────┘ │
                       │      │                        ▼          │
                       │      │                 ┌──────────────┐ │
                       │      └────────────────▶│   Writers    │ │
                       │                        │ pdf|docx|pptx│ │
                       │                        └──────────────┘ │
                       └────────────────────────────────────────┘
                                            │
                                            ▼
                                ┌──────────────────────┐
                                │  AI provider layer   │
                                │ Heuristic | Claude   │
                                │           | OpenAI   │
                                └──────────────────────┘
```

**Key types** are in `backend/app/models/accessibility.py`: `AccessibilityTree`, `AccessibilityFlag`, `RemediationAction`, `RemediationPlan`, `RemediationReport`. The frontend mirrors these in `frontend/frontend/src/api/client.ts`.

---

## Public APIs

### Pipeline (the new tree-aware path)

- `POST /pipeline/analyze` — multipart upload, returns a `PipelineResponse` (summary + violations + score). `?execute=false` by default; analysis is read-only.
- `POST /pipeline/remediate` — multipart upload + `approved_violations` JSON list. Runs analyzers, executes ONLY plans whose violation id is approved, writes via the format-specific writer, returns a download URL.
- `GET /pipeline/files/{job_id}/{filename}` — streams a previously-produced remediated file.

### Document workflow (the legacy persistent path)

`/documents/upload`, `/documents/{id}/scan`, `/documents/{id}/apply-fixes`, `/documents/{id}/finalize`, `/documents/{id}/fix-report`, plus per-format download endpoints. See `backend/app/api/documents.py`.

### Diagnostics & misc

- `GET /healthz` — liveness ping
- `GET /diagnostics` — deep probe (analyzers, executors, parsers, AI provider) with per-step timing
- `GET /api/policies`, `GET /api/policies/{id}` — policy packs

---

## Repository layout

| Path | Purpose |
|---|---|
| `backend/app/main.py` | FastAPI entry point |
| `backend/app/api/` | Route handlers (pipeline, documents, manual_review, policies, health, …) |
| `backend/app/parsers/` | Format-specific parsers that emit `AccessibilityTree` |
| `backend/app/analyzers/` | Detection rules (one file per rule family) |
| `backend/app/services/remediators/` | Tree-mutating executors |
| `backend/app/writers/` | Tree-to-file writers (DOCX, PPTX, PDF) |
| `backend/app/ai/semantic_inference.py` | Pluggable AI provider with heuristic fallback |
| `backend/app/cli/` | Headless command-line interface |
| `backend/app/devtools/` | Smoke tests and contract checks |
| `frontend/frontend/app/` | Expo Router screens (audit, home, help, about, contrast, settings, manual-review, …) |
| `frontend/frontend/src/ui/components/` | Themed component library (Button, Card, Chip, Dialog, Hero, Icon, ScoreBadge, IssueNavigator, PdfPreview, Skeleton, Tooltip, TrendChart, …) |
| `frontend/frontend/src/domain/` | Pure logic — issue catalog, contrast math, audit history & draft, sample documents, notifications |
| `docs/` | Architecture, development, policies, operations |

---

## Tests & smoke

From `backend/`:

```bash
python -B -m app.devtools.run_smoke_suite
python -B -m app.devtools.smoke_pipeline_remediate
python -B -m app.devtools.smoke_api_contract
python -B -m unittest discover tests -v
```

From `frontend/frontend/`:

```bash
npm exec tsc -- --noEmit
```

---

## Configuration

| Env var | Effect |
|---|---|
| `ANTHROPIC_API_KEY` | Enables Claude vision-based AI suggestions |
| `OPENAI_API_KEY` | Enables OpenAI vision-based AI suggestions |
| `SEMANTIC_PROVIDER` | Force `heuristic` / `claude` / `openai` |
| `STORAGE_PROVIDER` | `local` (default) or `s3` |
| `S3_BUCKET`, `AWS_REGION`, … | Required when `STORAGE_PROVIDER=s3` |
| `DATABASE_URL` | Defaults to SQLite at `backend/.runtime/508_agent.db` |
| `MAX_UPLOAD_MB` | Upload ceiling (default 25) |
| `CORS_ALLOW_ORIGINS` | Comma-separated origins |

---

## Privacy

- Files are processed locally. They do not leave the machine running the backend unless an AI provider is explicitly configured, in which case only the parts of the document needed for the request (a snippet of context, an image's bytes for alt-text generation) are sent.
- No telemetry. The frontend doesn't phone home; the backend doesn't either.
- Audit history is stored in `localStorage` on the user's browser. The legacy persistent workflow stores artifacts in `backend/.runtime/`. Both can be cleared from Settings.

See [`/about`](./frontend/frontend/app/about.tsx) for the full statement.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Backend offline (red chip on home) | Look at the `508 Agent — backend` terminal for the error. Most often a missing dependency — re-run `setup.bat` or `pip install -r requirements.txt`. |
| Browser blank after `start.bat` | Wait ~30s and refresh; Expo is slow on cold start. |
| `ModuleNotFoundError: No module named 'docx'` | `pip install -r backend/requirements.txt`. |
| Drag-and-drop doesn't accept .docx | Make sure you're on the latest build; the file picker accepts the proper MIME types. |
| Score doesn't move when I approve | The audit screen shows a *live* score derived from your decisions. If it isn't moving, there are no errors/warnings to remove (info issues are weight 0). |

---

## License

MIT (or whatever the project's chosen license — see `LICENSE`).

## Credits

Built as a working example of a deterministic + AI-assisted document accessibility tool. Heavily inspired by the work of the W3C WAI tutorials, Section 508 ICT Refresh, and the PDF Association's PDF/UA group.
