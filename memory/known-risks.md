# Known Risks

Last reviewed: 2026-04-22

## Highest Priority

1. Multiple orchestration paths can collide. Production is cron/systemd/Tailscale; do not start legacy Docker/APScheduler/Windows schedulers alongside it.
2. Private hosting is perimeter-only. CORS is now restricted by config, but there is still no app-level auth.
3. Model/backtest evaluation is exploratory unless it uses event-grouped walk-forward/out-of-fold predictions plus settlement-aware ROI.
4. SQLite is the production default, while Postgres compose remains as local/legacy. Keep code migration-friendly and avoid reintroducing storage split-brain.
5. NBA and soccer exist only as disabled registry placeholders; do not enable them without provider mapping tests and quota-reviewed cadence.

## Data Correctness

- Snapshot exact-tip boundary uses `<=` in places. Treat exact tip as ambiguous/leaky until tests clarify desired behavior.
- `splits_quotes` does not appear to have an odds-style dedup constraint, so repeated scrapes may duplicate rows.
- Event matching has had duplicate-event issues before. Treat team/time matching as a known weak point.
- DST-naive Eastern time conversion exists in scheduler logic that assumes UTC-5.

## Product Gaps

- No saved signal history of model-recommended entries. The Research Slip now persists a private watchlist, but it is not a recommendation ledger.
- No alerting for new edges or sharp movement.
- No app-level auth or role separation.
- Mobile board flow has URL state and a screenshot harness, but still needs repeated populated-device review.
- No fully free betting-lines feed is documented in this repo. ESPN is free for scores/schedules; The Odds API is quota-limited for lines.
- Sport/provider capability now has a registry, but it does not solve provider availability for player stats, injuries, props, or soccer league coverage.
- Player, injury, prop, and saved-recommendation schema is intentionally gated by `plans/provider-decision-gate.md`.

## When To Stop And Ask

- Before changing storage backend or backup policy.
- Before enabling public ingress or changing network exposure.
- Before increasing odds polling frequency.
- Before presenting historical model metrics as production-grade edge evidence.
- Before broad multi-sport assumptions that bypass team identity, market semantics, or source availability.
- Before adding sport-specific providers outside `dk_ncaab/config/sports.py`.
