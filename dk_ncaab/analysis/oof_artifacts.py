"""Generate and read out-of-fold entry-EV artifacts.

The functions here deliberately use only local DB/parquet data. They do not
call schedule, odds, splits, or stats providers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from dk_ncaab.analysis.dataset_build import build_dataset
from dk_ncaab.analysis.entry_ev import (
    build_oof_prediction_artifact,
    entry_feature_columns,
    walk_forward_model_clv,
)
from dk_ncaab.analysis.models_close_predict import fit_predict_oof_ridge
from dk_ncaab.config.settings import get_settings

log = logging.getLogger(__name__)

_ARTIFACT_DIR = Path("artifacts/analysis/oof")
_LATEST_SUMMARY = _ARTIFACT_DIR / "latest_entry_ev.json"


@dataclass(frozen=True)
class AnchorArtifactSummary:
    anchor: str
    rows_input: int
    rows_with_prediction: int
    feature_count: int
    artifact_path: str | None
    n_bets: int
    total_roi: float
    mean_clv: float
    settlement_by_sport_market: list[dict[str, object]]
    warnings: list[str]


@dataclass(frozen=True)
class OofArtifactSummary:
    generated_at_utc: str
    source: str
    dataset_path: str | None
    rows: int
    events: int
    anchors: list[AnchorArtifactSummary]
    warnings: list[str]


def latest_feature_parquet() -> Path | None:
    """Return the newest local feature parquet, preferring trainable exports."""
    parquet_dir = Path(get_settings().storage.parquet_dir)
    if not parquet_dir.exists():
        return None

    trainable = sorted(
        parquet_dir.glob("features_trainable*.parquet"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if trainable:
        return trainable[0]

    candidates = sorted(
        parquet_dir.glob("features_*.parquet"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_feature_frame(source: str = "auto") -> tuple[pd.DataFrame, str, Path | None]:
    """Load features from the DB or latest parquet without provider calls."""
    source = source.lower().strip()
    if source not in {"auto", "db", "latest-parquet"}:
        raise ValueError("source must be auto, db, or latest-parquet")

    if source in {"auto", "db"}:
        df = build_dataset()
        if not df.empty or source == "db":
            return df, "db", None

    path = latest_feature_parquet()
    if not path:
        return pd.DataFrame(), "latest-parquet", None
    return pd.read_parquet(path), "latest-parquet", path


def _prepare_frame(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    if "sport" not in frame.columns:
        frame["sport"] = "basketball_ncaab"
    if "league_key" not in frame.columns:
        frame["league_key"] = "ncaab"
    if "start_time_utc" in frame.columns:
        frame["start_time_utc"] = pd.to_datetime(frame["start_time_utc"], utc=True, errors="coerce")
    return frame


def _artifact_path(anchor: str, generated_at: datetime) -> Path:
    stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    return _ARTIFACT_DIR / f"entry_ev_oof_{anchor}_{stamp}.parquet"


def generate_oof_artifacts(
    source: str = "auto",
    anchors: Iterable[str] = ("T60", "T30"),
    min_train_size: int = 60,
    n_folds: int = 3,
    min_predictions: int = 20,
) -> OofArtifactSummary:
    """Generate OOF close-prediction artifacts from local historical features."""
    generated_at = datetime.now(timezone.utc)
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    df, resolved_source, dataset_path = load_feature_frame(source)
    df = _prepare_frame(df)

    warnings: list[str] = []
    if df.empty:
        warnings.append("No local feature rows found. Build dataset after collecting settled odds.")
        summary = OofArtifactSummary(
            generated_at_utc=generated_at.isoformat(),
            source=resolved_source,
            dataset_path=str(dataset_path) if dataset_path else None,
            rows=0,
            events=0,
            anchors=[],
            warnings=warnings,
        )
        write_summary(summary)
        return summary

    events = int(df["event_id"].nunique()) if "event_id" in df.columns else 0
    anchor_summaries: list[AnchorArtifactSummary] = []
    for anchor in anchors:
        anchor = anchor.upper()
        anchor_warnings: list[str] = []
        features = entry_feature_columns(df, anchor=anchor, sport=None)
        required = [*features, "implied_CLOSE", f"implied_{anchor}"]
        missing_required = [col for col in required if col not in df.columns]
        if missing_required:
            anchor_warnings.append(f"Missing required columns: {', '.join(missing_required)}")
            anchor_summaries.append(
                AnchorArtifactSummary(
                    anchor=anchor,
                    rows_input=len(df),
                    rows_with_prediction=0,
                    feature_count=len(features),
                    artifact_path=None,
                    n_bets=0,
                    total_roi=0.0,
                    mean_clv=0.0,
                    settlement_by_sport_market=[],
                    warnings=anchor_warnings,
                )
            )
            continue

        model_frame = df.dropna(subset=required).copy()
        if len(model_frame) < min_predictions:
            anchor_warnings.append(
                f"Only {len(model_frame)} complete rows for {anchor}; need {min_predictions}+."
            )
            anchor_summaries.append(
                AnchorArtifactSummary(
                    anchor=anchor,
                    rows_input=len(model_frame),
                    rows_with_prediction=0,
                    feature_count=len(features),
                    artifact_path=None,
                    n_bets=0,
                    total_roi=0.0,
                    mean_clv=0.0,
                    settlement_by_sport_market=[],
                    warnings=anchor_warnings,
                )
            )
            continue

        preds = fit_predict_oof_ridge(
            model_frame,
            features=features,
            n_folds=n_folds,
            min_train_size=min_train_size,
        )
        rows_with_prediction = int(preds.notna().sum())
        if rows_with_prediction < min_predictions:
            anchor_warnings.append(
                f"Only {rows_with_prediction} OOF predictions for {anchor}; "
                f"need {min_predictions}+ before UI promotion."
            )

        if f"price_american_{anchor}" not in model_frame.columns:
            anchor_warnings.append(
                f"Missing price_american_{anchor}; settlement ROI withheld for this artifact."
            )

        run = walk_forward_model_clv(
            model_frame,
            preds,
            anchor=anchor,
            min_calibration_bets=10,
            n_folds=n_folds,
            min_train_size=min_train_size,
        )
        artifact = build_oof_prediction_artifact(model_frame, preds)
        artifact["anchor"] = anchor
        artifact["expected_clv"] = artifact["predicted_close"] - model_frame[f"implied_{anchor}"]
        path = _artifact_path(anchor, generated_at)
        artifact.to_parquet(path, index=False)

        anchor_summaries.append(
            AnchorArtifactSummary(
                anchor=anchor,
                rows_input=len(model_frame),
                rows_with_prediction=rows_with_prediction,
                feature_count=len(features),
                artifact_path=str(path),
                n_bets=run.result.n_bets,
                total_roi=run.result.total_roi,
                mean_clv=run.result.mean_clv,
                settlement_by_sport_market=list(run.settlement_by_sport_market),
                warnings=anchor_warnings,
            )
        )

    summary = OofArtifactSummary(
        generated_at_utc=generated_at.isoformat(),
        source=resolved_source,
        dataset_path=str(dataset_path) if dataset_path else None,
        rows=len(df),
        events=events,
        anchors=anchor_summaries,
        warnings=warnings,
    )
    write_summary(summary)
    return summary


def write_summary(summary: OofArtifactSummary) -> Path:
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    payload = asdict(summary)
    tmp_path = _LATEST_SUMMARY.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(_LATEST_SUMMARY)
    return _LATEST_SUMMARY


def read_latest_summary() -> dict | None:
    if not _LATEST_SUMMARY.exists():
        return None
    try:
        return json.loads(_LATEST_SUMMARY.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Could not read latest OOF artifact summary", exc_info=True)
        return None
