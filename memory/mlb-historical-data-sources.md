# MLB Historical Data Sources

Last reviewed: 2026-05-02

## Summary

MLB has abundant historical baseball truth and scarce historical sportsbook-line truth. Build the dashboard around both realities:

- Use free/baseball-native sources to understand teams, players, starters, parks, weather context, and results.
- Treat DraftKings line history as a first-party asset collected append-only from today forward.
- Consider paid historical odds only for targeted backfills where it materially improves EV validation.

## Source Map

- MLB Stats API: already used locally for schedules, results, boxscores, probable starters, player logs, team logs, venues, and raw payload lineage. Best near-term source for current-season boxscore backfill.
- Baseball Savant / Statcast: pitch-level and batted-ball history, with CSV search and wrappers such as pybaseball. Best for pitcher K context, batter hits/total bases context, expected stats, barrels, hard-hit rate, whiff rate, arsenal, handedness, and venue/run-environment features.
- Retrosheet: long-run historical game logs, event/play-by-play files, rosters, ballpark codes, and processed CSV downloads. Best for durable historical baseball results and sanity checks, especially when MLB Stats API or Savant joins are thin.
- Chadwick Bureau Register/tools: player/person ID crosswalk across MLBAM, Retrosheet, Baseball Reference, and FanGraphs. Use before mixing Retrosheet, Savant, FanGraphs, and MLB Stats API rows.
- FanGraphs Guts park factors: reviewed manual CSV import source for park factors, including handedness splits when useful. Keep source URL, season, rolling window, and import lineage.
- The Odds API current odds/event odds: current DraftKings sportsbook truth for moneyline/spread/total, team totals, and selected player props. Preserve append-only rows.
- The Odds API historical odds: paid-only option. Featured market snapshots go back to 2020; additional/player markets are available from 2023. Use only as targeted backfill, because historical calls are quota-expensive.

## Recommended Backfill Order

1. Inventory local MLB data already present: events, results, `OddsQuote`, `EventOddsQuote`, raw payloads, `MlbTeamGameLog`, `MlbPlayerGameLog`, probable starters, environments, venues, and park factors. Implemented as `python -m dk_ncaab mlb-data-inventory`; the current artifact is `artifacts/inventory/mlb_data_inventory.json`.
2. Backfill current-season MLB Stats API game/team/player logs in bounded date windows. Implemented as `python -m dk_ncaab backfill-mlb-current-season`; the 2026-05-02 local pass filled 2026-04-08 through 2026-05-01 to zero final team/player log gaps.
3. Add a reviewed ID layer using Chadwick Register fields so player/team rows can survive source mixing. Implemented through `mlb_player_id_crosswalks` and `python -m dk_ncaab import-mlb-player-ids`; local import from the current split Register layout scanned 516,081 rows, upserted 128,742 crosswalk rows, and linked 965 local MLBAM players.
4. Add Statcast/Savant derived daily features in bounded chunks, stored as raw downloads plus normalized feature tables or parquet artifacts. Implemented through `mlb_statcast_daily`, `python -m dk_ncaab import-mlb-statcast-daily`, and `python -m dk_ncaab backfill-mlb-statcast-daily`; local bounded import currently covers 2026-04-08 through 2026-05-01 with 9,042 linked daily rows.
5. Add Retrosheet game-log/event-file ingestion only after the ID layer is stable; use it to broaden seasons and validate result/boxscore history.
6. Continue current/event-specific DraftKings odds collection going forward. This is the cheapest way to build proprietary line history.
7. Price a narrow paid historical-odds backfill only after the model asks for a specific missing line history, such as MLB game totals 2024-2026 or pitcher strikeouts from 2023 onward.

## Product Use

- Default board stays DraftKings-style: games sorted by time to start with current lines visible.
- Clicking a game or line opens a research suite with all markets, line movement, historical hit/miss context, team/player stats, environment, and EV evidence when strict artifacts exist.
- Daily Betting Queue is a secondary page/filter for prioritized review, not the default home.
- Hit/miss summaries against today's line and against prior posted lines are research context until strict out-of-fold EV proves edge.

## Known Risks

- Historical stats are not historical betting lines. Do not train line EV as if a retrospective Statcast/stat result knows what DraftKings offered at entry time.
- Historical odds backfill is paid and quota-expensive. Scope it by market/book/date before running anything broad.
- Player identity drift is the biggest join risk. The Chadwick Register crosswalk is now loaded locally, but Retrosheet/FanGraphs ingestion should still validate joins by source and date before broad source mixing.
- Pitcher and batter prop features need lineup/starter availability discipline. Do not use post-lineup or post-game facts as pregame features unless the anchor contract proves availability.

## Verification

- Local inventory: SQL counts by source/table and artifact file listing.
- Current-season stats backfill: `python -m dk_ncaab collect-mlb-stats --start-date YYYY-MM-DD --end-date YYYY-MM-DD`
- Bounded Statcast backfill: `python -m dk_ncaab backfill-mlb-statcast-daily --start-date YYYY-MM-DD --end-date YYYY-MM-DD --window-days 1`
- Feature rebuild: `python -m dk_ncaab build-dataset`
- Strict evidence: `python -m dk_ncaab oof-entry-ev --sport baseball_mlb ...`
