# Deployment Stabilization Plan

Last reviewed: 2026-05-05

## Goal

Stabilize the local private sportsbook workstation, then promote it to the VM only after
tests, docs, evidence labeling, and the deployment boundary are boring.

## Scope

- Deployment sports: NCAAB, NCAAF, NFL, and MLB.
- MLB is the reference sport for line reasoning, event-specific markets, and strict
  entry-EV evidence.
- NBA and Soccer/EPL remain planned placeholders until provider contracts, tests, and
  quota cadence are ready.
- Production runtime remains SQLite + cron + systemd + Tailscale Serve.

## Local Stabilization

- Keep bare `pytest` safe by limiting pytest discovery to `tests/` and keeping script
  diagnostics under `scripts/diagnostics/`.
- Keep `temp/` and all generated artifacts out of source control.
- Disable FastAPI docs by default with `api.enable_docs=false`; use
  `DKNCAAB_API__ENABLE_DOCS=true` only for local development.
- Treat strict OOF artifacts as validation plumbing until promotion gates pass.
- Use promotion fields on evidence payloads:
  - `promotion_status`
  - `promotion_gaps`
  - `min_oof_rows`
  - `min_settled_events`
  - `min_posted_line_samples`
- Keep the Research Slip session-state only and append every pin/update/remove/clear to
  `artifacts/state/research_ledger.jsonl`.

## Evidence Growth

- Resolve remaining unlinked event-specific MLB player quotes through the identity
  reconciliation dry-run/apply path.
- Grow the thin event-specific markets first:
  - `pitcher_strikeouts`
  - `team_totals`
- After bounded collection and settlement, rebuild the dataset, rerun strict
  `oof-entry-ev`, and append an evidence-growth snapshot.
- Do not cron event-specific odds yet.

## VM Promotion Gate

- Bring `odds-vm` online before promotion.
- Verify Tailscale SSH, Streamlit health, API `/status`, systemd services, cron logs,
  disk, backups, and Alembic migration head.
- Back up VM SQLite before any database change.
- If VM protected lineage is empty, seed from local SQLite. If VM has odds, splits,
  raw payloads, event odds, or result lineage, use an append-only merge/import path.
- Keep VM cron odds/splits disabled until secrets, quota, and collector behavior are
  verified.

## Validation

```bash
ruff check api ui dk_ncaab tests
pytest tests -q
python -m dk_ncaab status
alembic heads
alembic current
python scripts/check_sportsbook_board_screenshots.py
python -m dk_ncaab build-dataset
python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60
python -m dk_ncaab mlb-evidence-growth-log --label post-stabilization
```
