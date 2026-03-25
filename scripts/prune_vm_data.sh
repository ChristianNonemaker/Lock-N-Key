#!/usr/bin/env bash
set -euo pipefail

# === Retention Policy =========================================
# VM raw snapshot retention: 7 days
# VM log retention: 7 days
# ==============================================================

RAW_DIR="artifacts/raw_html"
LOG_DIR="artifacts/logs"
RAW_DAYS=7
LOG_DAYS=7
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      echo "Usage: $0 [--dry-run]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

prune_dir() {
  local dir="$1"
  local days="$2"
  if [[ ! -d "$dir" ]]; then
    return 0
  fi
  if [[ "$DRY_RUN" -eq 1 ]]; then
    find "$dir" -type f -mtime +"$days" -print
  else
    find "$dir" -type f -mtime +"$days" -delete
  fi
}

prune_dir "$RAW_DIR" "$RAW_DAYS"
prune_dir "$LOG_DIR" "$LOG_DAYS"

echo "Prune complete (dry-run=$DRY_RUN)"
