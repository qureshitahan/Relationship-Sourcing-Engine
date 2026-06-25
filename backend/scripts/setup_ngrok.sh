#!/usr/bin/env bash
# Start ngrok for Apollo phone webhooks and write APP_PUBLIC_URL to backend/.env
#
# Prerequisites:
#   1. Free ngrok account: https://dashboard.ngrok.com/signup
#   2. Your authtoken:     https://dashboard.ngrok.com/get-started/your-authtoken
#
# Usage:
#   NGROK_AUTHTOKEN=your_token_here ./scripts/setup_ngrok.sh
#   # or if already configured:
#   ./scripts/setup_ngrok.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
PORT="${NGROK_PORT:-8000}"

if ! command -v ngrok >/dev/null 2>&1; then
  echo "ngrok not found. Install with: brew install ngrok/ngrok/ngrok"
  exit 1
fi

if [[ -n "${NGROK_AUTHTOKEN:-}" ]]; then
  echo "Configuring ngrok authtoken..."
  ngrok config add-authtoken "$NGROK_AUTHTOKEN"
fi

# Stop any existing ngrok on this port's API
if curl -sf http://127.0.0.1:4040/api/tunnels >/dev/null 2>&1; then
  echo "Stopping existing ngrok session..."
  pkill -f "ngrok http $PORT" 2>/dev/null || true
  sleep 1
fi

echo "Starting ngrok tunnel to localhost:$PORT ..."
ngrok http "$PORT" --log=stdout > /tmp/ngrok-leadgen.log 2>&1 &
NGROK_PID=$!
echo "ngrok pid: $NGROK_PID (logs: /tmp/ngrok-leadgen.log)"

PUBLIC_URL=""
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:4040/api/tunnels >/dev/null 2>&1; then
    PUBLIC_URL=$(curl -sf http://127.0.0.1:4040/api/tunnels \
      | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('tunnels', []):
    u = t.get('public_url', '')
    if u.startswith('https://'):
        print(u)
        break
" 2>/dev/null || true)
    [[ -n "$PUBLIC_URL" ]] && break
  fi
  sleep 1
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "Failed to get ngrok public URL."
  echo "Check /tmp/ngrok-leadgen.log — you may need to sign up and set NGROK_AUTHTOKEN."
  exit 1
fi

echo ""
echo "Public URL: $PUBLIC_URL"

# Update APP_PUBLIC_URL in .env
if grep -q '^APP_PUBLIC_URL=' "$ENV_FILE" 2>/dev/null; then
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|^APP_PUBLIC_URL=.*|APP_PUBLIC_URL=$PUBLIC_URL|" "$ENV_FILE"
  else
    sed -i "s|^APP_PUBLIC_URL=.*|APP_PUBLIC_URL=$PUBLIC_URL|" "$ENV_FILE"
  fi
else
  echo "APP_PUBLIC_URL=$PUBLIC_URL" >> "$ENV_FILE"
fi

WEBHOOK_SECRET=$(grep '^APOLLO_PHONE_WEBHOOK_SECRET=' "$ENV_FILE" | cut -d= -f2- || true)
WEBHOOK_URL="$PUBLIC_URL/api/webhooks/apollo/phone"
[[ -n "$WEBHOOK_SECRET" ]] && WEBHOOK_URL="${WEBHOOK_URL}?token=${WEBHOOK_SECRET}"

echo ""
echo "Updated backend/.env:"
echo "  APP_PUBLIC_URL=$PUBLIC_URL"
echo ""
echo "Apollo will send phone numbers to:"
echo "  $WEBHOOK_URL"
echo ""
echo "NEXT STEPS:"
echo "  1. Restart the backend (Ctrl+C in dev.sh terminal, then ./dev.sh again)"
echo "  2. Open a job → Find contacts (phones arrive in 2-5 minutes)"
echo "  3. Keep this ngrok process running (pid $NGROK_PID)"
echo ""
echo "To stop ngrok later: kill $NGROK_PID"
