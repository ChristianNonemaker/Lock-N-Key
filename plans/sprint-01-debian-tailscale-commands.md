# Sprint 1 Commands (Debian + Tailscale-only)

Run these on VM `odds-vm` as user `nonemakerc05` unless marked `sudo`.

## 1) Repo + Python env
```bash
cd ~
[ -d dk_ncaab ] || git clone https://github.com/ChristianNonemaker/Lock-N-Key.git dk_ncaab
cd ~/dk_ncaab
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
playwright install chromium
```

## 2) Debian package checks (do not reinstall if present)
```bash
for p in python3 python3-pip python3-venv git sqlite3 nginx curl; do dpkg -s "$p" >/dev/null 2>&1 && echo "ok: $p" || echo "missing: $p"; done
```

## 3) Tailscale private access setup
```bash
cd ~/dk_ncaab
bash scripts/setup_tailscale_debian.sh --hostname odds-vm
```

## 4) GCP auth + secrets preflight
```bash
gcloud auth login
gcloud config set project odds-collector-prod
cd ~/dk_ncaab
bash scripts/preflight_secrets.sh --project-id odds-collector-prod --with-alerts
```

## 5) Install cron jobs (every 5 min collector)
```bash
cd ~/dk_ncaab
bash scripts/install_cron_jobs.sh --project-dir ~/dk_ncaab --python-cmd ~/dk_ncaab/.venv/bin/python
crontab -l
```

## 6) Dry-run maintenance jobs
```bash
cd ~/dk_ncaab
bash scripts/prune_vm_data.sh --dry-run
bash scripts/backup_sqlite_to_gcs.sh --dry-run --bucket odds-collector-raw-us-central1
```

## 7) Start private UI over Tailscale HTTPS
```bash
cd ~/dk_ncaab
mkdir -p artifacts/logs
bash scripts/start_private_ui.sh --project-dir ~/dk_ncaab --port 8501
```

## 8) Validation checks
```bash
cd ~/dk_ncaab
bash scripts/cron_collect_cycle.sh --project-dir ~/dk_ncaab --python-cmd ~/dk_ncaab/.venv/bin/python
python -m dk_ncaab --help
pytest tests/ -v
```

## 9) Recommended cron/system checks
```bash
systemctl status tailscaled --no-pager
crontab -l
tail -n 100 ~/dk_ncaab/artifacts/logs/collector_cron.log
tail -n 100 ~/dk_ncaab/artifacts/logs/health_monitor.log
```

## 10) Sprint 2: alert/backup health checks
```bash
cd ~/dk_ncaab
bash scripts/backup_sqlite_to_gcs.sh --dry-run --bucket odds-collector-raw-us-central1
bash scripts/monitor_health.sh --project-dir ~/dk_ncaab --project-id odds-collector-prod
```

## 11) Secret Manager keys for email alerts (minimum)
```bash
gcloud secrets create ALERT_SMTP_HOST --replication-policy="automatic" || true
gcloud secrets create ALERT_SMTP_USER --replication-policy="automatic" || true
gcloud secrets create ALERT_SMTP_PASS --replication-policy="automatic" || true

printf 'smtp.gmail.com' | gcloud secrets versions add ALERT_SMTP_HOST --data-file=-
printf 'your-smtp-user@gmail.com' | gcloud secrets versions add ALERT_SMTP_USER --data-file=-
printf 'your-app-password' | gcloud secrets versions add ALERT_SMTP_PASS --data-file=-
```

## 12) Persist API/UI with systemd (auto-restart + reboot-safe)
```bash
cd ~/dk_ncaab
sudo bash scripts/install_systemd_services.sh --project-dir ~/dk_ncaab --run-user nonemakerc05
sudo systemctl status dk-ncaab-api.service --no-pager
sudo systemctl status dk-ncaab-ui.service --no-pager

# Optional: verify private UI over Tailscale Serve
curl -I http://127.0.0.1:8501
curl -sSf http://127.0.0.1:8000/status | head -c 300
```
