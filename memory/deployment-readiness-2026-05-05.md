# Deployment Readiness 2026-05-05

Last reviewed: 2026-05-05

## Summary

Local is the source of progress. VM promotion is blocked until `odds-vm` is reachable
again over Tailscale.

## Findings

- `odds-vm` was reported offline/last seen 11 days ago.
- Streamlit health over Tailscale failed.
- Tailscale SSH to `root@odds-vm` failed with a dial/502 error.
- Local SQLite has meaningful MLB data and strict evidence artifacts.
- Full test suite was near-green before stabilization: one starter-rest assertion failed;
  bare pytest was also unsafe because script diagnostics were collected.

## Stabilization Decisions

- Deployment sports are NCAAB, NCAAF, NFL, MLB.
- NBA and Soccer/EPL are planned/disabled.
- FastAPI docs are off by default.
- Strict EV and line evidence expose promotion gates.
- Research Slip is session-state only; Research Ledger is append-only local JSONL.
- `temp/` and generated artifacts stay out of source control.

## Post-Stabilization Evidence

- Bounded `collect-event-odds` for one MLB event inserted 8 append-only rows.
- Provider headers reported 41 requests used and 459 remaining for that pull.
- Event-specific quotes now total 516: pitcher strikeouts 36, team totals 44.
- Reconciliation dry-run still reports 2 unresolved player quote identity gaps.
- Latest strict T60 EV remains sample-sensitive with non-positive recommended ROI.

## VM Gate

Before promotion:

1. Bring VM online.
2. Verify Tailscale SSH and Streamlit health.
3. Back up SQLite and verify restore.
4. Run `alembic upgrade head`.
5. Confirm cron/systemd/Tailscale are the only production owners.
6. Decide seed-vs-append-only-merge based on protected lineage already on the VM.

## Verification

```bash
ruff check api ui dk_ncaab tests
pytest tests -q
python -m dk_ncaab status
alembic heads
alembic current
python scripts/check_sportsbook_board_screenshots.py
```
