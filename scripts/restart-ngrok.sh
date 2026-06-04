#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${NGROK_DOMAIN:-discern-statute-viability.ngrok-free.dev}"
PORT="${NGROK_PORT:-8011}"
LOG_FILE="${NGROK_LOG:-/tmp/ngrok.log}"

pkill ngrok 2>/dev/null || true
sleep 2

nohup ngrok http "${PORT}" --url="https://${DOMAIN}" >"${LOG_FILE}" 2>&1 &
sleep 3

if curl -sf "http://127.0.0.1:4040/api/tunnels" >/tmp/ngrok-tunnels.json; then
  python3 - <<'PY'
import json
from pathlib import Path

data = json.loads(Path("/tmp/ngrok-tunnels.json").read_text())
for tunnel in data.get("tunnels", []):
    print(f"public_url={tunnel.get('public_url')}")
    print(f"addr={tunnel.get('config', {}).get('addr')}")
PY
else
  echo "ngrok API unavailable; log tail:"
  tail -30 "${LOG_FILE}" || true
  exit 1
fi
