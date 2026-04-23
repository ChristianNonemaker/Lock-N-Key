#!/usr/bin/env bash
set -euo pipefail

# Install persistent systemd services for API and Streamlit UI.
# Run as root on Debian VM.

PROJECT_DIR="/home/nonemakerc05/dk_ncaab"
RUN_USER="nonemakerc05"
API_PORT="8000"
UI_PORT="8501"
ENABLE_TAILSCALE_SERVE="true"
DB_URL="sqlite:///$PROJECT_DIR/artifacts/dk_ncaab.sqlite3"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --run-user)
      RUN_USER="$2"
      shift 2
      ;;
    --api-port)
      API_PORT="$2"
      shift 2
      ;;
    --ui-port)
      UI_PORT="$2"
      shift 2
      ;;
    --no-tailscale-serve)
      ENABLE_TAILSCALE_SERVE="false"
      shift
      ;;
    --help|-h)
      echo "Usage: sudo bash scripts/install_systemd_services.sh [options]"
      echo "  --project-dir <path>      Project directory (default: /home/nonemakerc05/dk_ncaab)"
      echo "  --run-user <user>         Linux user for services (default: nonemakerc05)"
      echo "  --api-port <port>         API port (default: 8000)"
      echo "  --ui-port <port>          UI port (default: 8501)"
      echo "  --no-tailscale-serve      Skip tailscale serve setup"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "This script must run as root (use sudo)." >&2
  exit 1
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Project directory not found: $PROJECT_DIR" >&2
  exit 1
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  echo "Missing Python venv executable: $PROJECT_DIR/.venv/bin/python" >&2
  exit 1
fi

mkdir -p "$PROJECT_DIR/artifacts/logs"
chown -R "$RUN_USER:$RUN_USER" "$PROJECT_DIR/artifacts"
DB_URL="sqlite:///$PROJECT_DIR/artifacts/dk_ncaab.sqlite3"

cat > /etc/systemd/system/dk-ncaab-api.service <<EOF
[Unit]
Description=DK NCAAB FastAPI service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port $API_PORT
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=DKNCAAB_DATABASE__URL=$DB_URL

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/dk-ncaab-ui.service <<EOF
[Unit]
Description=DK NCAAB Streamlit UI service
After=network-online.target dk-ncaab-api.service
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/streamlit run ui/app.py --server.address 127.0.0.1 --server.port $UI_PORT
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=DKNCAAB_DATABASE__URL=$DB_URL
Environment=API_BASE=http://127.0.0.1:$API_PORT

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now dk-ncaab-api.service
systemctl enable --now dk-ncaab-ui.service

if [[ "$ENABLE_TAILSCALE_SERVE" == "true" ]] && command -v tailscale >/dev/null 2>&1; then
  tailscale serve --https=443 "http://127.0.0.1:${UI_PORT}" || true
fi

echo "Installed services:"
systemctl --no-pager --full status dk-ncaab-api.service | sed -n '1,12p'
echo "---"
systemctl --no-pager --full status dk-ncaab-ui.service | sed -n '1,12p'
