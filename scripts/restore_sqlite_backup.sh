#!/usr/bin/env bash
set -euo pipefail

# === Usage =====================================================
# restore_sqlite_backup.sh --archive <tar.gz> [--db-path <path>] [--verify-only] [--force]
#
# Offline SQLite backup verification/restore. This script never calls GCS or
# any sports/data API. It is safe for local restore drills when used with
# --verify-only, and requires --force before overwriting an existing DB.
# ==============================================================

ARCHIVE=""
DB_PATH=""
VERIFY_ONLY=0
FORCE=0
PYTHON_CMD="${PYTHON_CMD:-python3}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive)
      ARCHIVE="$2"
      shift 2
      ;;
    --db-path|--restore-to)
      DB_PATH="$2"
      shift 2
      ;;
    --verify-only)
      VERIFY_ONLY=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --python-cmd)
      PYTHON_CMD="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: $0 --archive <tar.gz> [--db-path <path>] [--verify-only] [--force] [--python-cmd <path>]"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$ARCHIVE" ]]; then
  echo "--archive is required" >&2
  exit 2
fi

if [[ ! -f "$ARCHIVE" ]]; then
  echo "Backup archive not found: $ARCHIVE" >&2
  exit 1
fi

if [[ "$VERIFY_ONLY" -ne 1 && -z "$DB_PATH" ]]; then
  echo "--db-path is required unless --verify-only is used" >&2
  exit 2
fi

if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  echo "Python command not found: $PYTHON_CMD" >&2
  exit 1
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dk_sqlite_restore.XXXXXX")"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

tar -xzf "$ARCHIVE" -C "$TMP_DIR"

RESTORED_DB="$(find "$TMP_DIR" -type f -name '*.sqlite3' | head -n 1)"
if [[ -z "$RESTORED_DB" ]]; then
  echo "No .sqlite3 file found inside $ARCHIVE" >&2
  exit 1
fi

CHECKSUM_FILE="$(find "$TMP_DIR" -type f -name 'SHA256SUMS' | head -n 1)"
if [[ -n "$CHECKSUM_FILE" ]]; then
  "$PYTHON_CMD" - "$CHECKSUM_FILE" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

checksum_file = Path(sys.argv[1]).resolve()
base = checksum_file.parent

for raw in checksum_file.read_text(encoding="utf-8").splitlines():
    raw = raw.strip()
    if not raw:
        continue
    expected, name = raw.split(maxsplit=1)
    target = (base / name).resolve()
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Checksum mismatch for {target.name}")
PY
fi

"$PYTHON_CMD" - "$RESTORED_DB" <<'PY'
from __future__ import annotations

import sqlite3
import sys

db_path = sys.argv[1]
with sqlite3.connect(db_path) as conn:
    result = conn.execute("PRAGMA quick_check").fetchone()
if not result or result[0] != "ok":
    raise SystemExit(f"SQLite quick_check failed: {result!r}")
PY

if [[ "$VERIFY_ONLY" -eq 1 ]]; then
  echo "Backup restore verification passed: $ARCHIVE"
  exit 0
fi

if [[ -e "$DB_PATH" && "$FORCE" -ne 1 ]]; then
  echo "Refusing to overwrite existing DB without --force: $DB_PATH" >&2
  exit 1
fi

mkdir -p "$(dirname "$DB_PATH")"
TMP_TARGET="${DB_PATH}.restore_tmp"
cp "$RESTORED_DB" "$TMP_TARGET"
mv "$TMP_TARGET" "$DB_PATH"

echo "Restored SQLite backup to $DB_PATH"
