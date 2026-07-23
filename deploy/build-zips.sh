#!/usr/bin/env bash
# Build API + Web zip packages for Azure App Service manual upload.
# Preferred path: merge to main → GitHub Actions auto-deploys (see deploy/README.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARTIFACTS="$ROOT/deploy/artifacts"
API_URL="${AZURE_API_URL:-https://relationship-sourcing-api-dagshxhjachtgrfn.canadacentral-01.azurewebsites.net}"

mkdir -p "$ARTIFACTS"
rm -f "$ARTIFACTS"/*.zip

echo "==> Building frontend (API base: $API_URL)"
cd "$ROOT/frontend"
export VITE_API_BASE_URL="$API_URL"
npm run build

echo "==> API zip"
STAGE_API="$ARTIFACTS/api-stage"
rm -rf "$STAGE_API"
mkdir -p "$STAGE_API"
rsync -a "$ROOT/backend/" "$STAGE_API/" \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'data/*.db' \
  --exclude 'data/*.db-*'
mkdir -p "$STAGE_API/data"
(cd "$STAGE_API" && zip -rq "$ARTIFACTS/relationship-sourcing-api.zip" .)

echo "==> Web zip"
STAGE_WEB="$ARTIFACTS/web-stage"
rm -rf "$STAGE_WEB"
mkdir -p "$STAGE_WEB/dist"
cp -R "$ROOT/frontend/dist/"* "$STAGE_WEB/dist/"
cp "$ROOT/deploy/web-host/package.json" "$STAGE_WEB/"
cp "$ROOT/deploy/web-host/server.js" "$STAGE_WEB/"
(cd "$STAGE_WEB" && zip -rq "$ARTIFACTS/relationship-sourcing-web.zip" .)

rm -rf "$STAGE_API" "$STAGE_WEB"

echo ""
echo "Done. Upload these in Azure Portal:"
echo "  $ARTIFACTS/relationship-sourcing-api.zip  -> relationship-sourcing-api"
echo "  $ARTIFACTS/relationship-sourcing-web.zip  -> relationship-sourcing-web"
