#!/usr/bin/env bash
set -euo pipefail

# === Usage =====================================================
# install_cron_jobs.sh [--project-dir <path>] [--python-cmd <path>]
#
# Installs user cron entries for collector + maintenance jobs.
# ==============================================================

PROJECT_DIR="$HOME/dk_ncaab"
PYTHON_CMD="$PROJECT_DIR/.venv/bin/python"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir)
      PROJECT_DIR="$2"
      shift 2
      ;;
    --python-cmd)
      PYTHON_CMD="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--project-dir <path>] [--python-cmd <path>]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Project directory not found: $PROJECT_DIR" >&2
  exit 1
fi

CRON_BLOCK=$(cat <<EOF
# DK NCAAB cron jobs
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * cd $PROJECT_DIR && /bin/bash scripts/cron_collect_cycle.sh --project-dir $PROJECT_DIR --python-cmd $PYTHON_CMD
*/5 * * * * cd $PROJECT_DIR && /bin/bash scripts/monitor_health.sh --project-dir $PROJECT_DIR --project-id odds-collector-prod
15 3 * * * cd $PROJECT_DIR && /bin/bash scripts/prune_vm_data.sh
45 3 * * * cd $PROJECT_DIR && /bin/bash scripts/backup_sqlite_to_gcs.sh --python-cmd $PYTHON_CMD
@reboot cd $PROJECT_DIR && /bin/bash scripts/monitor_health.sh --project-dir $PROJECT_DIR --project-id odds-collector-prod --reboot-check
EOF
)

CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
FILTERED_CRON="$(printf '%s\n' "$CURRENT_CRON" | sed '/# DK NCAAB cron jobs/,+7d')"
{ printf '%s\n' "$FILTERED_CRON"; printf '%s\n' "$CRON_BLOCK"; } | sed '/^$/N;/^\n$/D' | crontab -

echo "Installed cron jobs:"
crontab -l
