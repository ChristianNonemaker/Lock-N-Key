# ESPN Schedule And Results

Last reviewed: 2026-04-20

## Summary

ESPN schedule/result reliability is guarded by no-network tests for the active
four sports: NCAAB, NCAAF, NFL, and MLB.

## Important Files

- `dk_ncaab/collectors/load_games.py`: ESPN schedule/result loading, event upsert,
  final score capture, status progression, active sport filtering.
- `dk_ncaab/config/sports.py`: ESPN scoreboard URL/params per sport.
- `tests/test_espn_load_games.py`: in-memory DB tests for ESPN mappings and
  event/result processing.

## Covered Behavior

- NCAAB uses legacy external keys like `espn:<id>`.
- NCAAF, NFL, and MLB use namespaced keys like `espn:<sport>:<id>`.
- ESPN params are pinned: NCAAB `groups=50`, NCAAF `groups=80`, NFL/MLB no group.
- Pre-game payloads create upcoming events and teams.
- Final payloads update event status and add one result row.
- Reprocessing the same final payload does not duplicate `event_results`.
- Malformed payloads return `(0, 0, 0)` and do not create events.
- `_sport_from_event()` can infer sport from league when an odds-created event
  does not have an ESPN-style external key.

## Still Pending

- Live ESPN payload shape tests from saved real samples are not checked in.
- NBA and soccer remain disabled planned registry entries. Verify their ESPN and
  odds mappings with fixtures before enabling collection or UI options.
- Team identity quality still depends on normalization/alias coverage.

## Verification

Run:

```bash
pytest tests/test_espn_load_games.py tests/test_sports_registry.py -v
pytest tests/ -v
```
