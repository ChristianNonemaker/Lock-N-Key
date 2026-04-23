"""Out-of-fold entry-time EV artifact generation.

This is the production-facing modeling contract for betting edge evidence:
out-of-fold outcome probabilities, American entry odds, settlement math, and
event-grouped temporal validation. It uses local DB/parquet data only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from dk_ncaab.analysis.dataset_build import build_dataset
from dk_ncaab.analysis.entry_ev import entry_feature_columns
from dk_ncaab.analysis.models_close_predict import temporal_cv_splits
from dk_ncaab.analysis.settlement import (
    break_even_probability,
    expected_value_units,
    settle_profit_units,
)
from dk_ncaab.config.settings import get_settings

DEFAULT_OUT_DIR = Path("artifacts/entry_ev/oof")


@dataclass(frozen=True)
class OofEntryEvResult:
    run_dir: Path
    manifest_path: Path
    predictions_path: Path
    summary_path: Path
    latest_path: Path
    summary: dict


def _latest_feature_parquet() -> Path | None:
    parquet_dir = Path(get_settings().storage.parquet_dir)
    if not parquet_dir.exists():
        return None
    candidates = sorted(
        parquet_dir.glob("features_*.parquet"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_source_frame(
    input_parquet: str | Path | None = None,
    from_db: bool = False,
) -> tuple[pd.DataFrame, str | None]:
    """Load local feature rows from DB or parquet without collector calls."""
    if from_db:
        return build_dataset(), None
    if input_parquet:
        path = Path(input_parquet)
    else:
        path = _latest_feature_parquet()
        if path is None:
            return pd.DataFrame(), None
    return pd.read_parquet(path), str(path)


def target_column_for(row: pd.Series, anchor: str) -> str:
    market = row.get("market")
    side = row.get("side")
    if market == "moneyline":
        return "home_win" if side == "home" else "away_win"
    if market == "spread":
        return f"spread_cover_{anchor}"
    if market == "total":
        return f"total_over_{anchor}"
    raise ValueError(f"Unsupported market/side for EV target: {market}/{side}")


def add_entry_ev_targets(df: pd.DataFrame, anchor: str) -> pd.DataFrame:
    """Add target outcome, settlement, and EV helper columns."""
    anchor = anchor.upper()
    price_col = f"price_american_{anchor}"
    line_col = f"line_{anchor}"
    implied_col = f"implied_{anchor}"

    required = {"event_id", "start_time_utc", "market", "side", price_col, implied_col}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Missing required OOF EV columns: {', '.join(missing)}")

    frame = df.copy()
    if "sport" not in frame.columns:
        frame["sport"] = "basketball_ncaab"
    if "league_key" not in frame.columns:
        frame["league_key"] = "ncaab"
    frame["start_time_utc"] = pd.to_datetime(
        frame["start_time_utc"],
        utc=True,
        errors="coerce",
    )

    target_values: list[float | None] = []
    profit_values: list[float | None] = []
    settlement_values: list[str] = []
    break_even_values: list[float | None] = []

    for _, row in frame.iterrows():
        try:
            target_col = target_column_for(row, anchor)
        except ValueError:
            target_values.append(None)
            profit_values.append(None)
            settlement_values.append("void")
            break_even_values.append(None)
            continue

        target = row.get(target_col)
        price = row.get(price_col)
        if pd.isna(price):
            target_values.append(None)
            profit_values.append(None)
            settlement_values.append("void")
            break_even_values.append(None)
            continue

        target_outcome = None if pd.isna(target) else int(target)
        settled = settle_profit_units(int(price), target_outcome)
        target_values.append(target_outcome)
        profit_values.append(settled.profit_units)
        settlement_values.append(settled.status)
        break_even_values.append(break_even_probability(int(price)))

    frame["anchor"] = anchor
    frame["entry_implied"] = frame[implied_col]
    frame["entry_line"] = frame[line_col] if line_col in frame.columns else np.nan
    frame["entry_price_american"] = frame[price_col]
    frame["target_outcome"] = target_values
    frame["actual_profit_units_1u"] = profit_values
    frame["settlement_status"] = settlement_values
    frame["break_even_prob"] = break_even_values
    return frame


def _modelable_frame(df: pd.DataFrame, anchor: str, sport: str | None) -> tuple[pd.DataFrame, list[str]]:
    artifact_only = {
        "target_outcome",
        "actual_profit_units_1u",
        "break_even_prob",
        "edge_prob",
        "model_ev_units",
        "recommended",
        "ev_threshold_units",
        "oof_win_prob",
        "oof_fold",
    }
    features = [
        col for col in entry_feature_columns(df, anchor=anchor, sport=sport)
        if col not in artifact_only
    ]
    required = [
        *features,
        "target_outcome",
        "event_id",
        "start_time_utc",
        "entry_price_american",
        "break_even_prob",
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing model-ready columns: {', '.join(missing)}")
    modelable = df.dropna(subset=required).copy()
    modelable = modelable[modelable["settlement_status"].isin(["win", "loss"])]
    return modelable, features


def generate_oof_entry_ev(
    input_parquet: str | Path | None = None,
    from_db: bool = False,
    anchor: str = "T60",
    sport: str | None = "basketball_ncaab",
    out_dir: str | Path = DEFAULT_OUT_DIR,
    n_folds: int = 3,
    min_train_size: int = 60,
    min_fold_train_rows: int = 20,
    ev_threshold_units: float = 0.0,
) -> OofEntryEvResult:
    """Generate strict OOF entry-EV artifact files."""
    anchor = anchor.upper()
    raw_df, input_path = load_source_frame(input_parquet=input_parquet, from_db=from_db)
    if raw_df.empty:
        raise ValueError("No local feature rows available for OOF entry EV.")
    if sport and "sport" in raw_df.columns:
        raw_df = raw_df[raw_df["sport"].fillna(sport) == sport].copy()

    frame = add_entry_ev_targets(raw_df, anchor)
    modelable, features = _modelable_frame(frame, anchor, sport)
    if modelable.empty:
        raise ValueError(
            "No modelable rows after requiring entry prices, outcomes, and entry-safe features."
        )

    preds = pd.Series(np.nan, index=modelable.index, dtype=float)
    fold_ids = pd.Series(np.nan, index=modelable.index, dtype="float")
    splits = temporal_cv_splits(
        modelable,
        n_folds=n_folds,
        min_train_size=min_train_size,
    )

    for fold_id, (train_df, test_df) in enumerate(splits):
        X_train = train_df[features].dropna()
        y_train = train_df.loc[X_train.index, "target_outcome"].astype(int)
        X_test = test_df[features].dropna()
        if len(X_train) < min_fold_train_rows or len(X_test) < 1 or y_train.nunique() < 2:
            continue

        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X_train)
        X_te = scaler.transform(X_test)
        model = LogisticRegression(max_iter=1000, C=1.0)
        model.fit(X_tr, y_train)
        preds.loc[X_test.index] = model.predict_proba(X_te)[:, 1]
        fold_ids.loc[X_test.index] = fold_id

    predicted = modelable.loc[preds.notna()].copy()
    predicted["oof_fold"] = fold_ids.loc[predicted.index].astype(int)
    predicted["oof_win_prob"] = preds.loc[predicted.index]
    predicted["edge_prob"] = predicted["oof_win_prob"] - predicted["break_even_prob"]
    predicted["model_ev_units"] = [
        expected_value_units(prob, int(price))
        for prob, price in zip(
            predicted["oof_win_prob"],
            predicted["entry_price_american"],
            strict=True,
        )
    ]
    predicted["recommended"] = predicted["model_ev_units"] > ev_threshold_units
    predicted["ev_threshold_units"] = ev_threshold_units

    keep_cols = [
        "event_id",
        "start_time_utc",
        "sport",
        "league_key",
        "market",
        "side",
        "anchor",
        "entry_implied",
        "entry_line",
        "entry_price_american",
        "target_outcome",
        "settlement_status",
        "actual_profit_units_1u",
        "oof_fold",
        "oof_win_prob",
        "break_even_prob",
        "edge_prob",
        "model_ev_units",
        "recommended",
        "ev_threshold_units",
    ]
    predictions = predicted[[col for col in keep_cols if col in predicted.columns]].copy()
    predictions.insert(0, "source_index", predicted.index.astype(str))

    generated_at = datetime.now(timezone.utc)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(out_dir) / f"{stamp}_{anchor}_logreg"
    run_dir.mkdir(parents=True, exist_ok=True)

    predictions_path = run_dir / "predictions.parquet"
    manifest_path = run_dir / "manifest.json"
    summary_path = run_dir / "summary.json"
    feature_path = run_dir / "feature_columns.json"
    latest_path = Path(out_dir) / "latest.json"

    predictions.to_parquet(predictions_path, index=False)
    feature_path.write_text(json.dumps(features, indent=2), encoding="utf-8")

    recommended = predictions[predictions["recommended"]]
    settled = recommended[recommended["settlement_status"].isin(["win", "loss", "push"])]
    summary = {
        "generated_at_utc": generated_at.isoformat(),
        "input_path": input_path,
        "anchor": anchor,
        "sport": sport,
        "model": "logistic_regression",
        "rows_input": int(len(raw_df)),
        "rows_modelable": int(len(modelable)),
        "rows_predicted": int(len(predictions)),
        "events_modelable": int(modelable["event_id"].nunique()),
        "feature_count": len(features),
        "recommended_count": int(len(recommended)),
        "recommended_profit_units": float(settled["actual_profit_units_1u"].sum())
        if not settled.empty
        else 0.0,
        "recommended_roi": float(settled["actual_profit_units_1u"].sum() / len(settled))
        if not settled.empty
        else 0.0,
        "mean_model_ev_units": float(predictions["model_ev_units"].mean())
        if not predictions.empty
        else 0.0,
        "warnings": [],
    }
    if predictions.empty:
        summary["warnings"].append("No OOF predictions were produced.")
    if len(predictions) < 20:
        summary["warnings"].append("Fewer than 20 OOF predictions; do not promote in UI.")

    manifest = {
        **summary,
        "n_folds": n_folds,
        "min_train_size": min_train_size,
        "min_fold_train_rows": min_fold_train_rows,
        "ev_threshold_units": ev_threshold_units,
        "predictions_path": str(predictions_path),
        "feature_columns_path": str(feature_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    latest_tmp = latest_path.with_suffix(".tmp")
    latest_tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    latest_tmp.replace(latest_path)

    return OofEntryEvResult(
        run_dir=run_dir,
        manifest_path=manifest_path,
        predictions_path=predictions_path,
        summary_path=summary_path,
        latest_path=latest_path,
        summary=summary,
    )


def read_latest_entry_ev(out_dir: str | Path = DEFAULT_OUT_DIR) -> dict | None:
    latest = Path(out_dir) / "latest.json"
    if not latest.exists():
        return None
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return None
