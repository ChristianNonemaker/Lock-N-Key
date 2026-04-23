# Production Foundation

Last reviewed: 2026-04-20

## Decision

The production foundation is a private single-user VM running:

- SQLite at `artifacts/dk_ncaab.sqlite3` as the production database.
- Cron one-shot collection through `scripts/cron_collect_cycle.sh`.
- Systemd API/UI services installed by `scripts/install_systemd_services.sh`.
- API and Streamlit bound to `127.0.0.1`.
- Tailscale Serve as the only intended remote access path.
- Daily SQLite backups through `scripts/backup_sqlite_to_gcs.sh`, with local
  restore verification before upload.

Postgres and Docker Compose remain in the repo as local/legacy experiments.
They are not the active VM production runtime unless a future decision replaces
this profile.

## Private Boundary

The production API and UI should not bind to public interfaces. Streamlit is
published only through Tailscale Serve, and FastAPI remains localhost-only for
the UI. Docker Compose files are localhost-bound to reduce accidental exposure
when used locally.

There is still no app-level authentication. Tailnet identity and localhost
binding are the current security boundary.

## Backup And Restore

Backups are SQLite snapshots created with Python's SQLite backup API. Each
archive includes:

- `dk_ncaab.sqlite3`
- `SHA256SUMS`
- `manifest.txt`

`scripts/backup_sqlite_to_gcs.sh` verifies the archive locally with
`scripts/restore_sqlite_backup.sh --verify-only` before upload. A restore drill
can be run without GCS or API calls:

```bash
bash scripts/backup_sqlite_to_gcs.sh --db-path artifacts/dk_ncaab.sqlite3 --dry-run
bash scripts/restore_sqlite_backup.sh --archive artifacts/backups/<archive>.tar.gz --verify-only
```

## Validation

Use local/offline checks first:

```bash
bash -n scripts/backup_sqlite_to_gcs.sh scripts/restore_sqlite_backup.sh scripts/install_cron_jobs.sh scripts/install_systemd_services.sh
.\.venv\Scripts\python.exe -m pytest tests/test_backup_restore.py -v
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

Do not run live collectors or browser scrapers as part of this foundation
validation.
