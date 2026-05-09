#!/usr/bin/env bash
# Build the Expo Router static web bundle for production.
# Output is written to ./dist (relative to frontend/frontend).
#
# Usage:
#   EXPO_PUBLIC_API_URL=https://api.example.com ./scripts/build-prod.sh
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

echo "Building static web bundle..."
npx expo export -p web --output-dir dist

echo "Build complete. Output: ${PROJECT_DIR}/dist"
