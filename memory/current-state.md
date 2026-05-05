# Current State

Last reviewed: 2026-05-05

## Summary

This repo is a working private sportsbook research workstation. It collects schedules,
results, DraftKings odds, selected public/sport context, builds feature rows, runs strict
entry-EV artifacts, exposes read-only FastAPI endpoints, and renders a Streamlit
Sportsbook Board.

Local is ahead of the VM. The VM was unreachable in the latest Tailscale check, so the
near-term path is local stabilization first, then VM promotion after backup and health
checks.

## Deployment Scope

- Deployment sports: NCAAB, NCAAF, NFL, MLB.
- MLB is the reference sport for deep research and evidence.
- NBA and Soccer/EPL are planned placeholders and disabled for UI/schedule/odds.
- Production profile: SQLite + cron + systemd + Tailscale Serve.
- Docker/Postgres/APScheduler are legacy/dev alternatives.

## What Works

- ESPN schedule/results loaders.
- The Odds API DraftKings core lines with append-only usage accounting.
- MLB event-specific odds for team totals, pitcher strikeouts, batter hits, and batter
  total bases.
- MLB Stats API team/player logs, probable starters, identities, Statcast daily imports,
  venue/environment, and reviewed park-factor import paths.
- Strict pregame OPEN/T60/T30/CLOSE snapshot policy.
- Strict `oof-entry-ev` artifact generation with event-grouped OOF predictions and
  American-price settlement math.
- Sportsbook Board with line-first rows, focused line views, slate intelligence, market
  readiness, evidence growth, and a private Research Slip / Ledger.

## Current Local Data

- MLB inventory: 358 events, 346 finals, 12 upcoming.
- DraftKings core quotes: 634.
- Event-specific quotes: 516 after one bounded post-stabilization pull.
- Settled DK pregame events: 74.
- Latest strict MLB T60 artifact: 652 OOF predictions, 242 flagged rows, about -1.8% ROI,
  `promotion_status=sample_sensitive`.
- Evidence is useful plumbing but not a betting feed; promotion gates should remain visible.

## Immediate Priorities

1. Keep tests and docs consistent with four-sport deployment scope.
2. Keep API docs disabled by default.
3. Grow thin MLB markets, especially `pitcher_strikeouts` and `team_totals`.
4. Resolve remaining unlinked event-specific player quotes.
5. Bring `odds-vm` online, back it up, then promote through Git after local validation.
