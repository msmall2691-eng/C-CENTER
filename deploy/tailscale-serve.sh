#!/usr/bin/env bash
#
# Publish Command Center over Tailscale with HTTPS, so you can reach the whole
# app (UI + API on :8000) from your phone at a private https://<machine>.ts.net URL.
#
#   deploy/tailscale-serve.sh        # turn it on (background)
#   deploy/tailscale-serve.sh off    # turn it off
#
# The backend must already be running on :8000 (./start.sh or the systemd service).

set -uo pipefail

PORT="${PORT:-8000}"

if ! command -v tailscale >/dev/null 2>&1; then
  echo "✗ tailscale not installed — get it at https://tailscale.com/download"
  exit 1
fi

case "${1:-serve}" in
  off|stop|reset)
    tailscale serve reset 2>/dev/null || true
    echo "✓ Tailscale serve turned off."
    exit 0 ;;
esac

# Warn (don't block) if nothing is listening yet.
if command -v curl >/dev/null 2>&1 && \
   ! curl -s -o /dev/null --max-time 3 "http://localhost:$PORT/api/health"; then
  echo "⚠️  Nothing answering on http://localhost:$PORT/api/health"
  echo "    Start the app first:  ./start.sh   (or the systemd service)"
  echo ""
fi

echo "▸ publishing localhost:$PORT over Tailscale HTTPS…"
# Note: needs MagicDNS + HTTPS certificates enabled in the Tailscale admin console
# (DNS tab). On older Tailscale, use: tailscale serve https / http://localhost:$PORT
tailscale serve --bg "$PORT"

echo ""
tailscale serve status 2>/dev/null || true

URL="$(tailscale status --json 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
if [ -n "${URL:-}" ]; then
  echo ""
  echo "Open from any device signed into your tailnet:"
  echo "    https://$URL"
  echo ""
  echo "If your frontend is on Vercel, point it here once (it's remembered):"
  echo "    https://<your-vercel-url>/?backend=$URL"
fi
