# Modeling And Backtests

Last reviewed: 2026-04-22

## Dataset Shape

Feature rows are built per `(event_id, market, side)`. A full game can produce six rows:

- moneyline home and away
- spread home and away
- total over and under

MLB rows can now include provider-backed rolling team/player context from local
MLB Stats API game logs. The current fields include recent team win/run form,
bullpen outs, rest days, and starting-pitcher recent form. These are computed
from games before the earliest available entry snapshot time, falling back to
game start only when no pregame odds snapshot exists.

The 2026-04-22 local MLB parquet (`features_20260423.parquet`) has 1116 rows
and 126 columns. Team form columns are mostly populated; starting-pitcher
ERA/WHIP columns are partially populated after a capped 50-boxscore historical
fill. `oof-entry-ev --anchor T60 --sport baseball_mlb` correctly refuses this
dataset because there are still no modelable settled rows with entry prices.

Current parquet artifacts include dated feature exports and a trainable export. One explorer found `features_trainable.parquet` at about `342 x 89`. Verify current artifacts before relying on that size.

## Implemented Analysis

- `dataset_build.py`: exports finished-game feature rows to parquet.
- `models_close_predict.py`: Ridge, LightGBM, quantile LightGBM, temporal CV, SHAP.
- `models_outcome.py`: logistic regression, LightGBM classifier, residual/mispricing detection.
- `backtest.py`: blind baselines, fade-public baselines, model CLV filter, CLV/ROI/drawdown/Sharpe-like metrics.
- `settlement.py`: American-odds profit, break-even, and EV helpers for one-unit entry bets.
- `entry_ev.py`: entry-safe feature selection, threshold calibration on prior OOF predictions, walk-forward model-CLV aggregation, settlement breakdowns, and close-proxy OOF artifact builders.
- `oof_entry_ev.py`: strict entry-EV artifact generation from event-grouped OOF outcome probabilities and American entry prices.
- `mlb_stats.py` plus feature enrichment in `etl/features.py`: MLB team/player trend foundation from provider-backed boxscores.
- `model_store.py`: expects saved models under `artifacts/models`, but no saved model artifacts were found in this investigation.

## EV, CLV, ROI

Product intent says EV at entry time is primary and CLV is secondary validation. Current code is more CLV-heavy:

- CLV is central in backtest strategy naming and reports.
- Backtest payout now uses `price_american_<anchor>` instead of implied probability.
- Spread/total settlement now uses anchor-specific outcomes (`spread_cover_T60`, `total_over_T30`, etc.) so moved lines do not borrow close-line W/L/P.
- Pushes settle at `0.0` units; void/missing rows are excluded instead of being treated as losses.
- `model_expected_value` and residual-style signals exist, and `detect_mispricings()` uses entry American odds when available, but calibrated outcome-probability EV is still pending.
- `entry_ev.walk_forward_model_clv()` can calibrate CLV thresholds on prior OOF rows with minimum-bet guards.
- `python -m dk_ncaab oof-entry-ev` is the stricter path for UI-promotable EV evidence. It fails when `price_american_<anchor>` is missing instead of inventing settlement math from implied probability.

## Main Statistical Risks

- `models_close_predict.temporal_cv_splits()` now keeps all rows for an `event_id` in the same fold and preserves original indexes.
- `fit_predict_oof_ridge()` can produce aligned out-of-fold close-implied predictions. Other model paths still need the same OOF contract.
- Full-data refit models used against historical rows remain optimistic unless predictions are out-of-fold.
- Outcome models include close-aware features such as `implied_CLOSE` and open-to-close movement. Keep those out of live entry-time scoring unless scoring at close.
- Entry-time feature selection excludes close-aware, future-anchor, outcome, CLV, sport-unavailable, and full pre-tip volatility fields.
- MLB trend columns are allowed only for `baseball_mlb` when the sport registry has the `mlb_stats` enricher.
- ROI payout math has W/L/P/V grouped settlement reporting; production promotion should use strict OOF entry-EV artifacts, not full-data model predictions or stale parquet without prices.
- `detect_mispricings()` is a heuristic signal, not a final EV calculator.

## Validation Notes

Run `pytest tests/test_oof_entry_ev_artifacts.py tests/test_entry_ev.py tests/test_analysis_cv.py tests/test_backtest_settlement.py tests/test_feature_outcomes_settlement.py -v`
for the entry-EV foundation checks. Re-run full tests before editing shared features or backtests.
