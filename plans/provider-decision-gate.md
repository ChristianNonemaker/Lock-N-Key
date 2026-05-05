# Provider Decision Gate

Last reviewed: 2026-05-05

## Decision

Only add provider-backed schema after the provider, identity, cadence, quota, and
retention contracts are known.

MLB now has approved schema for team/player logs, probable starters, identities,
Statcast daily features, venues/environment, park factors, and event-specific markets.
Those contracts are implemented and should remain append-only.

Other sports remain gated for richer providers:

- NCAAB: KenPom/AP/manual strength inputs exist; player, injury, and prop providers
  are not chosen.
- NCAAF/NFL: schedule/results and generic odds snapshots only.
- NBA/Soccer: planned placeholders, disabled for deployment.

## Required Matrix Before New Schema

For each new sport/provider surface, document:

- schedule source,
- odds source and provider sport key,
- results source,
- public splits source,
- team stats source,
- player stats source,
- injury/source availability,
- props source and settlement semantics,
- durable IDs for teams, players, events, markets, and books,
- quota/scrape risk,
- raw-payload retention policy,
- UI eligibility.

## Acceptance Criteria

New schema is allowed only when it can answer:

- What is the durable external ID?
- How does it map to local teams/events/players?
- Is the data available before the betting anchor?
- How are pushes, voids, postponements, and corrections represented?
- What raw payload is preserved?
- What bounded command or cadence collects it?

## Current Action

Finish MLB as the reference sport, then use the same contracts for NCAAB/NFL/NCAAF
expansion. Do not enable NBA or Soccer until provider tests and quota policy exist.
