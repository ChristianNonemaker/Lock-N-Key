# Data Pipeline And Ops

Last reviewed: 2026-05-05

## Data Sources

- ESPN: free schedules/results for NCAAB, NCAAF, NFL, MLB.
- The Odds API: quota-limited DraftKings core odds; default active target is MLB only.
- The Odds API event odds: bounded/manual MLB team totals and selected player props.
- MLB Stats API: no-key MLB schedules, results, boxscores, player/team logs, probable
  starters, venues, and raw lineage.
- Baseball Savant/Statcast: bounded CSV/download imports into daily player features.
- Chadwick Register: MLB player ID crosswalk.
- NWS: bounded MLB weather/wind snapshots; field-relative wind only with reviewed venue
  orientation metadata.
- Action Network splits: exploratory/brittle Playwright scraper; keep off cron.
- KenPom/AP: manual NCAAB imports.

## Ingestion Policy

- Preserve append-only odds, event odds, splits, raw payloads, and results.
- `dk_ncaab/config/sports.py` is the sport/provider source of truth.
- `dk_ncaab/config/props.py` is the MLB event-specific market source of truth.
- Use `python -m dk_ncaab mlb-daily-research-cycle` as the bounded runbook.
- Event-specific odds stay manual until quota and quality are boring.

## Quota Defaults

- Monthly Odds API budget: 500.
- Reserve: 50.
- Max sports per run: 1.
- Min interval per sport: 360 minutes.
- Max attempts per due sport: 1.
- Event-specific odds default max events: 1.

## Production Runtime

- SQLite DB: `artifacts/dk_ncaab.sqlite3`.
- Collector: `scripts/cron_collect_cycle.sh`.
- API/UI: systemd services installed by `scripts/install_systemd_services.sh`.
- Remote UI: Tailscale Serve only.
- FastAPI remains localhost-only.
- FastAPI docs are disabled by default through `api.enable_docs=false`.

## VM State

Latest read-only check found `odds-vm` unreachable over Tailscale. Treat VM promotion as
blocked until it is online.

Before any VM DB action:

1. Verify Tailscale SSH and Streamlit health.
2. Back up SQLite and verify restore.
3. Confirm Alembic head.
4. Check whether protected lineage already exists.
5. Seed from local only if VM protected lineage is empty; otherwise append-only merge.

## Validation

```bash
python -m dk_ncaab status
alembic heads
alembic current
pytest tests/test_odds_quota.py tests/test_sports_registry.py -q
```
