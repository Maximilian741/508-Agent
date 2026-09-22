#!/usr/bin/env bash
# ============================================================================
# 508 Agent — one-command server bootstrap (fresh Ubuntu/Debian VPS)
# ============================================================================
# Idempotent. Run from the repo root on your server:
#
#     bash deploy/bootstrap.sh
#
# What it does:
#   1. Installs Docker + the compose plugin if they're missing.
#   2. Creates .env from deploy/.env.example on first run.
#   3. Generates strong APP_SECRET + POSTGRES_PASSWORD (only if still blank —
#      never overwrites values you've set).
#   4. Refuses to start until the values only YOU can provide are filled in
#      (your public URLs + Stripe live keys), telling you exactly which.
#   5. Builds the images and brings the stack up.
#   6. Waits for the backend to report ready, then prints status + next steps.
#
# It will NOT touch Stripe, DNS, or your Cloudflare account — those are yours.
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_FILE="$ROOT/.env"
EXAMPLE="$ROOT/deploy/.env.example"

say()  { printf "\n\033[1;36m==>\033[0m %s\n" "$*"; }
ok()   { printf "  \033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[1;33m!\033[0m %s\n" "$*"; }
die()  { printf "\n\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# --- 1. Docker ---------------------------------------------------------------
say "Checking Docker"
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker not found — installing (needs sudo)…"
  curl -fsSL https://get.docker.com | sh
  ok "Docker installed"
else
  ok "Docker present ($(docker --version | awk '{print $3}' | tr -d ,))"
fi
if ! docker compose version >/dev/null 2>&1; then
  die "The 'docker compose' plugin is missing. Install Docker Engine v20.10+ with the compose plugin, then re-run."
fi
ok "docker compose available"

# --- 2. .env -----------------------------------------------------------------
say "Preparing .env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$EXAMPLE" "$ENV_FILE"
  ok "Created .env from deploy/.env.example"
else
  ok ".env already exists — leaving your values untouched"
fi

# set_if_blank KEY VALUE  — only fills a key whose value is currently empty
set_if_blank() {
  local key="$1" val="$2" cur
  cur="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  if [ -z "${cur}" ]; then
    # portable in-place edit (GNU + BSD sed)
    if sed --version >/dev/null 2>&1; then
      sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
      sed -i '' "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    fi
    ok "Generated ${key}"
  else
    ok "${key} already set"
  fi
}

# --- 3. Generated secrets ----------------------------------------------------
say "Generating secrets (only if blank)"
set_if_blank "APP_SECRET" "$(openssl rand -hex 32)"
set_if_blank "POSTGRES_PASSWORD" "$(openssl rand -hex 24)"

# --- 4. Validate the values only YOU can provide -----------------------------
say "Checking the values you must provide"
missing=()
check_real() {
  local key="$1" cur
  cur="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  case "$cur" in
    ""|"https://api.example.com"|"https://app.example.com") missing+=("$key") ;;
  esac
}
check_real "EXPO_PUBLIC_API_URL"   # public backend URL the web app calls
check_real "PUBLIC_BASE_URL"       # public app URL (used in emails / cert links)
if [ "${#missing[@]}" -gt 0 ]; then
  warn "These still hold placeholder/empty values in .env:"
  for m in "${missing[@]}"; do printf "      - %s\n" "$m"; done
  cat <<'EOF'

  Edit .env and set them to your real domains, e.g.:
      EXPO_PUBLIC_API_URL=https://api.yourdomain.com
      PUBLIC_BASE_URL=https://app.yourdomain.com
      CORS_ALLOW_ORIGINS=https://app.yourdomain.com

  Stripe live keys (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PRICE_*)
  are needed for payments — fill them now or add them later and re-run.
  Set ADMIN_EMAILS to your email so you can reach the /admin dashboard.

  Then re-run:  bash deploy/bootstrap.sh
EOF
  die "Fill the values above, then re-run."
fi
ok "Required public URLs are set"

# CORS must allow the app's own origin. If it doesn't, every page loads but
# every API call fails in the browser — the most confusing way a deploy breaks.
app_origin="$(grep -E "^PUBLIC_BASE_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed -E 's#^(https?://[^/]+).*#\1#' || true)"
cors="$(grep -E "^CORS_ALLOW_ORIGINS=" "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
case ",${cors// /}," in
  *",${app_origin},"*) ok "CORS_ALLOW_ORIGINS includes ${app_origin}" ;;
  *,\*,*) warn "CORS_ALLOW_ORIGINS is * — works, but any website can call your API from a visitor's browser" ;;
  *) die "CORS_ALLOW_ORIGINS (${cors:-empty}) does not include ${app_origin} (from PUBLIC_BASE_URL). The site would load but no API call would work. Set CORS_ALLOW_ORIGINS=${app_origin} in .env, then re-run." ;;
esac

# --- 5. Build + up -----------------------------------------------------------
say "Building images and starting the stack (this can take a few minutes)"
docker compose up -d --build

# --- 6. Wait for readiness ---------------------------------------------------
say "Waiting for the backend to report ready"
ready=""
for i in $(seq 1 40); do
  if docker compose exec -T backend curl -fsS http://localhost:8000/readyz >/dev/null 2>&1; then
    ready="yes"; break
  fi
  sleep 3
done

echo
if [ -n "$ready" ]; then
  ok "Backend is READY. Stack is up."
  docker compose ps
  cat <<EOF

\033[1;32m508 Agent is running.\033[0m  Frontend is served on this host's port 8080.

Next steps (only you can do these):
  1. Point your domain at this server — recommended: Cloudflare Tunnel
     (see deploy/cloudflare-tunnel.md) so you get TLS + WAF with no open ports.
     Map  app.yourdomain.com -> http://frontend:8080  and
          api.yourdomain.com -> http://backend:8000.
  2. In Stripe: add a webhook to  https://api.yourdomain.com/billing/webhook
     and confirm STRIPE_* values in .env, then:  docker compose up -d backend
  3. Go/no-go check (no money spent):
       docker compose exec -T backend python -m app.devtools.verify_live_site \\
         --api-url https://api.yourdomain.com
  4. Buy a Starter pack with Stripe test card 4242 4242 4242 4242 and confirm
     your credit balance updates.

Full detail: deploy/SHIP-CHECKLIST.md
EOF
else
  warn "Backend did not become ready in time. Check logs:"
  echo "    docker compose logs backend --tail=80"
  echo "    docker compose ps"
  die "Not ready — see logs above."
fi
