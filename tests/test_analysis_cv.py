from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from dk_ncaab.analysis.models_close_predict import fit_predict_oof_ridge, temporal_cv_splits
from dk_ncaab.analysis.models_outcome import _prepare_outcome_frame


def _grouped_frame(n_events: int = 12, rows_per_event: int = 6) -> pd.DataFrame:
    rows = []
    index = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for event_id in range(n_events):
        for row in range(rows_per_event):
            rows.append(
                {
                    "event_id": event_id,
                    "start_time_utc": start + timedelta(days=event_id),
                    "feature": float(row),
                    "implied_CLOSE": 0.5,
                }
            )
            index.append(1000 + event_id * 10 + row)
    return pd.DataFrame(rows, index=index)


def test_temporal_cv_splits_keep_all_event_rows_in_same_side_of_fold():
    df = _grouped_frame()
    splits = temporal_cv_splits(df, n_folds=3, min_train_size=24)

    assert splits
    for train, test in splits:
        assert set(train["event_id"]).isdisjoint(set(test["event_id"]))
        assert train["start_time_utc"].max() < test["start_time_utc"].min()


def test_temporal_cv_splits_preserve_original_index():
    df = _grouped_frame()
    splits = temporal_cv_splits(df, n_folds=3, min_train_size=24)

    original_index = set(df.index)
    for train, test in splits:
        assert set(train.index).issubset(original_index)
        assert set(test.index).issubset(original_index)
        assert not isinstance(train.index, pd.RangeIndex)
        assert not isinstance(test.index, pd.RangeIndex)


def test_oof_ridge_predictions_shape_and_index_contract():
    df = _grouped_frame(n_events=14, rows_per_event=6)
    df["feature"] = [float(i) for i in range(len(df))]
    df["implied_CLOSE"] = 0.4 + (df["feature"] * 0.001)

    preds = fit_predict_oof_ridge(
        df,
        features=["feature"],
        n_folds=3,
        min_train_size=24,
    )

    assert preds.index.equals(df.index)
    assert len(preds) == len(df)
    assert preds.isna().sum() > 0
    assert preds.notna().sum() > 0
    predicted_event_ids = set(df.loc[preds.notna(), "event_id"])
    warmup_event_ids = set(df.loc[preds.isna(), "event_id"])
    assert predicted_event_ids
    assert min(predicted_event_ids) > min(warmup_event_ids)


def test_outcome_training_frame_preserves_event_group_metadata():
    df = _grouped_frame(n_events=8, rows_per_event=2)
    df["target"] = [row % 2 for row in range(len(df))]

    sub, date_col, group_col = _prepare_outcome_frame(df, ["feature"], "target")
    splits = temporal_cv_splits(
        sub,
        date_col=date_col,
        group_col=group_col,
        n_folds=2,
        min_train_size=6,
    )

    assert "event_id" in sub.columns
    assert "start_time_utc" in sub.columns
    assert splits
    for train, test in splits:
        assert set(train["event_id"]).isdisjoint(set(test["event_id"]))
