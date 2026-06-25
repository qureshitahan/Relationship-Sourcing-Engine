#!/usr/bin/env bash
# Convenience launcher: starts the FastAPI backend and the Vite frontend.
# Usage: ./dev.sh   (Ctrl-C stops both)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

# --- Backend ---
cd "$ROOT/backend"
if [ ! -d ".venv" ]; then
  echo "Creating backend virtualenv..."
  python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet -r requirements.txt
fi
echo "Starting backend on http://localhost:8000 ..."
./.venv/bin/uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

# --- Frontend ---
cd "$ROOT/frontend"
if [ ! -d "node_modules" ]; then
  echo "Installing frontend dependencies..."
  npm install
fi
echo "Starting frontend on http://localhost:5173 ..."
npm run dev &
FRONTEND_PID=$!

trap 'echo; echo "Stopping..."; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true' INT TERM
wait
