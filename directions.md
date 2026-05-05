# Lock-N-Key Private Sportsbook Research System

Last reviewed: 2026-05-05

## Objective

Build a private sports betting research workstation that helps identify positive
expected value at entry time. The system compares model-implied probability against
market-implied probability at a known decision timestamp and validates that process
with settlement-aware ROI.

CLV is useful signal validation. It is not the primary success condition.

## Deployment Profile

- Private single-user dashboard.
- Production runtime: SQLite at `artifacts/dk_ncaab.sqlite3`, cron one-shot collectors,
  systemd API/UI services on `127.0.0.1`, and Tailscale Serve for remote access.
- No public ingress. FastAPI docs are disabled by default.
- Docker/Postgres/APScheduler remain local or legacy alternatives.

## Sports

- Deployment scope: NCAAB, NCAAF, NFL, MLB.
- MLB is the current reference sport because it has schedule/results, odds, team/player
  logs, Statcast imports, event-specific markets, environment context, and strict OOF
  EV artifacts wired.
- NBA and Soccer/EPL are planned placeholders until provider mappings, quota policy,
  tests, and UI expectations are ready.

## Data Contracts

- Preserve append-only odds, event-specific odds, splits, raw payloads, and result
  lineage.
- Use strict pregame anchors: OPEN, T60, T30, and CLOSE must be before start time.
- Keep DraftKings book-specific odds as the decision spine.
- Keep raw provider payloads traceable.
- Treat event identity as a first-class contract. Avoid ESPN/Odds/MLB duplicate games.

## Evidence Policy

Promoted evidence must use:

- anchor-specific American prices,
- entry-safe features only,
- event-grouped or date-grouped out-of-fold predictions,
- settlement-aware W/L/P/V math,
- enough OOF rows, settled events, and posted-line samples to pass promotion gates.

Everything else is research context: movement, splits, recent results, team/player
form, starters, park/weather, and descriptive hit/miss records.

## Dashboard North Star

The first screen should behave like a private DraftKings-style slate:

- games sorted by time,
- current DK lines visible without opening details,
- line clicks open focused reasoning for that exact market/side/participant,
- diagnostics and evidence shelves stay secondary,
- research context and validated edge evidence remain visually separate.

## Current Build Priority

1. Stabilize local tests, docs, evidence labeling, and source control hygiene.
2. Promote local code/data to the VM only after the VM is reachable and backed up.
3. Grow MLB settled priced samples with bounded collection.
4. Use MLB as the reference implementation before broader sport-specific depth.
