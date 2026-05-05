# DK NCAAB / Lock-N-Key Research Pipeline

Private sports betting research pipeline for schedules, DraftKings lines, public
context, feature generation, strict entry-EV evidence, and a Streamlit sportsbook
board.

## Runtime

Production is SQLite + cron + systemd + Tailscale Serve:

- DB: `sqlite:///artifacts/dk_ncaab.sqlite3`
- API: FastAPI on `127.0.0.1:8000`
- UI: Streamlit on `127.0.0.1:8501`
- Remote access: Tailscale Serve only
- FastAPI docs: disabled by default through `api.enable_docs=false`

Docker/Postgres/APScheduler paths remain local or legacy alternatives.

## Main Packages

- `collectors/`: ESPN schedules/results, The Odds API core odds, event-specific MLB
  markets, MLB Stats API, environment, identities, Statcast, and manual imports.
- `etl/`: normalization, snapshots, feature rows, and settlement labels.
- `analysis/`: dataset export, strict OOF entry-EV, readiness, market history, and
  evidence-growth artifacts.
- `db/`: SQLAlchemy schema and Alembic migrations.
- `api/`: read-only FastAPI routes for the dashboard.
- `ui/`: Streamlit pages, led by `Sportsbook Board`.

## Quick Start

```bash
pip install -e ".[dev]"
alembic upgrade head
python -m dk_ncaab --help
python -m dk_ncaab status
uvicorn api.main:app --host 127.0.0.1 --port 8000
streamlit run ui/app.py --server.address 127.0.0.1
```

Set secrets through environment variables or production secret loading:

```bash
export DKNCAAB_DATABASE__URL="sqlite:///artifacts/dk_ncaab.sqlite3"
export DKNCAAB_ODDS_API__KEY="..."
```

Use `DKNCAAB_API__ENABLE_DOCS=true` only for local API development.

## Core Commands

```bash
python -m dk_ncaab load-games
python -m dk_ncaab update-results --sport baseball_mlb
python -m dk_ncaab collect-odds
python -m dk_ncaab collect-event-odds --sport baseball_mlb --max-events 1
python -m dk_ncaab build-dataset
python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60
python -m dk_ncaab mlb-evidence-growth-log --label daily
```

## Validation

```bash
ruff check api ui dk_ncaab tests
pytest tests -q
python scripts/check_sportsbook_board_screenshots.py
```

Do not run live provider collectors as routine tests. Keep paid/quota-backed calls
manual and bounded.
