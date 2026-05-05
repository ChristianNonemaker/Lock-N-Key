# Repo Map

Last reviewed: 2026-05-05

## Source Of Truth

- Product direction: `directions.md`
- Deployment stabilization: `plans/deployment-stabilization-plan.md`
- Private dashboard roadmap: `plans/private-odds-dashboard-roadmap.md`
- Active sprint plan: `plans/three-sprint-odds-workstation-plan.md`
- Production foundation: `plans/production-foundation.md`
- Agent rules: `AGENTS.md` and `.github/copilot-instructions.md`

Old sprint/orchestration docs live under `plans/archive/2026-05-stale/`.

## Main Packages

- `dk_ncaab/config/`: settings, sport registry, prop registry.
- `dk_ncaab/db/`: SQLAlchemy models, session, Alembic migrations.
- `dk_ncaab/collectors/`: ESPN, odds, event odds, MLB stats/environment/identity.
- `dk_ncaab/etl/`: normalization, snapshots, features, outcomes.
- `dk_ncaab/analysis/`: dataset, strict EV, readiness, history, evidence growth.
- `api/`: read-only FastAPI.
- `ui/`: Streamlit dashboard.
- `scripts/`: ops scripts and diagnostics.

## Common Commands

```bash
pip install -e ".[dev]"
alembic upgrade head
python -m dk_ncaab --help
python -m dk_ncaab status
pytest tests -q
ruff check api ui dk_ncaab tests
uvicorn api.main:app --host 127.0.0.1 --port 8000
streamlit run ui/app.py --server.address 127.0.0.1
python scripts/check_sportsbook_board_screenshots.py
```

## Conventions

- Production DB is SQLite.
- Production API/UI are localhost-bound and published only via Tailscale Serve.
- Bare pytest should collect only `tests/`.
- Script probes live in `scripts/diagnostics/`; they are not regression tests.
- Generated data lives under `artifacts/` or `temp/` and is ignored.
