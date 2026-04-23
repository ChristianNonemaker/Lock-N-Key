# Project Guidelines

## Instruction Upkeep
- Treat this file as a living source of direction for humans and AI agents.
- Read this file, `AGENTS.md`, and the relevant `memory/*.md` file before making cross-cutting changes.
- Update this file whenever architecture, workflow, deployment policy, UI navigation, data-source policy, or validation expectations change.
- Keep updates concise and durable. Do not paste raw logs or temporary investigation notes here; put short-lived discoveries in `memory/`.

## Code Style
- Python 3.11 and Ruff line length 100 (`pyproject.toml`).
- Follow typed settings + DI patterns in `dk_ncaab/config/settings.py`, `dk_ncaab/db/session.py`, and `api/deps.py`.
- Expose user-facing workflows through `python -m dk_ncaab` in `dk_ncaab/__main__.py`.
- Prefer small functions and module-level loggers (`logging.getLogger(__name__)`).

## Architecture
- Platform profile: private single-user system on GCP/Tailscale. Current reachable host is Tailscale node `odds-vm` at `100.127.13.111`, with repo path `/home/nonemakerc05/dk_ncaab`.
- VM is an always-on, low-resource collector; local machine must not be required for ingestion.
- Data flow: `collectors` → `etl` → SQL tables (`dk_ncaab/db/models.py`) → `analysis`.
- Sport/provider capability source of truth: `dk_ncaab/config/sports.py`. Add or change sport eligibility, provider keys, enrichers, and UI availability there first.
- Collector and website are logically separate; web reads data but collector never depends on web availability.
- Scheduler policy: cron-based, restart-safe jobs every 5 minutes (no infinite-loop collector daemon).
- Cron safety default: `scripts/cron_collect_cycle.sh` runs free ESPN games/results, but skips paid/heavy odds and splits unless `DKNCAAB_CRON_RUN_ODDS=1` or `DKNCAAB_CRON_RUN_SPLITS=1` is explicitly set.
- DB policy: SQLite at `artifacts/dk_ncaab.sqlite3` is the production default on the VM; keep access layers migration-friendly, but do not treat Postgres as production unless a future decision changes this profile.
- Current VM runtime uses systemd services: `dk-ncaab-api.service` on `127.0.0.1:8000` and `dk-ncaab-ui.service` on `127.0.0.1:8501`, published only through Tailscale Serve. Docker Compose and APScheduler paths are legacy/dev alternatives, not the active production runtime.

## GUI and Hosting Criteria
- Keep UX classy and readable, but lightweight for a free/small cloud VM.
- Keep UI in Streamlit (`ui/app.py`, `ui/pages/*`); iterate instead of rewrites.
- Use clear hierarchy: summary KPIs → key charts/tables → drill-down.
- Avoid heavy assets, aggressive refresh loops, and expensive per-rerun queries.
- Desktop: richer filters/tables/charts; Mobile: simplified, touch-friendly, quick-scanning layouts.
- Keep the Streamlit sidebar collapsed by default so mobile opens on the primary board content instead of navigation.
- The main sportsbook-style workflow is `Sportsbook Board`, backed by compact `GET /board`, single-game `GET /events/{event_id}/research`, and batch `GET /events/research`.
- Sportsbook Board should support sport switching, Live/Today/Upcoming filters, URL state (`page`, `sport`, `mode`, `date`, `event_id`), compact game rows, expandable research panels, freshness warnings, and a private persisted Research Slip for clicked lines.
- Research Slip is for private review only; the app does not place wagers. It persists locally at `artifacts/state/research_watchlist.json`.
- Expanded game panels should surface line movement, team metrics, public splits, model/feature context, and player-stat empty states until a player data provider is wired.
- Website is private-only via Tailscale access (no anonymous/public access), authenticated by tailnet identity, HTTPS, and minimal exposed routes.
- Current private UI URL is `https://odds-vm.tail1282c7.ts.net`, proxied by Tailscale Serve to Streamlit. FastAPI should remain localhost-only on the VM.

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
- API/UI with Docker: `docker compose up -d` is localhost-bound legacy/dev only. Production uses `scripts/install_systemd_services.sh` plus `scripts/install_cron_jobs.sh`.
- Tests: `pytest tests/ -v`

