#!/usr/bin/env bash
#
# Install Command Center as an always-on user service (Linux + systemd).
#
#   deploy/install-systemd.sh            # build UI, install + start the service
#   deploy/install-systemd.sh uninstall  # stop and remove it
#
# One process (uvicorn) serves both the UI and the API on :8000, auto-starts on
# boot, and restarts on crash. No root needed — it's a `systemctl --user` service.

set -uo pipefail

ACTION="${1:-install}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT="$HOME/.config/systemd/user/command-center.service"
PORT="${PORT:-8000}"

if ! command -v systemctl >/dev/null 2>&1; then
  echo "✗ systemctl not found — this kit is for Linux with systemd."
  echo "  (On macOS, ask Claude for the launchd version.)"
  exit 1
fi

if [ "$ACTION" = "uninstall" ]; then
  systemctl --user disable --now command-center 2>/dev/null || true
  rm -f "$UNIT"
  systemctl --user daemon-reload 2>/dev/null || true
  echo "✓ command-center service removed."
  exit 0
fi

# uvicorn must be an absolute path for systemd ExecStart.
if [ -x "$ROOT/backend/.venv/bin/uvicorn" ]; then
  UVICORN="$ROOT/backend/.venv/bin/uvicorn"
else
  UVICORN="$(command -v uvicorn || true)"
fi
if [ -z "${UVICORN:-}" ]; then
  echo "✗ uvicorn not found. Install backend deps first:"
  echo "    cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

[ -f "$ROOT/backend/.env" ] || \
  echo "⚠️  backend/.env missing — set ANTHROPIC_API_KEY before the agents will run."

echo "▸ building the UI so the backend can serve it…"
if ( cd "$ROOT/frontend" && npm install >/dev/null 2>&1 && npm run build >/dev/null 2>&1 ); then
  echo "  done"
else
  echo "  ⚠️ frontend build failed — the API will still run; fix and re-run to serve the UI"
fi

mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<EOF
[Unit]
Description=Command Center (agents backend + UI)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT/backend
# PATH is baked in so the Agent SDK can find node at runtime.
Environment=PATH=$PATH
Environment=PORT=$PORT
ExecStart=$UVICORN main:app --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now command-center
# keep the service running after logout / across reboots without an active login
loginctl enable-linger "$USER" >/dev/null 2>&1 || true

echo ""
echo "✓ Command Center is installed and running."
echo "    status : systemctl --user status command-center"
echo "    logs   : journalctl --user -u command-center -f"
echo "    stop   : systemctl --user stop command-center"
echo "    remove : deploy/install-systemd.sh uninstall"
echo ""
echo "    open   : http://localhost:$PORT"
if command -v tailscale >/dev/null 2>&1; then
  TS_IP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
  [ -n "$TS_IP" ] && echo "    phone  : http://$TS_IP:$PORT   (or run deploy/tailscale-serve.sh for HTTPS)"
fi
