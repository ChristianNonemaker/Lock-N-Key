# Project Guidelines

## Code Style
- Python 3.11 and Ruff line length 100 (`pyproject.toml`).
- Follow typed settings + DI patterns in `dk_ncaab/config/settings.py`, `dk_ncaab/db/session.py`, and `api/deps.py`.
- Expose user-facing workflows through `python -m dk_ncaab` in `dk_ncaab/__main__.py`.
- Prefer small functions and module-level loggers (`logging.getLogger(__name__)`).

## Architecture
- Platform profile: private single-user system on GCP (`odds-collector-prod`, `us-central1-a`, `e2-micro`, Debian x86_64).
- VM is an always-on, low-resource collector; local machine must not be required for ingestion.
- Data flow: `collectors` → `etl` → SQL tables (`dk_ncaab/db/models.py`) → `analysis`.
- Collector and website are logically separate; web reads data but collector never depends on web availability.
- Scheduler policy: cron-based, restart-safe jobs every 5 minutes (no infinite-loop collector daemon).
- DB policy: SQLite is primary now; keep access layers migration-friendly for later Postgres move.

## GUI and Hosting Criteria
- Keep UX classy and readable, but lightweight for a free/small cloud VM.
- Keep UI in Streamlit (`ui/app.py`, `ui/pages/*`); iterate instead of rewrites.
- Use clear hierarchy: summary KPIs → key charts/tables → drill-down.
- Avoid heavy assets, aggressive refresh loops, and expensive per-rerun queries.
- Desktop: richer filters/tables/charts; Mobile: simplified, touch-friendly, quick-scanning layouts.
- Website is private-only via Tailscale access (no anonymous/public access), authenticated by tailnet identity, HTTPS, and minimal exposed routes.
- Keep Docker-first hosting with private-first network boundaries and non-public API surface.

## Agent Orchestration Workflow
- Orchestrator is the single coordinator: scopes tasks, assigns subagents, merges results, and enforces goals.
- Subagent roles:
  - Scaffolding: file structure/stubs (with scaffold comments only when explicitly requested).
  - Implementation: business logic and integrations.
  - Quality: lint/tests/build checks and regression reporting.
  - UI/UX: visual consistency, readability, and tasteful polish in `ui/*`.
- Use stage gates: scaffold → implement → review/test → polish.
- Respin low-quality/drifted work with tighter constraints and explicit acceptance criteria.

## Definition of Done
- Requested objective is complete with minimal, focused diffs.
- Relevant validation is run and reported (tests/lint/build/command smoke checks).
- Data/collector changes preserve append-only history, timestamps, and lineage.
- UI changes improve clarity/style without harming VM resource profile.
- Handoff includes: files touched, validations run, unresolved risks, and next step.

## Build and Test
- Install: `pip install -e ".[dev]"`
- Browser runtime for splits collector: `playwright install chromium`
- Migrations: `alembic upgrade head`
- One-shot collection: `python -m dk_ncaab collect-odds`
- Cron target cadence: every 5 minutes (collector jobs must be idempotent/restart-safe).
- API/UI with Docker: `docker compose up -d` (dev), `docker compose -f docker-compose.prod.yml up -d` (prod)
- Tests: `pytest tests/ -v`

## Project Conventions
- Preserve append-only odds/splits history; avoid destructive updates (`dk_ncaab/db/models.py`).
- Keep both raw artifacts and normalized outputs (`settings.storage.*`, `settings.yaml`).
- Use deterministic OPEN/T60/T30/CLOSE extraction in `dk_ncaab/etl/snapshots.py`.
- Prefer YAML defaults with `DKNCAAB_` env overrides over hardcoded values.
- Use VM retention windows as policy defaults: raw 7d, logs 7d, GCS raw 14d.
- Backups are daily; VM storage is short-term working storage, not long-term archive.

## Integration Points
- Odds source: The Odds API via `dk_ncaab/collectors/odds_api.py`.
- Splits source: Playwright flow in `dk_ncaab/collectors/splits_dknetwork.py`.
- Games/results: ESPN loaders in `dk_ncaab/collectors/load_games.py`.
- Shared boundary: SQLAlchemy models/session in `dk_ncaab/db/models.py` and `dk_ncaab/db/session.py`.

## Security
- Production secrets must come from Google Cloud Secret Manager (no `.env` in production).
- SSH model: OS Login + IAM-based SSH only; no password SSH, no repo-managed keys.
- Keep DB/API exposure minimal; no public anonymous API.
- Network model: Tailscale-only private access; no public ingress for API/UI.
- Treat `artifacts/` as sensitive operational data; do not expose full raw payloads publicly.
- Alerts route to `nonemakerc05@gmail.com` for collector failure, no data >15m, disk >80%, backup/sync failure, and unexpected restart.