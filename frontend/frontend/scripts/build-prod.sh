#!/usr/bin/env bash
# Build the Expo Router static web bundle for production.
# Output is written to ./dist (relative to frontend/frontend), plus ./nginx.conf
# for serving it. Mirrors the post-export steps in the frontend Dockerfile so a
# self-hosted build gets the same sitemap, link check, routing and headers.
#
# Usage:
#   EXPO_PUBLIC_API_URL=https://api.example.com \
#   PUBLIC_BASE_URL=https://app.example.com ./scripts/build-prod.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

if [ ! -d node_modules ]; then
  echo "node_modules not found - running npm install..."
  npm install
fi

# Clean previous bundle so stale chunks do not leak into the new one.
rm -rf dist

# Canonical URLs are inlined from this at build time (Seo.tsx).
export EXPO_PUBLIC_SITE_URL="${PUBLIC_BASE_URL:-}"

echo "Building static web bundle..."
npx expo export -p web --output-dir dist
rm -f dist/_sitemap.html
node scripts/generate-sitemap.mjs dist
node scripts/check-internal-links.mjs dist
node scripts/render-nginx-conf.mjs dist nginx.conf

echo "Build complete. Output: ${PROJECT_DIR}/dist (serve with ${PROJECT_DIR}/nginx.conf)"
