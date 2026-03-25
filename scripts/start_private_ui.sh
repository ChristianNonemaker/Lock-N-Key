#!/usr/bin/env bash
set -euo pipefail

# Start Streamlit bound to localhost, then expose via Tailscale Serve HTTPS.
# Requires tailscaled running and authenticated.

PROJECT_DIR="$HOME/dk_ncaab"
PORT="8501"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--project-dir <path>] [--port <8501>]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "$PROJECT_DIR"

if [[ -x ".venv/bin/streamlit" ]]; then
  STREAMLIT_CMD=".venv/bin/streamlit"
else
  STREAMLIT_CMD="streamlit"
fi

nohup "$STREAMLIT_CMD" run ui/app.py --server.port "$PORT" --server.address 127.0.0.1 > artifacts/logs/ui.log 2>&1 &

tailscale serve --https=443 "http://127.0.0.1:${PORT}"

echo "Private UI is available via your tailnet URL:"
tailscale status --json | python3 - <<'PY'
import json,sys
s=json.load(sys.stdin)
url=s.get("Self",{}).get("DNSName")
if url:
    print(f"https://{url}")
else:
    print("Run: tailscale status")
PY
