"""
Outcome prediction + mispricing detection (§10).

Modeling tasks:
  1. Outcome prediction: y = win/cover, X = CLOSE-adjusted + residual features.
  2. Mispricing detection: residual = market_price - model_price.
     Bet when |residual| exceeds statistically validated threshold.

All models use strict temporal cross-validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV

from dk_ncaab.analysis.settlement import expected_value_units
from dk_ncaab.analysis.models_close_predict import (
    temporal_cv_splits,
    FoldResult,
    ModelResult,
    TARGET,
)

log = logging.getLogger(__name__)

# Default outcome features: CLOSE-implied + residual features
OUTCOME_FEATURES = [
    # Closing market data (CLOSE-adjusted)
    "implied_CLOSE",
    "line_CLOSE",
    # Vig-removed fair probabilities (better for outcome correlation)
    "fair_implied_CLOSE",
    "d_fair_OPEN_CLOSE",
    "clv_fair_T60",
    "clv_fair_T30",
    # KenPom strength
    "adj_em_diff",
    "kenpom_expected_spread",
    "spread_dev_CLOSE",
    # Residual: how far CLOSE moved from model prediction (filled externally)
    # Movement dynamics (post-OPEN information)
    "d_implied_OPEN_CLOSE",
    "d_line_OPEN_CLOSE",
    # Volatility
    "std_implied",
    "n_price_changes",
    "max_implied_drawdown",
    # Late steam (final 30 min movement)
    "late_steam",
    "late_steam_direction",
    # AP rankings
    "ap_rank_diff",
    "ranked_vs_unranked",
    # Splits interactions
    "handle_minus_bets_T30",
    "sharp_money_proxy",
    "contrarian_intensity",
    # Interactions
    "deviation_x_public_extreme",
    "hmb_x_deviation",
]


def _prepare_outcome_frame(
    df: pd.DataFrame,
    cols: list[str],
    target_col: str,
) -> tuple[pd.DataFrame, str, str]:
    """Keep temporal/group metadata while filtering model-ready rows."""
    meta_cols = [
        col for col in ("event_id", "start_time_utc", "sport", "market", "side")
        if col in df.columns
    ]
    keep_cols = list(dict.fromkeys([*cols, target_col, *meta_cols]))
    sub = df[keep_cols].dropna(subset=[*cols, target_col]).copy()
    date_col = "start_time_utc"
    if date_col not in sub.columns:
        date_col = "_row_order"
        sub[date_col] = np.arange(len(sub))
    group_col = "event_id" if "event_id" in sub.columns else "_missing_event_id"
    return sub, date_col, group_col


@dataclass
class OutcomeFoldResult:
    """Metrics for one temporal CV fold of a classification model."""
    fold: int
    train_size: int
    test_size: int
    accuracy: float
    brier_score: float
    log_loss_val: float
    auc: float | None  # None if only one class in fold


@dataclass
class OutcomeModelResult:
    name: str
    target_col: str
    folds: list[OutcomeFoldResult]
    feature_importances: dict[str, float] | None = None

    @property
    def mean_accuracy(self) -> float:
        return float(np.mean([f.accuracy for f in self.folds]))

    @property
    def mean_brier(self) -> float:
        return float(np.mean([f.brier_score for f in self.folds]))

    @property
    def mean_auc(self) -> float | None:
        aucs = [f.auc for f in self.folds if f.auc is not None]
        return float(np.mean(aucs)) if aucs else None

    def summary(self) -> str:
        auc_str = f"{self.mean_auc:.4f}" if self.mean_auc else "N/A"
        return (
            f"{self.name} [{self.target_col}]: "
            f"Acc={self.mean_accuracy:.4f}  Brier={self.mean_brier:.4f}  "
            f"AUC={auc_str}  ({len(self.folds)} folds)"
        )


# ── Outcome prediction (§10.2) ─────────────────────────────────

def train_outcome_model(
    df: pd.DataFrame,
    target_col: str = "spread_cover",
    features: list[str] | None = None,
    n_folds: int = 3,
) -> OutcomeModelResult:
    """
    Logistic regression for outcome prediction (y = win/cover).

    Uses CLOSE-implied-adjusted features + residual features.
    Calibrated probabilities via CalibratedClassifierCV.
    """
    feats = features or OUTCOME_FEATURES
    cols = [c for c in feats if c in df.columns]
    sub, date_col, group_col = _prepare_outcome_frame(df, cols, target_col)

    if len(sub) < 50:
        log.warning("Too few rows for outcome model: %d", len(sub))
        return OutcomeModelResult(name="LogisticRegression", target_col=target_col, folds=[])

    splits = temporal_cv_splits(sub, date_col=date_col, group_col=group_col, n_folds=n_folds)
    fold_results: list[OutcomeFoldResult] = []

    for i, (train_df, test_df) in enumerate(splits):
        X_train = train_df[cols].dropna()
        y_train = train_df.loc[X_train.index, target_col]
        X_test = test_df[cols].dropna()
        y_test = test_df.loc[X_test.index, target_col]

        if len(X_train) < 30 or len(X_test) < 10:
            continue

        # Check for degenerate targets
        if len(y_train.unique()) < 2 or len(y_test.unique()) < 2:
            continue

        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X_train)
        X_te = scaler.transform(X_test)

        base_model = LogisticRegression(max_iter=1000, C=1.0)
        model = CalibratedClassifierCV(base_model, cv=3, method="isotonic")
        model.fit(X_tr, y_train)
        probs = model.predict_proba(X_te)[:, 1]
        preds = (probs > 0.5).astype(int)

        try:
            auc = float(roc_auc_score(y_test, probs))
        except ValueError:
            auc = None

        fold_results.append(OutcomeFoldResult(
            fold=i,
            train_size=len(X_train),
            test_size=len(X_test),
            accuracy=float(accuracy_score(y_test, preds)),
            brier_score=float(brier_score_loss(y_test, probs)),
            log_loss_val=float(log_loss(y_test, probs)),
            auc=auc,
        ))

    # Feature importances from last fold
    coefs = None
    if fold_results and hasattr(base_model, "coef_"):
        coefs = dict(zip(cols, base_model.coef_[0]))

    result = OutcomeModelResult(
        name="LogisticRegression",
        target_col=target_col,
        folds=fold_results,
        feature_importances=coefs,
    )
    log.info(result.summary())
    return result


def train_outcome_lgbm(
    df: pd.DataFrame,
    target_col: str = "spread_cover",
    features: list[str] | None = None,
    n_folds: int = 3,
) -> OutcomeModelResult:
    """
    LightGBM classifier for outcome prediction.
    Better at capturing non-linear interactions (e.g. deviation × public).
    """
    import lightgbm as lgb

    feats = features or OUTCOME_FEATURES
    cols = [c for c in feats if c in df.columns]
    sub, date_col, group_col = _prepare_outcome_frame(df, cols, target_col)

    if len(sub) < 100:
        log.warning("Too few rows for LGBM outcome model: %d", len(sub))
        return OutcomeModelResult(name="LightGBM-Classifier", target_col=target_col, folds=[])

    splits = temporal_cv_splits(sub, date_col=date_col, group_col=group_col, n_folds=n_folds)
    fold_results: list[OutcomeFoldResult] = []
    importances: dict[str, float] = {}

    for i, (train_df, test_df) in enumerate(splits):
        X_train = train_df[cols].dropna()
        y_train = train_df.loc[X_train.index, target_col]
        X_test = test_df[cols].dropna()
        y_test = test_df.loc[X_test.index, target_col]

        if len(X_train) < 50 or len(X_test) < 10:
            continue
        if len(y_train.unique()) < 2:
            continue

        model = lgb.LGBMClassifier(
            n_estimators=300,
            learning_rate=0.05,
            num_leaves=31,
            min_child_samples=20,
            subsample=0.8,
            colsample_bytree=0.8,
            verbose=-1,
        )
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[lgb.early_stopping(50), lgb.log_evaluation(0)],
        )
        probs = model.predict_proba(X_test)[:, 1]
        preds = (probs > 0.5).astype(int)

        try:
            auc = float(roc_auc_score(y_test, probs))
        except ValueError:
            auc = None

        fold_results.append(OutcomeFoldResult(
            fold=i,
            train_size=len(X_train),
            test_size=len(X_test),
            accuracy=float(accuracy_score(y_test, preds)),
            brier_score=float(brier_score_loss(y_test, probs)),
            log_loss_val=float(log_loss(y_test, probs)),
            auc=auc,
        ))

        for feat, imp in zip(cols, model.feature_importances_):
            importances[feat] = importances.get(feat, 0) + imp

    n = len(fold_results)
    if n > 0:
        importances = {k: v / n for k, v in importances.items()}

    result = OutcomeModelResult(
        name="LightGBM-Classifier",
        target_col=target_col,
        folds=fold_results,
        feature_importances=importances,
    )
    log.info(result.summary())
    return result


# ── Mispricing detection (§10.3) ────────────────────────────────

@dataclass
class MispricingSignal:
    """A detected mispricing for one (event, market, side)."""
    event_id: int
    market: str
    side: str
    market_implied: float         # current market price
    model_implied: float          # model-predicted fair price
    residual: float               # market - model (positive = overpriced by market)
    z_score: float                # how many std devs the residual is
    model_expected_value: float   # model_prob - break_even_prob


def detect_mispricings(
    df: pd.DataFrame,
    predicted_close: pd.Series,
    entry_anchor: str = "T60",
    z_threshold: float = 1.5,
) -> list[MispricingSignal]:
    """
    Identify rows where |residual| > z_threshold standard deviations.

    residual = market_price (at entry) - model_price (predicted close).

    A positive residual means the market is pricing this side HIGHER
    (more likely) than the model expects at close — potential fade.
    A negative residual means the market will move toward this side — potential bet.

    Args:
        df: Feature DataFrame with implied columns.
        predicted_close: Model's prediction of implied_CLOSE (aligned with df index).
        entry_anchor: Which anchor to use as "current market price" (T60, T30).
        z_threshold: Minimum |z-score| for a signal.

    Returns: list of MispricingSignal objects, sorted by |z_score| descending.
    """
    entry_col = f"implied_{entry_anchor}"
    if entry_col not in df.columns:
        log.warning("Entry column %s not in DataFrame", entry_col)
        return []

    # Compute residuals
    residuals = df[entry_col] - predicted_close
    residuals = residuals.dropna()

    if len(residuals) < 20:
        log.warning("Too few rows for mispricing detection: %d", len(residuals))
        return []

    # Z-score the residuals
    mu = residuals.mean()
    sigma = residuals.std()
    if sigma == 0:
        return []

    z_scores = (residuals - mu) / sigma

    signals: list[MispricingSignal] = []
    for idx in z_scores.index:
        z = z_scores[idx]
        if abs(z) < z_threshold:
            continue

        market_imp = df.loc[idx, entry_col]
        model_imp = predicted_close[idx]
        resid = market_imp - model_imp

        price_col = f"price_american_{entry_anchor}"
        price_american = df.loc[idx, price_col] if price_col in df.columns else None
        if price_american is not None and not pd.isna(price_american):
            model_ev = expected_value_units(float(model_imp), int(price_american))
        else:
            model_ev = model_imp - market_imp

        signals.append(MispricingSignal(
            event_id=int(df.loc[idx, "event_id"]),
            market=str(df.loc[idx, "market"]),
            side=str(df.loc[idx, "side"]),
            market_implied=float(market_imp),
            model_implied=float(model_imp),
            residual=float(resid),
            z_score=float(z),
            model_expected_value=float(model_ev),
        ))

    # Sort by |z_score| descending
    signals.sort(key=lambda s: abs(s.z_score), reverse=True)
    log.info("Detected %d mispricings (z > %.1f) from %d rows", len(signals), z_threshold, len(residuals))
    return signals
