# 508 Agent — getting started

A desktop-grade accessibility auditor for Word, PowerPoint, and PDF files.
Runs entirely on your machine. Walks you through every accessibility issue
in plain English. Produces a printable conformance report and a remediated
copy of the file.

## What you need

* **Python 3.10 or newer** — verify with `python --version`. Install from
  https://www.python.org/downloads/ and check "Add Python to PATH".
* **Node.js 18 or newer** — verify with `node --version`. Install from
  https://nodejs.org/.

If you've never used a terminal: open `cmd.exe` from the Start menu, paste
the verification commands, and press Enter. You should see version numbers.

## First time — run setup

Double-click **`setup.bat`** in this folder. It runs `pip install` and
`npm install` for you. Takes 2–5 minutes the first time. Wait for the
**"Setup complete"** message.

## Every time — launch

Double-click **`start.bat`**. Two terminal windows pop up — leave them open.
After ~8 seconds your browser opens to **http://localhost:8081**.

To stop, close both terminal windows.

## What the app does

You land on a **Home** page with a big **"Start an audit →"** button. The
flow is one screen, four numbered steps:

1. **Pick a file.** PDF, .docx, or .pptx. (Don't have one to test with?
   Click one of the three sample documents on the same screen — easy /
   typical / hard.)
2. **Watch the analyzer work.** Usually a few seconds.
3. **Review issues one at a time.** Each finding gets a card explaining
   what's wrong, why it matters, where in the document it lives, what the
   auto-fix will do, and which WCAG / Section 508 / PDF/UA criterion it
   cites. Approve, edit, or reject each one.
4. **Apply approved fixes** and **download the remediated file** + a
   printable audit report.

## Keyboard shortcuts on the audit screen

| Key                   | What it does           |
| --------------------- | ---------------------- |
| `j`                   | Next issue             |
| `k`                   | Previous issue         |
| `a`                   | Approve current issue  |
| `r`                   | Reject current issue   |
| `e`                   | Edit the suggestion    |
| `u` or `Ctrl+Z`       | Undo last decision     |
| `?`                   | Show / hide help       |

There's also a "Bulk:" row above the issue queue with one-click options
(Approve all errors, Approve safe auto-fixes, Reject heuristic
suggestions, Reset all).

## Beyond the audit

* **`/tools/contrast`** — paste two hex colors, get a live WCAG AA/AAA
  pass-fail readout. Use it during visual review.
* **`/help`** — searchable glossary of every rule the analyzer can
  detect, plus a FAQ.
* **`/about`** — privacy and data-handling policy. Hand it to your
  security team if they ask.
* **`/settings`** — backend URL, theme (light / dark / system), audit-
  complete browser notifications, AI provider configuration, and a
  one-click **"Run diagnostic"** that probes every subsystem.

## Power features on the audit screen

* **Drag-and-drop** a file anywhere on the page to start an audit.
* **Auto-save** — refresh the page mid-audit and your decisions are
  restored from local storage.
* **Issue grouping** — when a doc has 9 missing-alt-text findings, the
  navigator collapses them under one header. Expand to see each one.
* **Bulk actions** — Approve all errors / Approve safe auto-fixes /
  Reject heuristic suggestions / Reset all (with a confirm dialog).
* **Completion celebration** — a green banner appears when you've
  reviewed every issue.
* **Export options** — printable HTML compliance report + JSON for
  programmatic use + CSV for spreadsheet review.

## Demo Mode

Demo Mode is **off by default**. When it's on, all numbers and findings are
fake — useful for exploring the UI but not for real audits. Toggle in
**Settings → Demo Mode**. The header always shows a yellow chip when
you're in Demo Mode so it's obvious.

## Optional: real AI alt-text

By default the analyzer uses local heuristics to suggest alt text and link
rewrites. For real vision-AI suggestions, set one env var before running
`start.bat`:

* `ANTHROPIC_API_KEY` — Claude with vision
* `OPENAI_API_KEY` — GPT-4 with vision

Settings → Run Diagnostic shows you which provider is active and gives you
a sample output to verify it's working.

## Trouble

**"Backend offline" in red.** The Python service crashed or didn't start.
Check the **508 Agent — backend** terminal window for the error. Most
common cause: missing dependencies. Re-run `setup.bat`.

**Cannot find module 'expo-router'.** Re-run `setup.bat`.

**Browser opens to a blank page.** Wait ~30s and refresh — Expo can take a
while on cold start.

**File picker won't accept my .docx.** You're on an old build. Pull the
latest code; the audit screen accepts PDF, DOCX, PPTX and the corresponding
MIME types.

**Score doesn't make sense.** Open the audit screen on any document — the
"How the score is computed" section spells out the math (errors count
double, warnings count once, info doesn't penalize), and each issue card
shows exactly what was applied.

## For developers

`docs/ARCHITECTURE.md` has the deeper architecture rundown (analyzer →
planner → dispatcher pipeline, parsers, AI provider interface).
`docs/DEVELOPMENT.md` covers the dev loop, smoke-test suite, and how to
add a new accessibility rule end-to-end.
