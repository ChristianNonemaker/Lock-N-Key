from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from dk_ncaab.analysis.entry_ev import (
    build_oof_prediction_artifact,
    calibrate_clv_threshold,
    entry_feature_columns,
    walk_forward_model_clv,
)


def _moneyline_frame(n_events: int = 8) -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for event_id in range(n_events):
        rows.append(
            {
                "event_id": event_id,
                "start_time_utc": start + timedelta(days=event_id),
                "sport": "basketball_ncaab",
                "league_key": "ncaab",
                "market": "moneyline",
                "side": "home",
                "implied_T60": 0.50,
                "implied_CLOSE": 0.55,
                "price_american_T60": -110,
                "home_win": 1 if event_id % 2 == 0 else 0,
                "away_win": 0 if event_id % 2 == 0 else 1,
            }
        )
    return pd.DataFrame(rows, index=[100 + i for i in range(n_events)])


def test_entry_feature_columns_are_anchor_and_sport_aware():
    df = pd.DataFrame(
        [
            {
                "event_id": 1,
                "sport": "americanfootball_nfl",
                "implied_OPEN": 0.50,
                "implied_T60": 0.52,
                "implied_T30": 0.53,
                "implied_CLOSE": 0.54,
                "d_implied_OPEN_T60": 0.02,
                "d_implied_T60_T30": 0.01,
                "price_american_T60": -110,
                "line_T60": -3.0,
                "late_steam": 0.01,
                "std_implied": 0.02,
                "adj_em_diff": 5.0,
                "bets_pct_T60": 40.0,
            }
        ]
    )

    nfl_t60 = entry_feature_columns(df, anchor="T60", sport="americanfootball_nfl")
    ncaab_t60 = entry_feature_columns(df, anchor="T60", sport="basketball_ncaab")
    ncaab_open = entry_feature_columns(df, anchor="OPEN", sport="basketball_ncaab")

    assert "implied_OPEN" in nfl_t60
    assert "implied_T60" in nfl_t60
    assert "d_implied_OPEN_T60" in nfl_t60
    assert "price_american_T60" in nfl_t60
    assert "line_T60" in nfl_t60
    assert "implied_T30" not in nfl_t60
    assert "implied_CLOSE" not in nfl_t60
    assert "late_steam" not in nfl_t60
    assert "std_implied" not in nfl_t60
    assert "adj_em_diff" not in nfl_t60
    assert "bets_pct_T60" not in nfl_t60

    assert "adj_em_diff" in ncaab_t60
    assert "bets_pct_T60" in ncaab_t60
    assert "implied_T60" not in ncaab_open
    assert "d_implied_OPEN_T60" not in ncaab_open


def test_mlb_entry_features_are_sport_gated():
    df = pd.DataFrame(
        {
            "event_id": [1],
            "home_mlb_runs_for_l5": [5.0],
            "mlb_run_diff_delta_l5": [1.5],
            "home_adj_o": [110.0],
        }
    )

    mlb = entry_feature_columns(df, anchor="T60", sport="baseball_mlb")
    nfl = entry_feature_columns(df, anchor="T60", sport="americanfootball_nfl")

    assert "home_mlb_runs_for_l5" in mlb
    assert "mlb_run_diff_delta_l5" in mlb
    assert "home_mlb_runs_for_l5" not in nfl
    assert "mlb_run_diff_delta_l5" not in nfl
    assert "home_adj_o" not in mlb


def test_calibrate_clv_threshold_uses_roi_with_min_bet_guard():
    df = pd.DataFrame(
        [
            {
                "event_id": 1,
                "market": "moneyline",
                "side": "home",
                "implied_T60": 0.50,
                "implied_CLOSE": 0.55,
                "price_american_T60": -110,
                "home_win": 1,
            },
            {
                "event_id": 2,
                "market": "moneyline",
                "side": "home",
                "implied_T60": 0.50,
                "implied_CLOSE": 0.51,
                "price_american_T60": -110,
                "home_win": 0,
            },
        ],
        index=[10, 11],
    )
    predicted = pd.Series([0.56, 0.52], index=df.index)

    policy = calibrate_clv_threshold(
        df,
        predicted,
        anchor="T60",
        candidate_thresholds=(0.0, 0.05),
        min_bets=1,
    )

    assert policy.threshold == 0.05
    assert not policy.fallback_used


def test_walk_forward_model_clv_returns_oof_settlement_breakdown():
    df = _moneyline_frame()
    predicted = pd.Series([0.56] * len(df), index=df.index)

    run = walk_forward_model_clv(
        df,
        predicted,
        anchor="T60",
        candidate_thresholds=(0.0, 0.05),
        min_calibration_bets=1,
        n_folds=2,
        min_train_size=2,
    )

    assert run.policies
    assert run.result.n_bets > 0
    assert run.settlement_by_sport_market
    first_group = run.settlement_by_sport_market[0]
    assert first_group["sport"] == "basketball_ncaab"
    assert first_group["market"] == "moneyline"
    assert first_group["entry_anchor"] == "T60"


def test_build_oof_prediction_artifact_keeps_private_ui_contract():
    df = _moneyline_frame(n_events=2)
    predicted = pd.Series([0.56, float("nan")], index=df.index)

    artifact = build_oof_prediction_artifact(df, predicted)

    assert list(artifact["source_index"]) == ["100", "101"]
    assert list(artifact["event_id"]) == [0, 1]
    assert list(artifact["predicted_close"].notna()) == [True, False]
