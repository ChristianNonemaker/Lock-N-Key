# Modeling And Backtests

Last reviewed: 2026-05-05

## Dataset Contract

Feature rows are built per `(event_id, market, side)` with optional participant identity
for MLB team totals and player props.

Core markets:

- moneyline home/away
- spread home/away
- total over/under

MLB event-specific markets:

- team totals
- pitcher strikeouts
- batter hits
- batter total bases

## Evidence Policy

The product goal is positive EV at entry time. CLV is validation only.

UI-promotable evidence must use:

- strict pregame anchor prices,
- entry-safe features,
- event-grouped OOF predictions,
- American-price settlement,
- push/void-aware outcomes,
- enough sample to pass promotion gates.

Current promotion fields:

- `promotion_status`
- `promotion_gaps`
- `min_oof_rows`
- `min_settled_events`
- `min_posted_line_samples`

## Current Local Artifact

Latest local strict MLB T60 artifact:

- input: `artifacts/parquet/features_20260505.parquet`
- rows input: 2,508
- rows modelable: 712
- rows predicted: 652
- modelable events: 71
- flagged rows: 242
- ROI: about -1.8%
- promotion: `sample_sensitive` with `non_positive_recommended_roi`

This is useful evidence plumbing, not a betting feed.

## Key Risks

- Thin market samples can look strong by accident.
- Optional Statcast/participant context must not become a hard row filter.
- Close-aware/future-anchor fields must stay out of entry-time scoring.
- Team totals and props need more settled posted-line history before promotion.

## Verification

```bash
pytest tests/test_oof_entry_ev_artifacts.py tests/test_entry_ev.py tests/test_analysis_cv.py -q
pytest tests/test_backtest_settlement.py tests/test_feature_outcomes_settlement.py -q
python -m dk_ncaab oof-entry-ev --sport baseball_mlb --anchor T60
```
