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
STATE_DIR="artifacts/state"
RUNS_FILE="artifacts/state/runs.jsonl"
LOCK_FILE="/tmp/dk_ncaab_collect.lock"
TIMEOUT_SEC=240
RUN_ODDS="${DKNCAAB_CRON_RUN_ODDS:-0}"
RUN_SPLITS="${DKNCAAB_CRON_RUN_SPLITS:-0}"

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
mkdir -p "$STATE_DIR"

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
  local started_epoch
  started_epoch="$(date +%s)"

  local rc
  if timeout "$TIMEOUT_SEC" "$@"; then
    rc=0
    echo "$(date -u +%FT%TZ) step_ok=$label"
  else
    rc=$?
    echo "$(date -u +%FT%TZ) step_fail=$label rc=$rc"
  fi

  local ended_epoch
  ended_epoch="$(date +%s)"
  local duration_sec=$((ended_epoch - started_epoch))

  STEP_RC["$label"]="$rc"
  STEP_DUR["$label"]="$duration_sec"
}

skip_step() {
  local label="$1"
  local reason="$2"
  STEP_RC["$label"]=0
  STEP_DUR["$label"]=0
  echo "$(date -u +%FT%TZ) step_skip=$label reason=$reason"
}

enabled() {
  case "${1,,}" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

# === Collector Steps (one-shot, restart-safe) =================
declare -A STEP_RC
declare -A STEP_DUR

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
STARTED_UTC="$(date -u +%FT%TZ)"

{
  echo "$STARTED_UTC cycle start run_id=$RUN_ID"
  run_step load-games "$PYTHON_CMD" -m dk_ncaab load-games
  if enabled "$RUN_ODDS"; then
    run_step collect-odds "$PYTHON_CMD" -m dk_ncaab collect-odds
  else
    skip_step collect-odds disabled
  fi
  if enabled "$RUN_SPLITS"; then
    run_step collect-splits "$PYTHON_CMD" -m dk_ncaab collect-splits
  else
    skip_step collect-splits disabled
  fi
  run_step update-results "$PYTHON_CMD" -m dk_ncaab update-results
  echo "$(date -u +%FT%TZ) cycle end run_id=$RUN_ID"
} >> "$LOG_DIR/collector_cron.log" 2>&1

STATUS="success"
for step in load-games collect-odds collect-splits update-results; do
  if [[ "${STEP_RC[$step]:-1}" -ne 0 ]]; then
    STATUS="partial"
    break
  fi
done

COMPLETED_UTC="$(date -u +%FT%TZ)"
printf '{"run_id":"%s","started_at_utc":"%s","completed_at_utc":"%s","status":"%s","steps":{"load-games":{"rc":%s,"duration_sec":%s},"collect-odds":{"rc":%s,"duration_sec":%s},"collect-splits":{"rc":%s,"duration_sec":%s},"update-results":{"rc":%s,"duration_sec":%s}}}\n' \
  "$RUN_ID" "$STARTED_UTC" "$COMPLETED_UTC" "$STATUS" \
  "${STEP_RC[load-games]:-1}" "${STEP_DUR[load-games]:-0}" \
  "${STEP_RC[collect-odds]:-1}" "${STEP_DUR[collect-odds]:-0}" \
  "${STEP_RC[collect-splits]:-1}" "${STEP_DUR[collect-splits]:-0}" \
  "${STEP_RC[update-results]:-1}" "${STEP_DUR[update-results]:-0}" \
  >> "$RUNS_FILE"
