# Sport Provider Registry

Last reviewed: 2026-04-20

## Summary

The source of truth for sport/provider capability is `dk_ncaab/config/sports.py`.
Do not add new hardcoded sport maps in collectors, API routes, or UI pages.

The registry distinguishes:

- app/UI eligibility
- ESPN schedule/results collection eligibility
- The Odds API collection eligibility
- default odds polling targets
- provider coverage for splits, team stats, player stats, injuries, props, and feature enrichers

## Current Matrix

- `basketball_ncaab`: UI-enabled, ESPN schedule/results-enabled, Odds API collectable, NCAAB-specific enrichers (`action_network_splits`, `kenpom`, `ap_rankings`).
- `americanfootball_ncaaf`: UI-enabled, ESPN schedule/results-enabled, Odds API collectable, generic odds snapshots only.
- `americanfootball_nfl`: UI-enabled, ESPN schedule/results-enabled, Odds API collectable, generic odds snapshots only.
- `baseball_mlb`: UI-enabled, ESPN schedule/results-enabled, Odds API collectable, MLB Stats API team/player boxscore provider enabled, feature enrichers `odds_snapshots` and `mlb_stats`.
- `basketball_nba`: planned placeholder, disabled for schedule, odds, and UI until provider tests land.
- `soccer_epl`: planned placeholder, disabled for schedule, odds, and UI until the ESPN/The Odds API league mapping is verified.

## Quota-Safe Defaults

Schedule defaults include NCAAB, NCAAF, NFL, and MLB because ESPN is free.
Odds defaults include only `baseball_mlb` until request accounting is fixed.
Config overrides still exist through `settings.yaml` and `DKNCAAB_` env vars, but
`active_sports()` validates overrides through the registry.

## Important Files

- `dk_ncaab/config/sports.py`: registry and helper functions.
- `dk_ncaab/config/settings.py`: registry-backed defaults and validation.
- `dk_ncaab/collectors/load_games.py`: ESPN schedule/results sport validation.
- `dk_ncaab/collectors/odds_api.py`: Odds API sport validation and league mapping.
- `api/main.py`: API sport validation uses UI-enabled registry entries.
- `ui/pages/*.py`: shared Streamlit sport choices come from `ui_sport_choices()`.
- `tests/test_sports_registry.py`: no-network registry/consumer coverage.

## Verification

Run:

```bash
pytest tests/test_sports_registry.py tests/test_mlb_stats_collector.py tests/test_mlb_features.py -v
pytest tests/ -v
```

The last local check on 2026-04-20 passed `54 passed, 1 skipped`. The skipped
test is the Bash SQLite backup/restore round trip on Windows, which still needs
VM or Git Bash verification.
