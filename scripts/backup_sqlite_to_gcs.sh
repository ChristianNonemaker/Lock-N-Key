#!/usr/bin/env bash
set -euo pipefail

# === Usage =====================================================
# backup_sqlite_to_gcs.sh [--db-path <path>] [--bucket <name>] [--backup-dir <path>] [--dry-run]
# ==============================================================

DB_PATH="artifacts/dk_ncaab.sqlite3"
BUCKET="odds-collector-raw-us-central1"
BACKUP_DIR="artifacts/backups"
DRY_RUN=0
VERIFY_RESTORE=1
PYTHON_CMD="${PYTHON_CMD:-python3}"
MARKER_FILE="$BACKUP_DIR/last_backup_success_utc.txt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
    --backup-dir)
      BACKUP_DIR="$2"
      MARKER_FILE="$BACKUP_DIR/last_backup_success_utc.txt"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --skip-restore-verify)
      VERIFY_RESTORE=0
      shift
      ;;
    --python-cmd)
      PYTHON_CMD="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 [--db-path <path>] [--bucket <name>] [--backup-dir <path>] [--dry-run] [--skip-restore-verify] [--python-cmd <path>]"
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

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  echo "Python command not found: $PYTHON_CMD" >&2
  exit 1
fi

STAGE_DIR="$(mktemp -d "$BACKUP_DIR/sqlite_backup_stage.XXXXXX")"
cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

SNAPSHOT_DB="$STAGE_DIR/dk_ncaab.sqlite3"

"$PYTHON_CMD" - "$DB_PATH" "$SNAPSHOT_DB" <<'PY'
from __future__ import annotations

import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
with sqlite3.connect(target) as conn:
    result = conn.execute("PRAGMA quick_check").fetchone()
if not result or result[0] != "ok":
    raise SystemExit(f"SQLite quick_check failed: {result!r}")
PY

"$PYTHON_CMD" - "$SNAPSHOT_DB" "$STAGE_DIR/SHA256SUMS" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

db_path = Path(sys.argv[1])
checksum_path = Path(sys.argv[2])
checksum = hashlib.sha256(db_path.read_bytes()).hexdigest()
checksum_path.write_text(f"{checksum}  {db_path.name}\n", encoding="utf-8")
PY

{
  echo "created_at_utc=$STAMP"
  echo "source_db=$DB_PATH"
  echo "backup_db=dk_ncaab.sqlite3"
} > "$STAGE_DIR/manifest.txt"

tar -czf "$ARCHIVE" -C "$STAGE_DIR" dk_ncaab.sqlite3 SHA256SUMS manifest.txt

if [[ "$VERIFY_RESTORE" -eq 1 ]]; then
  PYTHON_CMD="$PYTHON_CMD" bash "$SCRIPT_DIR/restore_sqlite_backup.sh" --archive "$ARCHIVE" --verify-only
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run: created and verified $ARCHIVE; would upload to gs://$BUCKET/backups/"
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
