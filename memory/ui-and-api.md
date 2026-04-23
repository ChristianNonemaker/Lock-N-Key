# UI And API

Last reviewed: 2026-04-23

## API Inventory

FastAPI exposes read-only routes:

- `GET /games`
- `GET /teams`
- `GET /standings`
- `GET /teams/{team_id}/history`
- `GET /game/{event_id}/summary`
- `GET /game/{event_id}/timeseries`
- `GET /game/{event_id}/features`
- `GET /game/{event_id}/model`
- `GET /backtest/summary`
- `GET /status`
- `GET /runs`
- `GET /board`
- `GET /events/{event_id}/research`
- `GET /events/research`
- `GET /analysis/entry-ev/latest`
- `GET /analysis/mlb/readiness`

FastAPI docs are not disabled. CORS is restricted through `settings.api.allowed_origins`.
Sport validation for board/games/teams/standings uses UI-enabled entries from
`dk_ncaab/config/sports.py`.

## UI Inventory

Streamlit pages:

- `Sportsbook Board`: sport selector, Live/Today/Upcoming board, expandable game rows, line buttons, and Research Slip.
- `NCAAB View`: default page with game/team tabs.
- `Game Browser`: date/team/status/sport filters and game selection.
- `Team History`: team search, history, comparisons.
- `Game Detail`: summary, line movement, splits, snapshots, market tabs.
- `Model Panel`: model signals when artifacts exist.
- `Backtest Dashboard`: strategy summary and charts.
- `Pipeline Status`: freshness, recent runs, operational commands.

Sport selector options come from `ui_sport_choices()` in the registry at render time. The
`NCAAB View` page is intentionally pinned to NCAAB.
Pipeline Status now displays Odds API monthly budget, recorded usage, remaining
requests, last request time, and requests by sport from `/status`.

## UX Gaps

- Product docs promise public imbalance, EV at key timestamps, confidence intervals, and richer backtest filters that are not fully implemented.
- Non-board pages still use `selected_event_id` session state rather than deep links.
- Several non-board endpoints/pages make repeated blocking calls on rerender.
- `/games`, `/standings`, and team history paths have N+1 query pressure.
- Model Panel can be empty if `artifacts/models` is missing.
- Pipeline Status can miss recent runs if `artifacts/state/runs.jsonl` is absent or not volume-shared.
- Mobile responsiveness is mostly Streamlit defaults plus light CSS.

## Sportsbook Board Notes

- `GET /board` returns compact game rows with current lines, markets, public split summaries, and freshness flags; board teams/quotes/splits are prefetched in batches for the visible events.
- `GET /events/{event_id}/research` returns the expanded per-game payload: lines, snapshots, feature rows, team metrics, and player-stat empty states.
- `GET /events/research?event_ids=1,2` returns batched expanded payloads for expanded rows or watchlist refreshes.
- `Sportsbook Board` supports URL state through `page`, `sport`, `mode`, `date`, and `event_id` query params.
- `Research Slip` persists as a private local watchlist at `artifacts/state/research_watchlist.json`. It is a review/watchlist surface, not wager placement.
- The board reads `/analysis/entry-ev/latest` and shows whether a strict OOF entry-EV artifact is available. It does not manufacture recommendations when no artifact exists.
- When MLB is selected, the board reads `/analysis/mlb/readiness` and shows local-only readiness diagnostics for pregame odds, provider mapping, prior team logs, probable starters, prior starter logs, pending settlement, and settled trainable events.
- Player stats are intentionally placeholder-only until a free/cheap provider is added and schema is designed.
- The board should avoid loading research payloads for every visible game; load expanded details on demand.
- Screenshot verification exists for the first UI pass:
  - `artifacts/screenshots/sportsbook-board-desktop.png`
  - `artifacts/screenshots/sportsbook-board-mobile.png`
- VM screenshot verification exists for the deployed Tailscale UI:
  - `artifacts/screenshots/sportsbook-board-vm-desktop.png`
  - `artifacts/screenshots/sportsbook-board-vm-mobile-collapsed.png`
- Populated fixture screenshot harness:
  - `python scripts/check_sportsbook_board_screenshots.py`
  - writes `artifacts/screenshots/sportsbook-board-populated-desktop.png`
  - writes `artifacts/screenshots/sportsbook-board-populated-mobile.png`
  - writes `artifacts/screenshots/sportsbook-board-mlb-readiness-desktop.png`
  - writes `artifacts/screenshots/sportsbook-board-mlb-readiness-mobile.png`
  - uses a localhost mock API and does not call ESPN, The Odds API, or Action Network.
- The first screenshots captured the empty-state path because the local SQLite database had not been migrated (`events` table missing).
- The VM screenshots also capture an empty board because the deployed API currently has no upcoming events and no odds quotes.
- `ui/app.py` should keep `initial_sidebar_state="collapsed"` so mobile opens directly to the board instead of the navigation drawer.

## UI Iteration Workflow

Use `.codex/skills/sportsbook-ui-designer/SKILL.md` for UI work. The short version:

1. Pick one page and one user task per pass.
2. Capture baseline desktop and mobile screenshots when possible.
3. Make a focused change.
4. Re-run and compare screenshots.
5. Test populated and empty states.
6. Check state handoffs through `selected_event_id`.
7. Keep performance acceptable for a small VM.

## Design North Star

The dashboard should answer:

- What games are available today or soon?
- Where are the DraftKings lines and how did they move?
- Are public splits, KenPom/AP, or model signals pointing to possible value?
- Is the data fresh enough to trust right now?
- What did the backtest say, and is it honest enough to matter?