## Project Conventions
- Preserve append-only odds/splits history; avoid destructive updates (`dk_ncaab/db/models.py`).
- Keep both raw artifacts and normalized outputs (`settings.storage.*`, `settings.yaml`).
- Use deterministic OPEN/T60/T30/CLOSE extraction in `dk_ncaab/etl/snapshots.py`.
- Backtests must use American entry prices and explicit settlement math; pushes are 0-unit results, not losses.
- Spread and total backtests must settle against the entry anchor line, not the close line, because moved lines can flip W/L/P.
- Temporal validation for model evidence must keep all rows for an event in the same fold and prefer out-of-fold predictions.
- Entry-time feature selection must be sport-aware and anchor-aware; exclude close-aware, future-anchor, outcome, CLV, and full pre-tip volatility fields unless the feature is proven available at entry time.
- UI-promotable EV evidence must come from `python -m dk_ncaab oof-entry-ev` or an equivalent strict artifact path that uses out-of-fold outcome probabilities and `price_american_<anchor>`. Do not present close-movement proxy artifacts as true EV.
- Prefer YAML defaults with `DKNCAAB_` env overrides over hardcoded values.
- Do not create ad hoc sport maps in collectors, API routes, or UI pages; use registry helpers and add tests in `tests/test_sports_registry.py`.
- Use VM retention windows as policy defaults: raw 7d, logs 7d, GCS raw 14d.
- Backups are daily; VM storage is short-term working storage, not long-term archive. SQLite backups must pass local restore verification through `scripts/restore_sqlite_backup.sh` before upload.

## Integration Points
- Odds source: The Odds API via `dk_ncaab/collectors/odds_api.py`.
- MLB team/player trends: MLB Stats API via `dk_ncaab/collectors/mlb_stats.py`, stored in provider-backed player, team game log, player game log, probable starter, provider-key, and raw payload tables.
- Do not enable automated odds polling until the active sports list and cadence fit the free quota; one collection can fan out across multiple sports.
- Current quota-safe odds default is only `baseball_mlb`. ESPN schedule defaults include NCAAB, NCAAF, NFL, and MLB. NBA and soccer are planned registry placeholders and must stay disabled until provider mappings and tests land.
- Odds API usage must be append-only in `odds_api_usage`. Enforce `max_sports_per_run`, per-sport cadence, and reserve budget before making HTTP calls; never rely on a single collection invocation as the quota unit.
- Odds API request retries are capped by `odds_api.max_request_attempts`; default is 1 so quota-gated local/VM pulls do not silently spend extra free-tier requests on transient provider failures.
- Splits source: Playwright flow in `dk_ncaab/collectors/splits_dknetwork.py`.
- Games/results: ESPN loaders in `dk_ncaab/collectors/load_games.py`.
- ESPN schedule/result behavior for NCAAB, NCAAF, NFL, and MLB is covered by no-network tests in `tests/test_espn_load_games.py`; add fixture tests before enabling NBA or soccer.
- Shared boundary: SQLAlchemy models/session in `dk_ncaab/db/models.py` and `dk_ncaab/db/session.py`.
- Board API: `api.main:sportsbook_board` returns compact rows for the main odds board and batches team/quote/split prefetches for visible events.
- Research API: `api.main:event_research` returns expanded per-game context for one detail panel; `api.main:event_research_batch` returns multiple expanded payloads for watchlist/detail refreshes.
- Entry-EV API: `GET /analysis/entry-ev/latest` reports whether a strict OOF entry-EV artifact exists for the board.
- MLB readiness API: `GET /analysis/mlb/readiness` is a local-only diagnostic surface for MLB pregame odds, provider mapping, prior team logs, probable starters, prior starter logs, pending settlement, and settled trainable events. It must not call provider APIs or spend odds quota.
- CLI MLB stats collection: `python -m dk_ncaab collect-mlb-stats --start-date YYYY-MM-DD --end-date YYYY-MM-DD`; this does not use Odds API quota but should still be run in bounded date windows. Defaults cap boxscore fetches through `mlb_stats.max_boxscores_per_run` and delay requests through `mlb_stats.request_delay_sec`.

## Security
- Production secrets must come from Google Cloud Secret Manager (no `.env` in production).
- SSH model: prefer OS Login + IAM-based SSH long term, but current working admin path is Tailscale SSH to `odds-vm`. No password SSH or repo-managed keys.
- Keep DB/API exposure minimal; no public anonymous API.
- Network model: Tailscale-only private access; no public ingress for API/UI.
- Treat `artifacts/` as sensitive operational data; do not expose full raw payloads publicly.
- Alerts route to `nonemakerc05@gmail.com` for collector failure, no data >15m, disk >80%, backup/sync failure, and unexpected restart.
