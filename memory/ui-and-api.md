# UI And API

Last reviewed: 2026-05-05

## API Inventory

Read-only FastAPI routes include:

- `/board`
- `/events/{event_id}/research`
- `/events/research`
- `/registry/props`
- `/analysis/entry-ev/latest`
- `/analysis/mlb/readiness`
- `/analysis/mlb/market-readiness`
- `/analysis/mlb/evidence-growth/latest`
- `/games`, `/teams`, `/standings`, team history, game detail, model, backtest, status,
  and runs routes.

FastAPI docs are disabled by default. Set `DKNCAAB_API__ENABLE_DOCS=true` for local
development only.

## UI Inventory

Streamlit pages:

- Sportsbook Board
- NCAAB View
- Game Browser
- Team History
- Game Detail
- Model Panel
- Backtest Dashboard
- Pipeline Status

The Sportsbook Board is the default workflow.

## Sportsbook Board Contract

- Default screen is a time-ordered DraftKings-style slate.
- Current line buttons stay visible on each game row.
- Clicking a line opens a focused line view for that market/side/participant.
- Research context and validated evidence stay separate.
- Board payloads must remain compact and freshness-aware.
- Expanded research loads on demand; do not fetch every detail payload per rerun.
- Research Slip is session-state only; Research Ledger appends pins/updates/removals to
  `artifacts/state/research_ledger.jsonl`.

## MLB Research Context

Focused MLB views can show:

- line thesis,
- evidence status and promotion gates,
- market profile,
- team market context,
- starter/bullpen pressure,
- run environment,
- recent results vs current lines,
- recent results vs posted lines,
- settled market history samples.

All descriptive hit/miss context remains research-only unless strict OOF evidence passes
promotion gates.

## UX Risks

- Mobile tables can overflow; prefer compact cards in focused-line summaries.
- Non-board pages still rely on session state more than deep links.
- Empty model/artifact states must stay honest.
- Diagnostics should remain collapsed below the betting workflow.

## Verification

```bash
python scripts/check_sportsbook_board_screenshots.py
ruff check ui/pages/sportsbook_board.py api/main.py api/schemas.py
```
