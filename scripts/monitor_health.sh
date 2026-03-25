#!/usr/bin/env bash
set -euo pipefail

# Health monitor checks:
# - no odds data for >15 minutes
# - disk usage >80%
# - backup missing/stale (>26h)
# - unexpected restart (@reboot mode)
# Sends email alerts via scripts/send_email_alert.py using SMTP creds from GCP Secret Manager.

PROJECT_DIR="$HOME/dk_ncaab"
PROJECT_ID="odds-collector-prod"
STATE_DIR="$PROJECT_DIR/artifacts/state/alerts"
BACKUP_MARKER="$PROJECT_DIR/artifacts/backups/last_backup_success_utc.txt"
MAX_STALE_MIN=15
MAX_DISK_PCT=80
MAX_BACKUP_AGE_HOURS=26
COOLDOWN_SEC=3600
REBOOT_CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)
      PROJECT_DIR="$2"
      STATE_DIR="$PROJECT_DIR/artifacts/state/alerts"
      BACKUP_MARKER="$PROJECT_DIR/artifacts/backups/last_backup_success_utc.txt"
      shift 2
      ;;
    --project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    --reboot-check)
      REBOOT_CHECK=1
      shift
      ;;
    --help|-h)
      echo "Usage: $0 [--project-dir <path>] [--project-id <id>] [--reboot-check]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "$PROJECT_DIR"
mkdir -p "$STATE_DIR" "$PROJECT_DIR/artifacts/logs"

send_alert() {
  local key="$1"
  local subject="$2"
  local body="$3"

  local state_file="$STATE_DIR/${key}.epoch"
  local now_epoch
  now_epoch="$(date +%s)"

  if [[ -f "$state_file" ]]; then
    local last_epoch
    last_epoch="$(cat "$state_file" || echo 0)"
    if (( now_epoch - last_epoch < COOLDOWN_SEC )); then
      echo "$(date -u +%FT%TZ) alert_suppressed key=$key" >> "$PROJECT_DIR/artifacts/logs/health_monitor.log"
      return 0
    fi
  fi

  if ! command -v gcloud >/dev/null 2>&1; then
    echo "$(date -u +%FT%TZ) gcloud missing, cannot send alert" >> "$PROJECT_DIR/artifacts/logs/health_monitor.log"
    return 1
  fi

  local smtp_host smtp_port smtp_user smtp_pass from_email to_email
  smtp_host="$(gcloud secrets versions access latest --secret=ALERT_SMTP_HOST --project="$PROJECT_ID")"
  smtp_port="$(gcloud secrets versions access latest --secret=ALERT_SMTP_PORT --project="$PROJECT_ID" 2>/dev/null || echo 587)"
  smtp_user="$(gcloud secrets versions access latest --secret=ALERT_SMTP_USER --project="$PROJECT_ID")"
  smtp_pass="$(gcloud secrets versions access latest --secret=ALERT_SMTP_PASS --project="$PROJECT_ID")"
  from_email="$(gcloud secrets versions access latest --secret=ALERT_FROM_EMAIL --project="$PROJECT_ID" 2>/dev/null || echo "$smtp_user")"
  to_email="$(gcloud secrets versions access latest --secret=ALERT_TO_EMAIL --project="$PROJECT_ID" 2>/dev/null || echo "nonemakerc05@gmail.com")"

  ALERT_SUBJECT="$subject" \
  ALERT_BODY="$body" \
  ALERT_SMTP_HOST="$smtp_host" \
  ALERT_SMTP_PORT="$smtp_port" \
  ALERT_SMTP_USER="$smtp_user" \
  ALERT_SMTP_PASS="$smtp_pass" \
  ALERT_FROM_EMAIL="$from_email" \
  ALERT_TO_EMAIL="$to_email" \
  python3 scripts/send_email_alert.py

  echo "$now_epoch" > "$state_file"
  echo "$(date -u +%FT%TZ) alert_sent key=$key" >> "$PROJECT_DIR/artifacts/logs/health_monitor.log"
}

if [[ "$REBOOT_CHECK" -eq 1 ]]; then
  send_alert \
    "unexpected_restart" \
    "[DK NCAAB] VM restart detected" \
    "Host reboot event detected on $(hostname) at $(date -u +%FT%TZ)."
  exit 0
fi

LATEST_ODDS_TS="$(python3 - <<'PY'
from datetime import datetime, timezone
from sqlalchemy import select
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import OddsQuote

with SessionLocal() as session:
    row = session.execute(
        select(OddsQuote.collected_at_utc).order_by(OddsQuote.collected_at_utc.desc()).limit(1)
    ).scalar_one_or_none()
if row is None:
    print("")
else:
    if row.tzinfo is None:
        row = row.replace(tzinfo=timezone.utc)
    print(row.isoformat())
PY
)"

if [[ -z "$LATEST_ODDS_TS" ]]; then
  send_alert \
    "no_data_15m" \
    "[DK NCAAB] No odds data found" \
    "No odds quotes found in database at $(date -u +%FT%TZ)."
else
  AGE_MIN="$(python3 - <<PY
from datetime import datetime, timezone
latest = datetime.fromisoformat("$LATEST_ODDS_TS")
now = datetime.now(timezone.utc)
print(int((now - latest).total_seconds() // 60))
PY
)"
  if (( AGE_MIN > MAX_STALE_MIN )); then
    send_alert \
      "no_data_15m" \
      "[DK NCAAB] No data for > ${MAX_STALE_MIN}m" \
      "Latest odds quote is ${AGE_MIN} minutes old (${LATEST_ODDS_TS})."
  fi
fi

DISK_PCT="$(df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
if (( DISK_PCT > MAX_DISK_PCT )); then
  send_alert \
    "disk_over_80" \
    "[DK NCAAB] Disk usage high (${DISK_PCT}%)" \
    "Disk usage is ${DISK_PCT}% on $(hostname) at $(date -u +%FT%TZ)."
fi

if [[ ! -f "$BACKUP_MARKER" ]]; then
  send_alert \
    "backup_failure" \
    "[DK NCAAB] Backup marker missing" \
    "Backup success marker not found at ${BACKUP_MARKER}."
else
  BACKUP_AGE_HOURS="$(python3 - <<PY
from datetime import datetime, timezone
with open("$BACKUP_MARKER", "r", encoding="utf-8") as f:
    stamp = f.read().strip().split()[0]
ts = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
now = datetime.now(timezone.utc)
print(int((now - ts).total_seconds() // 3600))
PY
)"
  if (( BACKUP_AGE_HOURS > MAX_BACKUP_AGE_HOURS )); then
    send_alert \
      "backup_failure" \
      "[DK NCAAB] Backup stale (> ${MAX_BACKUP_AGE_HOURS}h)" \
      "Last successful backup marker is ${BACKUP_AGE_HOURS} hours old."
  fi
fi

echo "$(date -u +%FT%TZ) health_ok" >> "$PROJECT_DIR/artifacts/logs/health_monitor.log"
