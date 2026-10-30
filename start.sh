#!/usr/bin/env bash
# Anankos Sim Lab - launcher for Linux/macOS (dev + Tailscale hosts)
set -euo pipefail
cd "$(dirname "$0")"

export ANANKOS_HOST="${ANANKOS_HOST:-0.0.0.0}"
export ANANKOS_PORT="${ANANKOS_PORT:-47811}"

echo "============================================"
echo " ANANKOS SIM LAB  (training simulation only)"
echo "============================================"

if [ ! -d ".venv" ]; then
  echo "[setup] creating virtual environment..."
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  echo "[setup] installing dependencies..."
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo ""
echo " Local:     http://127.0.0.1:${ANANKOS_PORT}"
if command -v tailscale >/dev/null 2>&1; then
  TSIP="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
  [ -n "${TSIP}" ] && echo " Tailscale: http://${TSIP}:${ANANKOS_PORT}   (open this on your phone)"
else
  echo " Tailscale: not detected. Install + 'tailscale up' to reach this from your phone."
fi
echo ""

exec python -m simlab
