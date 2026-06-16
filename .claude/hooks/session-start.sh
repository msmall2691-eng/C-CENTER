#!/bin/bash
# SessionStart hook for Claude Code on the web.
# Installs backend (pip) and frontend (npm) dependencies so tests, linters,
# and the app itself are ready when the session begins.
set -euo pipefail

# Only run in the remote (Claude Code on the web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(pwd)}"

echo "[session-start] Installing backend Python dependencies..."
if [ -f "$PROJECT_DIR/backend/requirements.txt" ]; then
  python3 -m pip install --user --upgrade pip >/dev/null
  python3 -m pip install --user -r "$PROJECT_DIR/backend/requirements.txt"
fi

echo "[session-start] Installing frontend npm dependencies..."
if [ -f "$PROJECT_DIR/frontend/package.json" ]; then
  npm install --prefix "$PROJECT_DIR/frontend"
fi

echo "[session-start] Done."
