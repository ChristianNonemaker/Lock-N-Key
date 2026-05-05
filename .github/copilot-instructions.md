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
- Platform profile: private single-user system on GCP/Tailscale. Target host is Tailscale node `odds-vm` at `100.127.13.111`, with repo path `/home/nonemakerc05/dk_ncaab`; verify availability before any promotion because it was offline at the 2026-05-05 readiness check.
- VM is an always-on, low-resource collector; local machine must not be required for ingestion.
- Data flow: `collectors` → `etl` → SQL tables (`dk_ncaab/db/models.py`) → `analysis`.
- Sport/provider capability source of truth: `dk_ncaab/config/sports.py`. Add or change sport eligibility, provider keys, enrichers, and UI availability there first.
- Event-specific team-total and player-prop market definitions live in `dk_ncaab/config/props.py`. Do not create ad hoc prop-market lists in collectors, API routes, or UI code.
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
- Sportsbook Board should open like a private DraftKings-style slate: games sorted by time to start, current DraftKings lines visible on the row, and line/game clicks leading into the research suite.
- Daily Betting Queue is a secondary board lens for prioritized review. It should not replace the default time-ordered sportsbook board.
- Sportsbook Board should support sport switching, Live/Today/Upcoming filters, URL state (`page`, `sport`, `mode`, `date`, `lens`, `event_id`), compact game rows, expandable research panels, freshness warnings, a session-state Research Slip, and an append-only Research Ledger for clicked lines.
- Focused-line URL state also supports `focus_market`, `focus_side`, and optional `focus_key`. Use `focus_key` for participant-specific MLB lines so team totals and player props reopen to the exact selected team/player market.
- Research Slip is for private review only; the app does not place wagers. Durable notes go to the append-only private Research Ledger at `artifacts/state/research_ledger.jsonl` with line snapshot, focus key, thesis, note, status, and outcome fields.
- Expanded game panels should transform a clicked matchup/line into a data suite: all available markets, movement, EV evidence, market history, team/player context, environment, public splits, model/feature context, and honest player-line empty states when current event-specific quotes are not stored.
- Website is private-only via Tailscale access (no anonymous/public access), authenticated by tailnet identity, HTTPS, and minimal exposed routes.
- Current private UI URL is `https://odds-vm.tail1282c7.ts.net`, proxied by Tailscale Serve to Streamlit. FastAPI should remain localhost-only on the VM.
- FastAPI docs, ReDoc, and OpenAPI JSON are disabled by default in production through `api.enable_docs=false` / `DKNCAAB_API__ENABLE_DOCS=false`; enable them only for local diagnostics.

## Agent Orchestration Workflow
- Orchestrator is the single coordinator: scopes tasks, assigns subagents, merges results, and enforces goals.
- Codex repo-specific skills should live in `.agents/skills` for modern Codex discovery.
  `.github/skills` can document GitHub/Copilot workflows, and `.codex/skills` may still be
  useful for local compatibility, but avoid divergent copies without a reason.
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
- Treat exact-tip rows as ineligible for entry-time anchors: OPEN/T60/T30/CLOSE and related readiness/status counters use strict `< start_time_utc`, not `<=`.
- Backtests must use American entry prices and explicit settlement math; pushes are 0-unit results, not losses.
- Spread and total backtests must settle against the entry anchor line, not the close line, because moved lines can flip W/L/P.
- Temporal validation for model evidence must keep all rows for an event in the same fold and prefer out-of-fold predictions.
- Entry-time feature selection must be sport-aware and anchor-aware; exclude close-aware, future-anchor, outcome, CLV, and full pre-tip volatility fields unless the feature is proven available at entry time.
- UI-promotable EV evidence must come from `python -m dk_ncaab oof-entry-ev` or an equivalent strict artifact path that uses out-of-fold outcome probabilities and `price_american_<anchor>`. Do not present close-movement proxy artifacts as true EV.
- MLB event-specific EV rows must preserve participant identity (`participant_name`,
  entity type, team/player IDs) from `EventOddsQuote` through feature export and OOF
  prediction artifacts.
- Optional sparse context such as MLB participant Statcast features must not become a hard row filter. Keep base entry/outcome requirements strict, but handle optional feature gaps with training-fold-only imputation or an equally leakage-safe method.
- Prefer YAML defaults with `DKNCAAB_` env overrides over hardcoded values.
- Do not create ad hoc sport maps in collectors, API routes, or UI pages; use registry helpers and add tests in `tests/test_sports_registry.py`.
- Use VM retention windows as policy defaults: raw 7d, logs 7d, GCS raw 14d.
- Backups are daily; VM storage is short-term working storage, not long-term archive. SQLite backups must pass local restore verification through `scripts/restore_sqlite_backup.sh` before upload.

