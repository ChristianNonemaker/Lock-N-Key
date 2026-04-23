# Data Pipeline And Ops

Last reviewed: 2026-04-22

## Data Sources

- ESPN: free schedules/results through scoreboard-style loaders. This is the cleanest no-cost source in the repo.
- MLB Stats API: free public MLB schedule/boxscore source for provider-backed team logs, player logs, probable starters, and raw payload lineage.
- The Odds API: DraftKings lines source. Free-tier budget is assumed to be about 500 requests/month.
- Action Network public betting page: scraped with Playwright for public betting splits. Money/handle percentage may be paywalled, so current captures can be limited.
- KenPom and AP rankings: manual import paths, not live automated free APIs.
- Sport/provider eligibility is defined in `dk_ncaab/config/sports.py`. Do not add ad hoc sport maps elsewhere.

## Ingestion Shape

- `load_games.py` seeds leagues, teams, events, and event results.
- `odds_api.py` collects moneyline/spread/total prices and raw odds payloads.
- `mlb_stats.py` collects MLB schedule/result/starter context plus final team/player boxscores without using Odds API quota.
- `splits_dknetwork.py` scrapes splits, stores raw HTML/screenshot evidence, matched split quotes, and unmatched rows.
- `auto_collect.py` runs ESPN every cycle and conditionally runs odds/splits plus dataset build.
- `cron_collect_cycle.sh` is the preferred newer one-shot cron wrapper in plans.
- Current safety default: cron runs ESPN games/results, but skips odds and splits unless `DKNCAAB_CRON_RUN_ODDS=1` or `DKNCAAB_CRON_RUN_SPLITS=1` is set.
- ESPN schedule/results tests now cover NCAAB, NCAAF, NFL, and MLB without live
  API calls. NBA/soccer should not be enabled until fixture tests pin provider mappings.

## Sport Registry Defaults

ESPN schedule/result collection is enabled by default for NCAAB, NCAAF, NFL, and MLB.
The Odds API is collectable for those same four sports, but the default active odds
target remains only `baseball_mlb` until request accounting is fixed. NBA and
soccer are planned-but-disabled registry entries.

## Free-Tier Quota Risk

`collect_odds()` can fan out over each configured sport, so one "cycle" may be multiple Odds API requests. Request attempts are now recorded per sport in `odds_api_usage`; cadence, `max_sports_per_run`, and reserve checks happen before HTTP calls.

Do not enable automated odds polling until the active sports list and cadence fit the free quota. True live odds are not realistic on the free Odds API tier without very selective polling.

Local-first is the current development approach for odds/model iteration: prove the one-shot collector, feature export, OOF EV artifacts, API, and UI locally; then promote the same guarded path to the VM. Keep VM cron odds disabled until local behavior is boring.

Current quota defaults:

- monthly request budget: 500
- reserve requests: 50
- max sports per run: 1
- min interval per sport: 360 minutes
- max request attempts per due sport: 1
- MLB Stats API max boxscores per run: 50
- MLB Stats API boxscore request delay: 0.1 seconds

## Private Hosting Shape

The intended target is a single low-resource GCP Debian VM, Tailscale-only access, cron-driven collection, and GCP Secret Manager for production secrets. API/UI should stay private and collector work should continue even if the UI is down.

## Current VM Access

- Tailscale node: `odds-vm` at `100.127.13.111`.
- Deployed repo: `/home/nonemakerc05/dk_ncaab`.
- Services: `dk-ncaab-api.service` on `127.0.0.1:8000` and `dk-ncaab-ui.service` on `127.0.0.1:8501`.
- Private UI URL: `https://odds-vm.tail1282c7.ts.net`, served through Tailscale Serve to Streamlit.
- FastAPI stays localhost-only on the VM; Streamlit reaches it through `API_BASE=http://127.0.0.1:8000`.
- Google Cloud CLI currently sees project `odds-vm`, but Compute API/billing was not usable during the last check. Tailscale SSH is the working admin path.
- After the VM restart, Tailscale SSH to `root@odds-vm` worked again on 2026-04-22.

## Current Data State

Last VM status check showed 729 teams, 5423 historical/final events, 0 upcoming events, 0 odds quotes, 0 split quotes, and successful cron cycles. Artifact inventory found only `artifacts/dk_ncaab.sqlite3` and no VM parquet/backup file with odds data under `artifacts/`. A local SQLite smoke on 2026-04-22 now has MLB odds plus MLB Stats API history: 200 odds quotes, 186 MLB results, 210 team logs, 5439 player logs, 389 probable starters, and a fresh 1116-row parquet with MLB trend columns. Strict EV trainable events remain 0 because no settled event currently has both a valid pregame odds quote and a result. The board is therefore deployed but empty until schedule/odds collection is deliberately enabled and repaired on the VM.

Observed blockers:

- VM had no `.env`, so `DKNCAAB_ODDS_API__KEY` was absent and The Odds API returned 401. The collector now skips cleanly when the key is missing and redacts `apiKey=` in HTTP logs.
- Cron is installed every five minutes, but current logs show `step_skip=collect-odds reason=disabled` and `step_skip=collect-splits reason=disabled`; this is intentional quota/load protection until explicit env flags are enabled.
- Playwright Chromium and Debian browser dependencies were installed, but the Action Network splits scrape can still hang on the small VM. Keep cron splits disabled until the scraper has a bounded timeout and better failure signal. After dependency installation, disk was about 6.3G used of 9.7G.

## Production Foundation

Production is now documented as SQLite + cron + systemd + Tailscale Serve:

- SQLite DB: `artifacts/dk_ncaab.sqlite3`.
- Collector owner: `scripts/cron_collect_cycle.sh` installed by `scripts/install_cron_jobs.sh`.
- API/UI owner: `scripts/install_systemd_services.sh`, bound to `127.0.0.1`.
- Remote UI access: Tailscale Serve only.
- Restore-verified SQLite backups: `scripts/backup_sqlite_to_gcs.sh` verifies local restore through `scripts/restore_sqlite_backup.sh` before upload.

Legacy/alternative paths still exist and should not be started alongside cron:

- `python -m dk_ncaab auto`
- `python -m dk_ncaab.jobs.scheduler`
- Debian cron scripts
- Windows Task Scheduler scripts
- Docker compose services

Only one should own production ingestion at a time.

## Storage Conflict

SQLite is the production default now. Docker compose still provisions Postgres for local/legacy experiments, so code should remain migration-friendly, but production backup/deploy hardening should target SQLite unless a future decision replaces this profile.

## Operational Gaps

- Restore verification exists for SQLite archives through `scripts/restore_sqlite_backup.sh`; a full scheduled restore drill against a staging DB is still a future operational task.
- Health checks cover useful basics but do not fully probe API/UI, Tailscale, cron freshness, Postgres readiness, or artifact sharing.
- Docker compose is localhost-bound now, but remains legacy/dev; production private access should still use systemd plus Tailscale Serve.
- API CORS is restricted by config, but there is still no app-level auth.
- VM disk is tight for Playwright, logs, raw HTML, screenshots, database, parquet, and backups.
