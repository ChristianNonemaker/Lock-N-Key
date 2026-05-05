# Sprint 2: Alerting + Backup Verification (Debian, Tailscale-only)

## Scope Implemented
- Health monitor script checks:
  - no odds data for >15 minutes
  - disk usage >80%
  - backup marker missing/stale (>26h)
  - unexpected restart (`@reboot`)
- Backup script now verifies uploaded object exists in GCS and writes a success marker.
- Cron installer now includes health monitor every 5 minutes and reboot alert check.

## Files
- `scripts/monitor_health.sh`
- `scripts/send_email_alert.py`
- `scripts/backup_sqlite_to_gcs.sh`
- `scripts/install_cron_jobs.sh`
- `scripts/preflight_secrets.sh`

## Deploy Commands
```bash
cd ~/dk_ncaab
bash scripts/preflight_secrets.sh --project-id odds-collector-prod --with-alerts
bash scripts/install_cron_jobs.sh --project-dir ~/dk_ncaab --python-cmd ~/dk_ncaab/.venv/bin/python
bash scripts/monitor_health.sh --project-dir ~/dk_ncaab --project-id odds-collector-prod
```

## Optional Secret Keys
- `ALERT_SMTP_PORT` (default: 587)
- `ALERT_FROM_EMAIL` (default: SMTP user)
- `ALERT_TO_EMAIL` (default: nonemakerc05@gmail.com)

## Notes
- `monitor_health.sh` uses cooldown suppression (1 hour per alert key) to reduce spam.
- This is designed for Debian cron + systemd environments.
