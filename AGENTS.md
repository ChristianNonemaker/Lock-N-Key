# DK Prediction Agent Instructions

## Critical Rules

- Preserve append-only betting data. Do not overwrite or delete historical odds, splits, raw payloads, or result lineage unless the user explicitly asks.
- Treat this repo as a private sports betting research website, not a public product. Keep API/UI private behind Tailscale or localhost boundaries.
- Avoid context flood. Before broad searching, read the relevant file in `memory/`, then use targeted `rg` searches.
- Use subagents only when the user explicitly asks for subagents or parallel agent work, or when the current runtime policy allows it. Prefer subagents for read-heavy exploration, test/log triage, screenshots, and independent review. Return distilled summaries, not raw logs.
- The storage decision is not fully settled. Code and compose still show Postgres paths, while some ops docs/scripts assume SQLite. Do not harden one path without calling out the conflict.
- The goal is positive expected value at entry time. CLV is useful validation, but it is not the primary success condition.

## Mission

Build a privately hosted, low-resource sports betting data website for NCAAB first, expanding only to NCAAF, NFL, and MLB when data contracts are ready. Prefer free sources and strict quotas. The dashboard should make betting lines, movement, public splits, model signals, data freshness, and backtests easy to inspect.

## Start Here

1. Read `memory/README.md`.
2. Read `.github/copilot-instructions.md`.
3. Read only the specific memory files relevant to the task.
4. Use `rg` or focused file reads to verify current code before editing.
5. If the task spans multiple subsystems and the user authorized subagents, split exploration by subsystem and keep the main thread focused on decisions.
6. After meaningful discoveries, update or create a concise memory file so future agents do not repeat the same search.
7. Update `.github/copilot-instructions.md` whenever architecture, workflow, deployment policy, UI navigation, data-source policy, or validation expectations change.

## Codebase Map

- `dk_ncaab/collectors/`: ESPN games/results, The Odds API lines, Action Network public splits, manual KenPom/AP imports.
- `dk_ncaab/etl/`: team normalization, OPEN/T60/T30/CLOSE snapshots, feature rows.
- `dk_ncaab/analysis/`: dataset export, close/outcome models, backtests, model persistence.
- `dk_ncaab/db/`: SQLAlchemy models, session, Alembic migration.
- `api/`: read-only FastAPI endpoints for the Streamlit UI.
- `ui/`: Streamlit dashboard pages.
- `scripts/`: VM, cron, deployment, diagnostics, and operational helpers.
- `plans/`: roadmap and cloud/VM orchestration plans.
- `.github/skills/sportsbook-ui-designer/`: repo-local UI subagent skill for dashboard design iteration.

## Sportsbook Board

- Main sportsbook-style page: `ui/pages/sportsbook_board.py`.
- Compact board endpoint: `GET /board`.
- Expanded game endpoint: `GET /events/{event_id}/research`.
- The Research Slip is session-state only and is for private review, not wager placement.
- Keep board payloads compact and freshness-aware. Do not make the UI call many detail endpoints per rerun.

## Context And Memory

Use `memory/` as progressive disclosure:

- `memory/current-state.md`: plain-English state of the repo.
- `memory/repo-map.md`: subsystem layout and common commands.
- `memory/data-pipeline-and-ops.md`: ingestion, free sources, quotas, deployment, secrets, storage.
- `memory/modeling-and-backtests.md`: dataset shape, models, EV/CLV/ROI, leakage risks.
- `memory/ui-and-api.md`: endpoint/page inventory, UX gaps, screenshot workflow.
- `memory/known-risks.md`: highest-priority risks and decisions to resolve.

When creating or updating memory files:

- Keep each file under about 120 lines.
- Prefer stable facts, file paths, commands, risks, and decisions.
- Do not paste raw command output, large schemas, or long logs.
- Include a `Last reviewed` date when the file reflects an investigation.
- If a fact may be stale, write how to verify it.

## Data Rules

- Keep raw inputs and normalized tables traceable.
- Keep snapshots strictly pre-tip for entry-time decisions. Exact-tip boundary behavior should be treated as suspicious until tested.
- Use event-grouped or date-grouped temporal validation for model evaluation. Row-level temporal splits can leak because each game creates multiple market/side rows.
- Do not present model-driven backtest metrics as production evidence unless predictions are out-of-fold or walk-forward.
- Separate live entry-time feature contracts from retrospective close-aware feature sets.
- EV should use real break-even and settlement math before any signal is promoted from exploratory to actionable.

## UI Rules

- Use the `sportsbook-ui-designer` skill for UI/UX tasks that touch visual hierarchy, controls, responsive behavior, empty states, or screenshot iteration.
- Keep Streamlit unless the user explicitly asks for a stack change.
- Optimize for fast private use on a small VM: cache expensive calls, avoid heavy rerun loops, and keep pages useful when artifacts are missing.
- Make primary flows obvious: choose sport/date/team/game, inspect lines and movement, review signal quality, check pipeline health.
- For substantial UI work, verify desktop and mobile screenshots when the app can run locally. Check populated and empty states.

## Ops And Security

- Preferred private model: single low-resource GCP Debian VM, Tailscale-only access, cron-driven collection, secrets from GCP Secret Manager.
- Collector must not depend on API/UI availability.
- Only one orchestration path should run at a time. Cron, Docker daemon, Windows Task Scheduler, and legacy APScheduler can collide.
- Do not expose Streamlit, FastAPI, Postgres, raw artifacts, screenshots, logs, or docs pages publicly.
- Treat `.env.cloud` as placeholder-only. Production secrets must not be committed.

## Verification

Use the smallest useful check first:

- Tests: `pytest tests/ -v`
- Targeted test examples: `pytest tests/test_normalize.py tests/test_snapshots.py tests/test_kenpom_ap.py -v`
- CLI smoke: `python -m dk_ncaab --help`
- API smoke: `uvicorn api.main:app --reload --port 8000`
- UI smoke: `streamlit run ui/app.py`

For operational changes, also smoke the relevant script in `scripts/` and report prerequisites such as `gcloud`, `gsutil`, Docker, Tailscale, or Playwright.

## Done Means

- The requested outcome is implemented or the investigation is clearly summarized.
- Relevant validation was run, or the reason it was not run is stated.
- Any unresolved storage, quota, privacy, leakage, or deployment risk is named plainly.
- Memory files are updated when the work produced reusable understanding.
