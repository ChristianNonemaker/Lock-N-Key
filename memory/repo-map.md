# Repo Map

Last reviewed: 2026-04-22

## Source Of Truth

- Product goal: `directions.md`
- Current delivery plan: `plans/master-execution-plan.md`
- GCP/private VM plan: `plans/gcp-vm-orchestration-plan.md`
- Existing Copilot guidance: `.github/copilot-instructions.md`
- Codex guidance: `AGENTS.md`

## Main Packages

- `dk_ncaab/config/`: sport/provider registry plus typed settings from `settings.yaml` with `DKNCAAB_` env overrides.
- `dk_ncaab/db/`: SQLAlchemy models, session factory, Alembic migration.
- `dk_ncaab/collectors/`: ESPN, The Odds API, Action Network splits, KenPom/AP imports.
- `dk_ncaab/etl/`: normalization, snapshot extraction, feature generation.
- `dk_ncaab/analysis/`: parquet dataset build, models, model store, backtests, reports.
- `dk_ncaab/jobs/`: auto collector and legacy scheduler.
- `api/`: FastAPI read-only service.
- `ui/`: Streamlit dashboard.
- `scripts/`: deployment, cron, health, backup, debugging, diagnostics.

## Common Commands

- Install: `pip install -e ".[dev]"`
- Playwright runtime: `playwright install chromium`
- Migrate: `alembic upgrade head`
- CLI help: `python -m dk_ncaab --help`
- Tests: `pytest tests/ -v`
- API: `uvicorn api.main:app --reload --port 8000`
- UI: `streamlit run ui/app.py`
- Strict entry-EV artifacts: `python -m dk_ncaab oof-entry-ev --input-parquet artifacts/parquet/features_YYYYMMDD.parquet --anchor T60`
- Populated board screenshots without live API calls: `python scripts/check_sportsbook_board_screenshots.py`
- Docker dev: `docker compose up -d`

## Important Conventions

- Odds and splits are append-style time series.
- Snapshots are derived at query time, not stored in a snapshot table.
- `selected_event_id` is the Streamlit session-state handoff between non-board game selection pages and detail/model pages.
- Sportsbook Board URL state uses query params (`page`, `sport`, `mode`, `date`, `event_id`) and persists its private watchlist in `artifacts/state/research_watchlist.json`.
- Current active registry sports: NCAAB, NCAAF, NFL, MLB. NBA and soccer are disabled planned entries.

## Watch For

- Existing worktree may be dirty. Do not revert user changes.
- Some files contain mojibake in comments/docstrings from prior encoding issues. Avoid broad formatting churn unless requested.
- Scripts under `scripts/test_*.py` are diagnostics, not the same as pytest regression tests.