## Integration Points
- Odds source: The Odds API via `dk_ncaab/collectors/odds_api.py`.
- Event-specific team totals/player props: The Odds API event-odds path via `dk_ncaab/collectors/odds_event_markets.py`, normalized into append-only `EventOddsQuote` rows.
- MLB team/player trends: MLB Stats API via `dk_ncaab/collectors/mlb_stats.py`, stored in provider-backed player, team game log, player game log, probable starter, provider-key, and raw payload tables.
- Do not enable automated odds polling until the active sports list and cadence fit the free quota; one collection can fan out across multiple sports.
- Current quota-safe odds default is only `baseball_mlb`. ESPN schedule defaults include NCAAB, NCAAF, NFL, and MLB. NBA and soccer are planned registry placeholders and must stay disabled until provider mappings and tests land.
- Odds API usage must be append-only in `odds_api_usage`. Enforce `max_sports_per_run`, per-sport cadence, and reserve budget before making HTTP calls; never rely on a single collection invocation as the quota unit.
- Odds API request retries are capped by `odds_api.max_request_attempts`; default is 1 so quota-gated local/VM pulls do not silently spend extra free-tier requests on transient provider failures.
- Event-specific props/team totals must stay on the dedicated append-only `EventOddsQuote` path. Do not overload base `OddsQuote` for player or team-total markets.
- `collect-event-odds` is manual and bounded first. Keep it off cron until quota behavior and provider quality are proven locally.
- Splits source: Playwright flow in `dk_ncaab/collectors/splits_dknetwork.py`.
- Games/results: ESPN loaders in `dk_ncaab/collectors/load_games.py`.
- ESPN loaders should enrich an existing same-game event when odds created it first: match by stored ESPN provider key or by home/away teams within the time window, then attach the ESPN provider key instead of creating a second event row.
- MLB stats event matching should follow the same identity discipline: match the exact home/away pairing within the tighter start-time window, prefer lineage-rich existing events, and avoid attaching a second `mlb_stats_api` key to a conflicting event.
- ESPN schedule/result behavior for NCAAB, NCAAF, NFL, and MLB is covered by no-network tests in `tests/test_espn_load_games.py`; add fixture tests before enabling NBA or soccer.
- Shared boundary: SQLAlchemy models/session in `dk_ncaab/db/models.py` and `dk_ncaab/db/session.py`.
- Board API: `api.main:sportsbook_board` returns compact rows for the main odds board and batches team/quote/split prefetches for visible events.
- Board API rows include `slate_intelligence`, composed in `api/services/slate_intelligence.py`. Use it as the durable "why open this game?" contract: score, tier, headline, reasons, gaps, strongest move, split pressure, evidence label, and next action. Keep it local-only and cheap.
- Board rows should stay sportsbook-native: expose `open -> current -> best entry anchor`, separate number move from price move, and keep DraftKings as the primary book-specific truth for those lifecycle fields.
- Board rows should keep the current DraftKings line strip visible before selected-game research opens. Keep slate-level health text compact, order line buttons before generic research actions on mobile, and show compact number/price/best-entry cues on line buttons when available.
- Expanded board rows should start with a compact local-only `Market Pulse`: freshness, strongest move, split pressure, and evidence/readiness. Keep this text-native and cheap; do not fetch research payloads for every game just to fill it.
- Treat the line itself as the primary interaction in the board. Clicking a line should open a focused explanation for that exact market/side, with visual market history above the generic game tabs; pinning/ledger behavior is secondary.
- Focused line views should begin with a compact market-profile strip before deeper tables. Show sample size, recent range, median, current vs median, percentile, and the top supporting `why_this_line` factors when the data exists.
- For MLB, focused line views should also include compact reasoning blocks before long tables: team market profile, starter plus bullpen pressure, and run environment. Keep them visible and bettor-readable instead of burying them in tabs.
- Research API: `api.main:event_research` returns expanded per-game context for one detail panel; `api.main:event_research_batch` returns multiple expanded payloads for slip/detail refreshes.
- Research API now returns `line_evidence_status` rows for focused markets. These rows classify each line as `research_only`, `thin_validated`, or `validated_sample` using current-line availability, MLB market readiness, settled sample size, posted-line sample size, and strict OOF coverage.
- `line_evidence_status` rows are composed in `api/services/line_evidence.py` and include stable participant-aware `focus_key` values. Keep focused-line evidence rules in that service instead of expanding `api/main.py`.
- Research API now also returns `line_thesis` rows for focused markets. These rows use the same `focus_key` and compose the premium clicked-line readout from local-only context: current line, movement, history, line/evidence quality, supports, cautions, risk, and next step. Keep thesis rules in `api/services/line_thesis.py`.
- Game research intelligence should explain why a line exists before model settlement: market movement, public split context, team trends, starter/player context, environment context, and explicit data gaps. For MLB, populate team/starter/player trend fields from local MLB Stats API tables; populate weather/wind from local MLB environment snapshots when collected; translate NWS compass wind into field-relative wind only when manually reviewed venue orientation metadata exists; populate park factors only from reviewed CSV imports with source/season/rolling-window lineage.
- The first bettor-readable synthesis layer for game research is `why_this_line` in `GET /events/{event_id}/research`. Keep it above raw tables and make it explain the number before any model claim. For MLB, the current supported factors are market pressure, probable starter edge, team form, and run environment.
- Keep MLB research stats-first. `GET /events/{event_id}/research` now also carries a typed `matchup_snapshot`, `bullpen_usage`, richer `team_trends`, starter workload/rest, recent starter logs, recent team-vs-market history, and recent player averages from local boxscores. Present this as descriptive matchup context, not an implied pick or lineup confirmation.
- On the board, MLB research should help the user understand the number quickly: the Overview tab should summarize the game total case, team-total case, and side-market case before dumping long supporting tables.
- Prefer visual comparisons when they help explain the number quickly: market totals vs recent production, team totals vs recent scoring/opponent prevention, and player average vs current line are good descriptive charts as long as they stay clearly separate from validated edge.
- When the board shows player-line visuals, prefer same-market recent results over generic season stats. Game-by-game results against today’s current line are more useful than one blended average alone.
- Event-specific team totals and player props should follow the same sportsbook-native story as the main board when history exists: open, current, best entry, and whether movement came from the number, the price, or both.
- Expanded game panels should expose a compact Available Markets selector for core lines, team totals, and player props. Selecting a row should open the shared focused-line research suite for that exact participant-aware market.
- When the board summarizes team totals or player props, include recent results versus today’s current line as descriptive context. Show the recent O/U/P-style record and average margin-versus-line clearly, but do not present that as validated EV evidence.
- When prior event-specific market history exists, also show recent results versus each team or player's own recent posted lines. This is useful for explaining how the market has priced them lately, but it is still descriptive context rather than validated EV.
- When team totals or props show posted-line history, also surface how many settled posted-line samples actually support that view. Make thin sample sizes visible instead of letting a short record look stronger than it is.
- Keep event-specific MLB team totals and props reusable for later modeling: the research payload should carry settled market history rows with event/date, actual result, matched posted line, posted prices, margin, and O/U/P result instead of only flattened summary strings.
- Apply the same descriptive line-history idea to core markets too: game totals should show recent results versus current and posted totals, while moneyline sides should show recent close-price/implied-probability history and recent W-L at those prices.
- Product priority: prefer line-backed team/player averages, current-line hit/miss context, prior posted-line hit/miss context, and team-vs-market context before niche factors such as batting-order stability.
- Current short-term execution is tracked in `plans/three-sprint-odds-workstation-plan.md`: Sprint 1 line explainer UX, Sprint 2 MLB line reasoning depth, Sprint 3 historical market truth plus validated evidence.
- MLB now has a bounded current props provider path for research-grade player/team line comparisons, but it is not yet a historical props-EV pipeline.
- Keep MLB team-total evidence labeled as derived/current hybrid context until true sportsbook team-total history exists.
- Entry-EV API: `GET /analysis/entry-ev/latest` reports whether a strict OOF entry-EV artifact exists for the board.
- MLB readiness API: `GET /analysis/mlb/readiness` is a local-only diagnostic surface for MLB pregame odds, provider mapping, prior team logs, probable starters, prior starter logs, pending settlement, settled quoted events, and settled trainable events. It must not call provider APIs or spend odds quota.
- MLB market readiness API: `GET /analysis/mlb/market-readiness` is a local-only market-level evidence surface for MLB current lines, settled pregame history, strict OOF rows, participant linkage, and stats context. It must not call provider APIs or spend odds quota.
- MLB market readiness rows also carry local-only next-action guidance (`priority_score`, `next_action`, `next_action_label`, `next_action_command`, `next_action_reason`). Keep those commands bounded and quota-aware; the API/UI should explain the next step but not execute provider calls.
- MLB evidence growth snapshots are append-only local artifacts written by `python -m dk_ncaab mlb-evidence-growth-log` under `artifacts/evidence_growth/`. Run this after bounded collection/build/OOF cycles so the dashboard can show what actually improved. The snapshot should also surface unlinked event-specific quote counts and per-market growth deltas so readiness can distinguish sample growth from identity/linkage blockers.
- Props registry API: `GET /registry/props` returns the current supported event-specific markets for a sport.
- One-time MLB duplicate cleanup CLI: `python -m dk_ncaab reconcile-mlb-events` for dry-run planning and `python -m dk_ncaab reconcile-mlb-events --apply` for the local merge. This path must preserve append-only child lineage and only auto-merge groups without conflicting same-provider keys.
- CLI MLB stats collection: `python -m dk_ncaab collect-mlb-stats --start-date YYYY-MM-DD --end-date YYYY-MM-DD`; this does not use Odds API quota but should still be run in bounded date windows. Defaults cap boxscore fetches through `mlb_stats.max_boxscores_per_run` and delay requests through `mlb_stats.request_delay_sec`.
- CLI MLB current-season stats backfill: `python -m dk_ncaab backfill-mlb-current-season --start-date YYYY-MM-DD --end-date YYYY-MM-DD --window-days N`; this wraps MLB Stats API collection in restartable windows and skips existing boxscores by default.
- CLI MLB data inventory: `python -m dk_ncaab mlb-data-inventory`; writes `artifacts/inventory/mlb_data_inventory.json` with local date ranges, line-history counts, stats coverage, identity/statcast coverage, and missing joins.
- CLI MLB player ID import: `python -m dk_ncaab import-mlb-player-ids path/to/chadwick.csv`; imports Chadwick-style MLBAM/Retrosheet/Baseball Reference/FanGraphs crosswalk rows and links local MLBAM-backed players when possible. The importer also accepts the current Chadwick Register split `people-*.csv` directory layout.
- CLI MLB Statcast import: `python -m dk_ncaab import-mlb-statcast-daily path/to/statcast.csv`; aggregates Baseball Savant pitch rows into daily batter/pitcher features for starter and prop context.
- CLI MLB Statcast bounded backfill: `python -m dk_ncaab backfill-mlb-statcast-daily --start-date YYYY-MM-DD --end-date YYYY-MM-DD --window-days 1`; downloads traceable Baseball Savant CSV windows into `artifacts/raw/mlb/statcast` and imports them into daily feature rows.
- CLI event-specific odds collection: `python -m dk_ncaab collect-event-odds --sport baseball_mlb --max-events 1`; use this for manual, quota-gated team totals/player props collection before any automation.
- CLI event-specific odds identity reconciliation: `python -m dk_ncaab reconcile-event-odds-identities`; it dry-runs by default. Use `--apply` only to fill missing null participant IDs from source-backed local MLB data, never to alter odds values, timestamps, or raw payload lineage.
- CLI MLB daily research runbook: `python -m dk_ncaab mlb-daily-research-cycle`; this prints the bounded dashboard refresh sequence and skips event-specific Odds API commands unless `--include-event-odds` is supplied.
- CLI MLB environment collection: `python -m dk_ncaab collect-mlb-environment --max-events N`; this backfills venues from archived MLB schedule payloads, then makes bounded NWS no-key weather requests for upcoming MLB games. Keep it separate from odds collection and do not run broad weather fan-outs.
- CLI MLB venue metadata export/import: `python -m dk_ncaab export-mlb-venue-metadata-template temp/mlb_venue_metadata_template.csv` and `python -m dk_ncaab import-mlb-venue-metadata path/to/venue_metadata.csv`; use this for the small reviewed stadium orientation / roof / wind-reliability table.
- Venue-name drift is real (`Minute Maid Park` vs `Daikin Park`, `Guaranteed Rate Field` vs `Rate Field`, `Dodger Stadium` vs `UNIQLO Field at Dodger Stadium`). Keep alias handling explicit so live venue rows inherit reviewed orientation metadata.
- Treat 2026/current MLB venue names as canonical in the stadium layer: `Daikin Park`, `Rate Field`, `Sutter Health Park`, `loanDepot park`, `American Family Field`, `Oracle Park`, `T-Mobile Park`, `Rogers Centre`, `Truist Park`. Support same-venue historical renames as aliases, but do not alias different physical parks together.
- CLI MLB park-factor import: `python -m dk_ncaab import-mlb-park-factors path/to/park_factors.csv --source SOURCE --source-url URL`; this is quota-free and supports reviewed FanGraphs Guts park-factor exports, but should not become automated leaderboard scraping.
- Validation for event-specific odds and MLB research changes should include `tests/test_event_odds_markets.py`, `tests/test_mlb_readiness_api.py`, and the relevant registry/API tests before UI changes are treated as stable.

## Security
- Production secrets must come from Google Cloud Secret Manager (no `.env` in production).
- SSH model: prefer OS Login + IAM-based SSH long term, but current working admin path is Tailscale SSH to `odds-vm`. No password SSH or repo-managed keys.
- Keep DB/API exposure minimal; no public anonymous API.
- Network model: Tailscale-only private access; no public ingress for API/UI.
- Treat `artifacts/` as sensitive operational data; do not expose full raw payloads publicly.
- Alerts route to `nonemakerc05@gmail.com` for collector failure, no data >15m, disk >80%, backup/sync failure, and unexpected restart.
