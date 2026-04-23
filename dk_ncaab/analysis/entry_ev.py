"""Entry-time EV evaluation helpers.

This module is the guardrail layer between exploratory close prediction and
bet-at-entry evaluation. It keeps feature selection anchor-aware, calibrates
thresholds on prior out-of-fold predictions, and reports settlement outcomes
with pushes and voids separated from wins/losses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from dk_ncaab.analysis.backtest import (
    BacktestResult,
    aggregate_bets,
    backtest_model_clv,
    settlement_breakdown,
)
from dk_ncaab.analysis.models_close_predict import temporal_cv_splits
from dk_ncaab.config.sports import feature_enrichers_for


IDENTITY_COLUMNS = {
    "event_id",
    "start_time_utc",
    "sport",
    "league_key",
    "market",
    "side",
}

OUTCOME_COLUMNS = {
    "home_win",
    "away_win",
    "spread_cover",
    "spread_cover_entry",
    "total_over",
    "model_expected_value",
}

ALWAYS_LEAKY_COLUMNS = {
    "implied_CLOSE",
    "line_CLOSE",
    "price_american_CLOSE",
    "fair_implied_CLOSE",
    "late_steam",
    "late_steam_direction",
    "d_implied_T30_CLOSE",
    "d_implied_OPEN_CLOSE",
    "d_line_T30_CLOSE",
    "d_line_OPEN_CLOSE",
    "d_fair_T30_CLOSE",
    "d_fair_OPEN_CLOSE",
    "velocity_implied_T30_CLOSE",
    "std_implied",
    "n_price_changes",
    "max_implied_drawdown",
}

NCAAB_ONLY_COLUMNS = {
    "home_adj_o",
    "home_adj_d",
    "home_adj_em",
    "home_tempo",
    "home_sos",
    "away_adj_o",
    "away_adj_d",
    "away_adj_em",
    "away_tempo",
    "away_sos",
    "adj_em_diff",
    "kenpom_expected_spread",
    "spread_dev_OPEN",
    "spread_dev_T60",
    "spread_dev_T30",
    "spread_dev_CLOSE",
    "ap_rank_home",
    "ap_rank_away",
    "ap_rank_diff",
    "ranked_vs_unranked",
}

SPLIT_COLUMNS = {
    "bets_pct_OPEN",
    "handle_pct_OPEN",
    "bets_pct_T60",
    "handle_pct_T60",
    "bets_pct_T30",
    "handle_pct_T30",
    "handle_minus_bets_OPEN",
    "handle_minus_bets_T60",
    "handle_minus_bets_T30",
    "d_handle_minus_bets_OPEN_T60",
    "d_handle_minus_bets_T60_T30",
    "sharp_money_proxy",
    "contrarian_intensity",
    "deviation_x_public_extreme",
    "movement_x_public_extreme",
    "hmb_x_deviation",
}

MLB_ONLY_COLUMNS = {
    "home_mlb_win_pct_l10",
    "away_mlb_win_pct_l10",
    "mlb_win_pct_delta_l10",
    "home_mlb_runs_for_l5",
    "away_mlb_runs_for_l5",
    "home_mlb_runs_allowed_l5",
    "away_mlb_runs_allowed_l5",
    "home_mlb_run_diff_l5",
    "away_mlb_run_diff_l5",
    "mlb_run_diff_delta_l5",
    "home_mlb_bullpen_outs_l3",
    "away_mlb_bullpen_outs_l3",
    "mlb_bullpen_outs_delta_l3",
    "home_mlb_rest_days",
    "away_mlb_rest_days",
    "home_mlb_starter_era_l3",
    "away_mlb_starter_era_l3",
    "home_mlb_starter_whip_l3",
    "away_mlb_starter_whip_l3",
    "home_mlb_starter_k_bb_l3",
    "away_mlb_starter_k_bb_l3",
    "home_mlb_starter_avg_ip_l3",
    "away_mlb_starter_avg_ip_l3",
}

ANCHOR_FUTURE_MARKERS = {
    "OPEN": ("T60", "T30"),
    "T60": ("T30",),
    "T30": (),
}

DEFAULT_CLV_THRESHOLDS = (0.0, 0.0025, 0.005, 0.01, 0.015, 0.02)


@dataclass(frozen=True)
class ThresholdCandidate:
    threshold: float
    n_bets: int
    roi: float
    mean_clv: float


@dataclass(frozen=True)
class ThresholdPolicy:
    anchor: str
    threshold: float
    min_bets: int
    fallback_used: bool
    candidates: tuple[ThresholdCandidate, ...]


@dataclass(frozen=True)
class EntryEvRun:
    anchor: str
    result: BacktestResult
    policies: tuple[ThresholdPolicy, ...]
    settlement_by_sport_market: tuple[dict[str, object], ...]


def infer_single_sport(df: pd.DataFrame) -> str | None:
    """Return the one non-null sport in a frame, otherwise None."""
    if "sport" not in df.columns:
        return None
    sports = [sport for sport in df["sport"].dropna().unique().tolist() if sport]
    return sports[0] if len(sports) == 1 else None


def is_entry_time_feature(column: str, anchor: str, sport: str | None = None) -> bool:
    """Return whether a column is safe to use at the requested entry anchor."""
    anchor = anchor.upper()
    if anchor not in ANCHOR_FUTURE_MARKERS:
        raise ValueError(f"Unsupported entry anchor={anchor}")

    if column in IDENTITY_COLUMNS or column in OUTCOME_COLUMNS:
        return False
    if (
        column.startswith("clv_")
        or column.startswith("spread_cover_")
        or column.startswith("total_over_")
        or "CLOSE" in column
        or column in ALWAYS_LEAKY_COLUMNS
    ):
        return False
    if any(marker in column for marker in ANCHOR_FUTURE_MARKERS[anchor]):
        return False

    enrichers = set(feature_enrichers_for(sport)) if sport else set()
    if column in NCAAB_ONLY_COLUMNS and not (
        {"kenpom", "ap_rankings"} & enrichers
    ):
        return False
    if column in SPLIT_COLUMNS and "action_network_splits" not in enrichers:
        return False
    if column in MLB_ONLY_COLUMNS and "mlb_stats" not in enrichers:
        return False

    return True


def entry_feature_columns(
    df: pd.DataFrame,
    anchor: str = "T60",
    sport: str | None = None,
) -> list[str]:
    """Return numeric, sport-aware, anchor-safe feature columns."""
    sport_key = sport or infer_single_sport(df)
    numeric_cols = [
        col for col in df.columns
        if pd.api.types.is_numeric_dtype(df[col])
    ]
    return [
        col for col in numeric_cols
        if is_entry_time_feature(col, anchor=anchor, sport=sport_key)
    ]


def _require_aligned_predictions(df: pd.DataFrame, predicted_close: pd.Series) -> None:
    if not predicted_close.index.equals(df.index):
        raise ValueError("predicted_close must be aligned one-to-one with df.index")


def calibrate_clv_threshold(
    df: pd.DataFrame,
    predicted_close: pd.Series,
    anchor: str = "T60",
    candidate_thresholds: Sequence[float] = DEFAULT_CLV_THRESHOLDS,
    min_bets: int = 20,
    fallback_threshold: float = 0.01,
) -> ThresholdPolicy:
    """Choose a CLV threshold using only the supplied calibration frame."""
    _require_aligned_predictions(df, predicted_close)
    candidates: list[ThresholdCandidate] = []

    valid_mask = predicted_close.notna()
    cal_df = df.loc[valid_mask]
    cal_pred = predicted_close.loc[valid_mask]

    for threshold in candidate_thresholds:
        result = backtest_model_clv(
            cal_df,
            cal_pred,
            anchor=anchor,
            clv_threshold=float(threshold),
        )
        candidates.append(
            ThresholdCandidate(
                threshold=float(threshold),
                n_bets=result.n_bets,
                roi=result.total_roi,
                mean_clv=result.mean_clv,
            )
        )

    eligible = [candidate for candidate in candidates if candidate.n_bets >= min_bets]
    if not eligible:
        return ThresholdPolicy(
            anchor=anchor,
            threshold=float(fallback_threshold),
            min_bets=min_bets,
            fallback_used=True,
            candidates=tuple(candidates),
        )

    best = max(
        eligible,
        key=lambda candidate: (candidate.roi, candidate.mean_clv, candidate.n_bets),
    )
    return ThresholdPolicy(
        anchor=anchor,
        threshold=best.threshold,
        min_bets=min_bets,
        fallback_used=False,
        candidates=tuple(candidates),
    )


def walk_forward_model_clv(
    df: pd.DataFrame,
    predicted_close: pd.Series,
    anchor: str = "T60",
    candidate_thresholds: Sequence[float] = DEFAULT_CLV_THRESHOLDS,
    min_calibration_bets: int = 20,
    fallback_threshold: float = 0.01,
    n_folds: int = 3,
    min_train_size: int = 100,
) -> EntryEvRun:
    """
    Apply a CLV model with thresholds calibrated on prior OOF predictions.

    The model predictions must already be out-of-fold and aligned to `df.index`.
    This function does not fit models and does not touch live APIs.
    """
    _require_aligned_predictions(df, predicted_close)
    all_bets = []
    policies: list[ThresholdPolicy] = []

    for train_df, test_df in temporal_cv_splits(
        df,
        n_folds=n_folds,
        min_train_size=min_train_size,
    ):
        train_pred = predicted_close.loc[train_df.index]
        policy = calibrate_clv_threshold(
            train_df,
            train_pred,
            anchor=anchor,
            candidate_thresholds=candidate_thresholds,
            min_bets=min_calibration_bets,
            fallback_threshold=fallback_threshold,
        )
        policies.append(policy)

        test_pred = predicted_close.loc[test_df.index]
        fold_result = backtest_model_clv(
            test_df,
            test_pred,
            anchor=anchor,
            clv_threshold=policy.threshold,
        )
        all_bets.extend(fold_result.bets)

    result = aggregate_bets(f"walk_forward_model_clv_{anchor}", all_bets)
    return EntryEvRun(
        anchor=anchor,
        result=result,
        policies=tuple(policies),
        settlement_by_sport_market=tuple(settlement_breakdown(result.bets)),
    )


def build_oof_prediction_artifact(
    df: pd.DataFrame,
    predicted_close: pd.Series,
    prediction_col: str = "predicted_close",
) -> pd.DataFrame:
    """Build a compact, UI-safe OOF prediction artifact frame."""
    _require_aligned_predictions(df, predicted_close)
    keep_cols = [
        "event_id",
        "start_time_utc",
        "sport",
        "league_key",
        "market",
        "side",
        "implied_OPEN",
        "implied_T60",
        "implied_T30",
        "implied_CLOSE",
        "price_american_OPEN",
        "price_american_T60",
        "price_american_T30",
    ]
    artifact = df[[col for col in keep_cols if col in df.columns]].copy()
    artifact.insert(0, "source_index", df.index.astype(str))
    artifact[prediction_col] = predicted_close
    return artifact


def save_oof_prediction_artifact(
    df: pd.DataFrame,
    predicted_close: pd.Series,
    path: str | Path,
    prediction_col: str = "predicted_close",
) -> Path:
    """Persist OOF predictions as parquet or CSV for private UI/API use."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = build_oof_prediction_artifact(df, predicted_close, prediction_col)
    if out_path.suffix.lower() == ".csv":
        artifact.to_csv(out_path, index=False)
    else:
        artifact.to_parquet(out_path, index=False)
    return out_path
