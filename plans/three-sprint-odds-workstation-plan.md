# Three-Sprint Odds Workstation Plan

Last reviewed: 2026-05-02

## Goal

Turn the current MLB-first board into a true private odds workstation:

1. a DraftKings-style, time-ordered slate as the default screen,
2. current DraftKings lines visible directly on each game row,
3. one-click transformation from a selected game or line into a research data suite,
4. deeper team/player context that explains why the market posted the number,
5. a separate Daily Betting Queue page/filter for prioritized review,
6. a separate validated-evidence shelf that only promotes strict entry-time results.

## Guardrails

- Keep `Sportsbook Board` as the main user workflow.
- Keep Daily Betting Queue as a secondary lens, not the default home screen.
- Keep DraftKings as the primary book-specific truth for line lifecycle fields.
- Keep research context and validated evidence visibly separate.
- Prefer local/fixture-backed development and bounded provider calls.
- Do not add new provider-backed schema without explicit identifier/cadence/retention decisions.

## Sprint 1: Focused Line Explainer

### Outcome

The default view should feel sportsbook-native first: games sorted by time to start, current lines visible without opening drawers, and a clicked line should answer that exact line before showing generic game research.

### Build

- Keep line click as the primary interaction.
- Keep slate sorting by start time and preserve current-line visibility on collapsed game rows.
- Add a compact focused-line header with:
  - `open -> best entry -> current`
  - number move
  - price move
  - market profile strip:
    - recent range
    - median recent posted number or price
    - current vs median
    - current percentile versus recent market history
    - sample size
- Show the top 2-3 supporting factors from `why_this_line` directly under the profile strip.
- Keep `Pin Selected Line` as a secondary action in the focused view.
- Preserve deep-link state through `focus_market` and `focus_side`.

### Acceptance

- A clicked total, side, or spread opens a focused line view with compact market profile context.
- The default board reads as a time-ordered sportsbook slate before any research drawer opens.
- Desktop and mobile screenshots remain readable with populated fixture data.
- No new provider calls are required for the UI layer.

## Sprint 2: MLB Line Reasoning Depth

### Outcome

The selected line view should explain why the number exists using team and player context, not just movement history.

### Build

- Add compact team-level market profile blocks:
  - recent implied team total range
  - recent opponent implied total range
  - recent scoring vs market expectation
  - recent prevention vs market expectation
- Deepen MLB selected-line context with:
  - starter form and workload tension versus market
  - bullpen fatigue and leverage usage
  - run-environment summary from weather plus reviewed venue context
  - recent team-vs-market summaries for totals, sides, and team totals
- Keep player views centered on:
  - recent average vs current line
  - recent results vs current line
  - recent results vs posted lines
  - matchup context

### Acceptance

- A user can open any MLB line and see a compact explanation of market placement without jumping tabs.
- Team/player visuals remain descriptive and clearly labeled as research context.
- No placeholder player/injury schema is added without explicit provider choice.

## Sprint 3: Historical Market Truth And Evidence Shelf

### Outcome

Make team totals and player props historically real enough to support future evidence, while keeping the board honest.

### Build

- Grow bounded MLB event-odds history for:
  - team totals
  - pitcher strikeouts
  - batter hits
  - batter total bases
- Add settlement-aware history joins for supported event-specific markets.
- Add recent hit/miss summaries against both today's current line and each side's/player's own recent posted lines.
- Rebuild feature parquet from the cleaned local DB on a controlled cadence.
- Re-run strict `oof-entry-ev` as settled priced sample grows.
- Keep evidence promotion rules explicit:
  - strict pregame anchor prices only
  - out-of-fold predictions only
  - settlement-aware ROI only
  - enough sample size before board promotion

### Acceptance

- The board can keep showing descriptive line context even when no validated evidence exists.
- Validated evidence remains a separate shelf and only appears for markets with real historical support.
- MLB remains the reference implementation before broader sport expansion.

## Execution Order After These Sprints

1. Finish MLB as the reference sport.
2. Use free historical baseball truth aggressively: MLB Stats API, Baseball Savant/Statcast, Retrosheet, Chadwick IDs, and reviewed park factors.
3. Treat historical betting-line truth as scarce: preserve our append-only DraftKings collection first, and only buy paid historical odds backfill where it changes the EV story.
4. Strengthen team/player data contracts behind the selected-line flow.
5. Expand to NCAAB using the same line-explainer and evidence split.

## Checks

- `python -m ruff check ui/pages/sportsbook_board.py api/main.py api/schemas.py`
- `pytest tests/ -v`
- `python scripts/check_sportsbook_board_screenshots.py`

## Notes

- OddsTrader and similar public sites can be used as manual references, not ingestion backbones.
- The product north star is still positive expected value at entry time, but the UX should first make line reasoning seamless and trustworthy.
- `memory/mlb-historical-data-sources.md` tracks the MLB data-source map and backfill order.
