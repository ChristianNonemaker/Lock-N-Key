# Entry-Time EV Modeling

Last reviewed: 2026-04-24

## Summary

Entry-time EV work has started with the required foundations:

- feature rows include `sport`, `league_key`, and American prices at OPEN/T60/T30/CLOSE
- spread and total pushes are treated as pushes instead of losses
- spread and total ROI settles against the entry anchor line, not the close line
- backtests use actual American entry prices for settlement
- push payouts count as `0.0` units
- unknown/void rows are excluded before settlement when possible
- temporal CV keeps all rows for an `event_id` in the same fold
- an out-of-fold Ridge helper returns predictions aligned to original indexes
- `entry_ev.py` provides sport/anchor-aware feature selection, prior-fold CLV threshold calibration, walk-forward model-CLV aggregation, W/L/P/V settlement breakdowns, and OOF artifact builders
- `oof_entry_ev.py` provides the stricter production-facing artifact path: event-grouped OOF logistic outcome probabilities, American entry odds EV, realized one-unit profit, recommendations, manifest, predictions parquet, and `latest.json`
- outcome-model frame preparation now preserves `event_id` and `start_time_utc` before temporal splitting

## Important Files

- `dk_ncaab/etl/features.py`: feature contract and settlement labels.
- `dk_ncaab/analysis/settlement.py`: American odds profit, break-even, EV helpers.
- `dk_ncaab/analysis/backtest.py`: settlement-aware bet records and model prediction alignment checks.
- `dk_ncaab/analysis/models_close_predict.py`: event-grouped temporal splits and OOF Ridge predictions.
- `dk_ncaab/analysis/models_outcome.py`: outcome frame prep preserves temporal/group metadata; mispricing EV uses entry American odds when available.
- `dk_ncaab/analysis/entry_ev.py`: entry-safe feature selection, threshold calibration, walk-forward OOF backtests, and artifact persistence.
- `dk_ncaab/analysis/oof_entry_ev.py`: strict OOF entry-EV artifact generation.
- `tests/test_backtest_settlement.py`
- `tests/test_feature_outcomes_settlement.py`
- `tests/test_analysis_cv.py`
- `tests/test_entry_ev.py`
- `tests/test_oof_entry_ev_artifacts.py`

## Current Local State

- A fresh local parquet now exists at `artifacts/parquet/features_20260425.parquet`.
- `python -m dk_ncaab oof-entry-ev --input-parquet artifacts/parquet/features_20260425.parquet --sport baseball_mlb --anchor T60` now succeeds locally.
- The first strict MLB artifact lives at `artifacts/entry_ev/oof/20260425T044212Z_T60_logreg`.
- Current summary:
  - `rows_input`: 1332
  - `rows_modelable`: 50
  - `events_modelable`: 13
  - `rows_predicted`: 26
  - `recommended_count`: 5
  - `recommended_roi`: `-18.6%`
- This is enough to validate the strict artifact path, but not enough data to treat MLB thresholds or ROI as stable.

## Still Pending

- Calibrated outcome probabilities by sport/market/anchor, not just close-implied proxy predictions.
- UI/API surfacing of artifact details beyond the latest summary.
- Anchor-truncated volatility features; current full pre-tip volatility is excluded from entry feature selection because it leaks future movement.

## Strict Entry-EV Command

Run after rebuilding a feature parquet from a populated DB:

```bash
python -m dk_ncaab oof-entry-ev --input-parquet artifacts/parquet/features_YYYYMMDD.parquet --sport baseball_mlb --anchor T60
```

The command intentionally fails if `price_american_<anchor>` is missing. Older
local parquet files currently lack those columns, so they can support close-proxy
research but not strict entry EV.

## Verification

Run:

```bash
pytest tests/test_oof_entry_ev_artifacts.py tests/test_entry_ev.py tests/test_analysis_cv.py tests/test_backtest_settlement.py tests/test_feature_outcomes_settlement.py -v
pytest tests/ -v
```
