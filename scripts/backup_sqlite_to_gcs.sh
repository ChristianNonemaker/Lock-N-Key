#!/usr/bin/env bash
set -euo pipefail

# === Usage =====================================================
# backup_sqlite_to_gcs.sh [--db-path <path>] [--bucket <name>] [--dry-run]
# ==============================================================

DB_PATH="artifacts/dk_ncaab.sqlite3"
BUCKET="odds-collector-raw-us-central1"
BACKUP_DIR="artifacts/backups"
DRY_RUN=0
MARKER_FILE="$BACKUP_DIR/last_backup_success_utc.txt"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db-path)
      DB_PATH="$2"
      shift 2
      ;;
    --bucket)
      BUCKET="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --help|-h)
      echo "Usage: $0 [--db-path <path>] [--bucket <name>] [--dry-run]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="$BACKUP_DIR/sqlite_backup_${STAMP}.tar.gz"

if [[ ! -f "$DB_PATH" ]]; then
  echo "SQLite DB not found at $DB_PATH" >&2
  exit 1
fi

tar -czf "$ARCHIVE" "$DB_PATH"

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run: would upload $ARCHIVE to gs://$BUCKET/backups/"
  exit 0
fi

if ! command -v gsutil >/dev/null 2>&1; then
  echo "gsutil not found" >&2
  exit 1
fi

gsutil cp "$ARCHIVE" "gs://$BUCKET/backups/"
OBJECT_URI="gs://$BUCKET/backups/$(basename "$ARCHIVE")"

if ! gsutil ls "$OBJECT_URI" >/dev/null 2>&1; then
  echo "Upload verification failed: $OBJECT_URI not found" >&2
  exit 1
fi

echo "$(date -u +%FT%TZ) $OBJECT_URI" > "$MARKER_FILE"
echo "Backup uploaded and verified: $OBJECT_URI"
