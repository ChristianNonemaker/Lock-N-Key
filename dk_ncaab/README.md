# DK NCAAB – DraftKings College Basketball Research Pipeline

End-to-end system for collecting DraftKings NCAAB odds, public betting splits,
and game results — then building features and models to predict closing lines
and identify value.

## Architecture

Production foundation: SQLite on the private VM, cron one-shot collection,
systemd API/UI services bound to `127.0.0.1`, and Tailscale Serve for remote
access. Docker/Postgres/APScheduler paths remain legacy/dev alternatives.

```
dk_ncaab/
├── collectors/          # Data ingestion
│   ├── odds_api.py      # The-Odds-API → odds_quotes
│   ├── splits_dknetwork.py  # Playwright → splits_quotes
│   └── results.py       # Scores → event_results
├── etl/                 # Transform layer
│   ├── normalize.py     # Team names, odds math
│   ├── snapshots.py     # OPEN/T60/T30/CLOSE extraction
│   └── features.py      # Movement, velocity, volatility, CLV
├── analysis/            # Modeling + evaluation
│   ├── dataset_build.py # Join everything → Parquet
│   ├── correlation_report.py
│   ├── models_close_predict.py  # Ridge, LightGBM, Quantile
│   └── backtest.py      # CLV + ROI evaluation
├── db/                  # Postgres via SQLAlchemy + Alembic
│   ├── models.py
│   ├── session.py
│   └── migrations/
├── config/
│   ├── settings.yaml
│   └── settings.py
└── jobs/
    └── scheduler.py     # APScheduler orchestration
```

## Quick Start

### 1. Install dependencies
```bash
pip install -e ".[dev]"
playwright install chromium
```

### 2. Configure
Copy and edit settings:
```bash
# Set your API key via environment variables. SQLite is the VM default:
export DKNCAAB_ODDS_API__KEY="your-api-key"
export DKNCAAB_DATABASE__URL="sqlite:///artifacts/dk_ncaab.sqlite3"
```

### 3. Initialize database
```bash
alembic upgrade head
```

### 4. Run collectors
```bash
# One-shot:
python -c "from dk_ncaab.collectors.odds_api import collect_odds; collect_odds()"

# Production scheduled path:
bash scripts/cron_collect_cycle.sh --project-dir "$PWD" --python-cmd .venv/bin/python
```

### 5. Build features + analyze
```bash
python -c "from dk_ncaab.analysis.dataset_build import run_dataset_build; run_dataset_build()"
```

### 6. Run tests
```bash
pytest tests/ -v
```

## Key Design Decisions

- **Append-only quotes**: Every odds/splits poll inserts new rows. Never overwrite.
- **Dedup on insert**: `ON CONFLICT DO NOTHING` prevents duplicates on collector restart.
- **Deterministic snapshots**: OPEN/T60/T30/CLOSE are pure functions of the data — no interpolation.
- **CLV as primary metric**: Closing Line Value converges faster than ROI for evaluating edge.
- **Temporal CV only**: No shuffled cross-validation. Train on past, test on future.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DKNCAAB_DATABASE__URL` | SQLite production DB URL or optional dev Postgres URL |
| `DKNCAAB_ODDS_API__KEY` | The-Odds-API key |
| `DKNCAAB_SPLITS__HEADLESS` | `true`/`false` for Playwright |
| `DKNCAAB_POLLING__ODDS_BASELINE_SEC` | Override polling interval |
