#!/usr/bin/env bash
#
# Command Center — start the backend and frontend together.
#
#   ./start.sh
#
# Backend runs on :8000, frontend on :5173, both bound to 0.0.0.0 so you can
# reach them from another device on your network (or over Tailscale). Press
# Ctrl+C once to stop both.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- sanity checks -----------------------------------------------------------
if [ ! -f "$ROOT/backend/.env" ]; then
  echo "⚠️  backend/.env not found."
  echo "    Run:  cp backend/.env.example backend/.env"
  echo "    Then set ANTHROPIC_API_KEY and WORKSPACE_DIR in it, and re-run ./start.sh"
  echo ""
fi

# Use the backend virtualenv's uvicorn if present, otherwise whatever is on PATH.
if [ -x "$ROOT/backend/.venv/bin/uvicorn" ]; then
  UVICORN="$ROOT/backend/.venv/bin/uvicorn"
else
  UVICORN="uvicorn"
fi

# Install frontend deps on first run.
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "▸ installing frontend dependencies (first run only)…"
  ( cd "$ROOT/frontend" && npm install )
fi

# --- start both servers ------------------------------------------------------
echo "▸ starting backend  on :8000"
( cd "$ROOT/backend" && exec "$UVICORN" main:app --host 0.0.0.0 --port 8000 ) &
BACK=$!

echo "▸ starting frontend on :5173"
( cd "$ROOT/frontend" && exec npm run dev -- --host ) &
FRONT=$!

# Kill a process and all its descendants (npm spawns Vite as a grandchild).
kill_tree() {
  local pid=$1 child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

cleanup() {
  echo ""
  echo "▸ shutting down…"
  kill_tree "$BACK"
  kill_tree "$FRONT"
  wait 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# --- print where to open it --------------------------------------------------
echo ""
echo "Command Center is up:"
echo "  • on this computer : http://localhost:5173"
if command -v tailscale >/dev/null 2>&1; then
  TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
  if [ -n "$TS_IP" ]; then
    echo "  • from your phone  : http://$TS_IP:5173   (Tailscale must be on)"
  fi
fi
echo ""
echo "Press Ctrl+C to stop both."
echo ""

wait
