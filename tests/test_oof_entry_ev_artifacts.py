from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from dk_ncaab.analysis.oof_entry_ev import add_entry_ev_targets, generate_oof_entry_ev

_TEST_OUT = Path("artifacts/test_outputs/oof_entry_ev")


def _case_dir(name: str) -> Path:
    path = _TEST_OUT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _feature_frame(n_events: int = 16) -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for event_id in range(n_events):
        home_win = 1 if event_id % 2 == 0 else 0
        rows.append(
            {
                "event_id": event_id,
                "start_time_utc": start + timedelta(days=event_id),
                "sport": "basketball_ncaab",
                "league_key": "ncaab",
                "market": "moneyline",
                "side": "home",
                "implied_OPEN": 0.48 + event_id * 0.001,
                "implied_T60": 0.50 + event_id * 0.001,
                "implied_T30": 0.51 + event_id * 0.001,
                "implied_CLOSE": 0.52 + event_id * 0.001,
                "price_american_T60": -110,
                "line_T60": None,
                "home_win": home_win,
                "away_win": 1 - home_win,
                "spread_cover_T60": None,
                "total_over_T60": None,
            }
        )
    return pd.DataFrame(rows)


def test_oof_entry_ev_requires_entry_american_price():
    case_dir = _case_dir("missing_price")
    df = _feature_frame().drop(columns=["price_american_T60"])
    path = case_dir / "features.parquet"
    df.to_parquet(path, index=False)

    with pytest.raises(ValueError, match="price_american_T60"):
        generate_oof_entry_ev(
            input_parquet=path,
            anchor="T60",
            out_dir=case_dir / "oof",
            min_train_size=4,
        )


def test_add_entry_ev_targets_uses_american_odds_and_pushes():
    df = pd.DataFrame(
        [
            {
                "event_id": 1,
                "start_time_utc": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "market": "spread",
                "side": "home",
                "implied_T60": 0.52,
                "line_T60": -3.0,
                "price_american_T60": -110,
                "spread_cover_T60": None,
            }
        ]
    )

    out = add_entry_ev_targets(df, "T60")

    assert out.loc[0, "settlement_status"] == "push"
    assert out.loc[0, "actual_profit_units_1u"] == 0.0
    assert out.loc[0, "break_even_prob"] == pytest.approx(110 / 210)


def test_generate_oof_entry_ev_writes_artifact_bundle():
    case_dir = _case_dir("artifact_bundle")
    df = _feature_frame()
    path = case_dir / "features.parquet"
    df.to_parquet(path, index=False)

    result = generate_oof_entry_ev(
        input_parquet=path,
        anchor="T60",
        out_dir=case_dir / "entry_ev",
        min_train_size=4,
        min_fold_train_rows=4,
        n_folds=3,
    )

    assert result.manifest_path.exists()
    assert result.predictions_path.exists()
    assert result.summary_path.exists()
    assert result.latest_path.exists()

    predictions = pd.read_parquet(result.predictions_path)
    assert not predictions.empty
    assert {
        "event_id",
        "anchor",
        "entry_price_american",
        "oof_win_prob",
        "break_even_prob",
        "model_ev_units",
        "actual_profit_units_1u",
    }.issubset(predictions.columns)
    assert predictions["anchor"].eq("T60").all()
    assert predictions["oof_win_prob"].between(0, 1).all()
