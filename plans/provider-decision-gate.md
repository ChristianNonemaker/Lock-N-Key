# Provider Decision Gate

Last reviewed: 2026-04-20

## Decision

Do not add database schema for players, injuries, player game logs, props, or
saved recommendations until provider choices and identifier contracts are known.

## Why

These tables will become long-lived lineage surfaces. Adding placeholders now
would force later migrations around unknown provider IDs, team/player identity,
prop market semantics, injury status vocabularies, void/push behavior, and
refresh cadence.

## Required Provider Matrix Before Schema

For each sport, choose and document:

- schedule source
- odds source and provider sport key
- results source
- public splits source
- team stats source
- player stats source
- injury source
- props source
- feature enrichers
- UI eligibility
- monthly/daily quota or scrape risk
- stable external IDs for teams, players, events, markets, and books
- allowed retention and raw-payload policy

The registry fields in `dk_ncaab/config/sports.py` are the current source of
truth. Unknown provider areas should remain `None` or disabled.

## Schema Acceptance Criteria

Add schema only when the chosen provider can answer:

- What is the durable player ID?
- How does the player map to team, sport, season, and provider?
- Are injury statuses normalized or provider-specific?
- Are props event-level, player-level, team-level, or market-level?
- How are pushes, voids, postponements, and stat corrections represented?
- What raw payload should be retained for audit?
- What is the quota/cadence budget?

## Current Action

Schema work is intentionally deferred. The next productive path is entry-time EV
modeling on the existing event/team/odds/result tables, while provider research
for player/injury/prop data happens separately.
