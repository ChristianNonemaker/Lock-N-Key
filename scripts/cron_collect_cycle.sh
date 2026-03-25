#!/usr/bin/env bash
set -euo pipefail

# === Usage =====================================================
# cron_collect_cycle.sh [--project-dir <path>] [--python-cmd <path>] [--log-dir <path>] [--lock-file <path>] [--timeout-sec <n>]
#
# Runs one restart-safe collector cycle intended for cron.
# ==============================================================

PROJECT_DIR="$HOME/dk_ncaab"
PYTHON_CMD="python3"
LOG_DIR="artifacts/logs"
LOCK_FILE="/tmp/dk_ncaab_collect.lock"
TIMEOUT_SEC=240

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
    --log-dir)
      LOG_DIR="$2"
      shift 2
      ;;
    --lock-file)
      LOCK_FILE="$2"
      shift 2
      ;;
    --timeout-sec)
      TIMEOUT_SEC="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--project-dir <path>] [--python-cmd <path>] [--log-dir <path>] [--lock-file <path>] [--timeout-sec <n>]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "$PROJECT_DIR"
mkdir -p "$LOG_DIR"

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  echo "Python command not found: $PYTHON_CMD" >&2
  exit 1
fi

if ! command -v flock >/dev/null 2>&1; then
  echo "flock not found (expected from util-linux on Debian)" >&2
  exit 1
fi

# === Overlap Guard ============================================
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "$(date -u +%FT%TZ) lock busy, skipping cycle" >> "$LOG_DIR/collector_cron.log"
  exit 0
fi

run_step() {
  local label="$1"
  shift
  if timeout "$TIMEOUT_SEC" "$@"; then
    echo "$(date -u +%FT%TZ) step_ok=$label"
  else
    echo "$(date -u +%FT%TZ) step_fail=$label rc=$?"
  fi
}

# === Collector Steps (one-shot, restart-safe) =================
{
  echo "$(date -u +%FT%TZ) cycle start"
  run_step load-games "$PYTHON_CMD" -m dk_ncaab load-games
  run_step collect-odds "$PYTHON_CMD" -m dk_ncaab collect-odds
  run_step collect-splits "$PYTHON_CMD" -m dk_ncaab collect-splits
  run_step update-results "$PYTHON_CMD" -m dk_ncaab update-results
  echo "$(date -u +%FT%TZ) cycle end"
} >> "$LOG_DIR/collector_cron.log" 2>&1
