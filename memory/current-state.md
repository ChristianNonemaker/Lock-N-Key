# Current State

Last reviewed: 2026-04-22

## Simple Version

This repo is a working early sports betting research platform. It already collects game schedules/results, betting lines, public split data, and some strength/ranking inputs. It stores the data, builds features, runs exploratory models/backtests, exposes a read-only FastAPI API, and shows a Streamlit dashboard.

The project is not production-finished yet. The main remaining work is making the data pipeline reliable on a tiny private VM, tightening free-tier odds API usage, making model evaluation honest enough for real EV decisions, and polishing the dashboard so it is easy to use on desktop and mobile.

## What Exists

- ESPN loaders for schedules/results.
- The Odds API collector for DraftKings lines.
- Action Network public betting split scraper through Playwright.
- Manual CSV-style KenPom/AP import paths.
- SQLAlchemy schema for leagues, teams, events, odds, splits, raw payloads, results, KenPom, and AP rankings.
- Query-time OPEN/T60/T30/CLOSE snapshot logic.
- Feature generation, parquet exports, close/outcome models, and exploratory backtests.
- FastAPI read-only endpoints and Streamlit pages for games, teams, detail, model panel, backtest, and pipeline status.
- A sportsbook-style board now exists with `GET /board`, `GET /events/{event_id}/research`, batched `GET /events/research`, URL state, expandable game rows, and a persisted private Research Slip.
- A sport/provider registry now drives collector/API/UI sport eligibility from `dk_ncaab/config/sports.py`.
- ESPN schedule/result processing now has no-network tests for NCAAB, NCAAF, NFL, and MLB event creation, final updates, and malformed payload handling.
- Entry-time EV foundations now exist: American-price settlement, anchor-specific spread/total settlement, push-aware outcomes, event-grouped CV, OOF Ridge prediction helper, sport/anchor-aware feature selection, threshold calibration, and OOF artifact helpers.
- Strict entry-EV artifact generation now exists through `python -m dk_ncaab oof-entry-ev`; it requires `price_american_<anchor>` and refuses stale parquet that cannot support true EV math.
- VM/Tailscale/cron/backup planning and scripts.
- The current VM deployment is reachable through Tailscale Serve at `https://odds-vm.tail1282c7.ts.net`.
- Local development is now the preferred proving ground for odds/model/UI iteration before promoting to the VM.

## Not Finished

- Production foundation is now documented as SQLite + cron + systemd + Tailscale. Docker/Postgres/APScheduler paths remain legacy/dev alternatives.
- Odds API usage is now tracked in append-only `odds_api_usage` rows by sport, with cadence/max-sports/reserve gates before HTTP calls.
- VM cron now skips odds/splits by default to protect free quota and VM load until explicit env flags enable them.
- Multi-sport support exists through the registry for NCAAB, NCAAF, NFL, and MLB. NCAAB is still the only deeply enriched sport.
- NBA and soccer are planned registry placeholders, not active collection or UI sports.
- The deployed board is empty right now because the VM has 0 upcoming events and 0 odds quotes. Cron now succeeds by skipping odds/splits until those data sources are explicitly enabled.
- The local DB now has a first quota-gated MLB odds smoke: 24 upcoming MLB events, 136 DraftKings odds quotes, and 1 recorded Odds API request for `baseball_mlb` as of 2026-04-22. It still has 0 results, so strict EV artifacts cannot be trained from this local MLB sample yet.
- Backtest/model metrics are still exploratory unless they come from the new out-of-fold/walk-forward helpers and settlement reports. Existing local parquet is stale for strict EV because it lacks American entry price columns.
- UI is functional and now has board deep links/watchlist persistence, but still needs more real populated data and repeated screenshot review.
- Private access depends on perimeter controls; there is no app-level auth.

## Where You Left Off

The codebase is past scaffolding. The next best moves are:

1. Choose providers before schema work for player stats, injuries, props, or saved recommendations.
2. Let the local MLB odds sample settle through ESPN results, then rebuild fresh parquet with American price columns and run `python -m dk_ncaab oof-entry-ev` before showing model-driven edge in the UI.
3. Tighten statistical validation around calibrated outcome probabilities and sport/market/anchor thresholds.
4. Continue improving the Sportsbook Board with real player-stat providers and populated screenshot review from fixture or real private data.
