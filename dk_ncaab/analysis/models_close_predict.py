"""
Closing-line prediction models.

Modeling ladder:
  1. Ridge regression (baseline)
  2. LightGBM (gradient boosting)
  3. Quantile regression (prediction intervals)

All models use strict temporal cross-validation (expanding window).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_squared_error, r2_score, mean_absolute_error
from sklearn.preprocessing import StandardScaler

log = logging.getLogger(__name__)

TARGET = "implied_CLOSE"

# Default feature columns for closing-price prediction (§10.1)
DEFAULT_FEATURES = [
    # Odds anchors
    "implied_OPEN", "implied_T60", "implied_T30",
    # Movement (note: d_implied_OPEN_CLOSE excluded — it leaks CLOSE into target)
    "d_implied_OPEN_T60", "d_implied_T60_T30",
    # Velocity / acceleration
    "velocity_implied_OPEN_T60", "velocity_implied_T60_T30",
    "accel_implied",
    # Late steam (§6)
    "late_steam", "late_steam_direction",
    # Volatility
    "std_implied", "n_price_changes", "max_implied_drawdown",
    # Lines
    "line_OPEN", "line_T60", "line_T30",
    "d_line_OPEN_T60", "d_line_T60_T30",
    # KenPom deviation (§4)
    "adj_em_diff", "kenpom_expected_spread",
    "spread_dev_OPEN", "spread_dev_T60", "spread_dev_T30",
    # AP rankings (§5)
    "ap_rank_home", "ap_rank_away", "ap_rank_diff", "ranked_vs_unranked",
    # Splits (§7)
    "bets_pct_T60", "handle_pct_T60",
    "handle_minus_bets_T60",
    "sharp_money_proxy", "contrarian_intensity",
    # Interaction features (§7)
    "deviation_x_public_extreme", "movement_x_public_extreme", "hmb_x_deviation",
    # Context
    "hours_before_tip_at_OPEN",
]


# ── Result containers ───────────────────────────────────────────

@dataclass
class FoldResult:
    fold: int
    train_size: int
    test_size: int
    rmse: float
    mae: float
    r2: float


@dataclass
class ModelResult:
    name: str
    folds: list[FoldResult]
    feature_importances: dict[str, float] | None = None

    @property
    def mean_r2(self) -> float:
        return float(np.mean([f.r2 for f in self.folds]))

    @property
    def mean_rmse(self) -> float:
        return float(np.mean([f.rmse for f in self.folds]))

    def summary(self) -> str:
        return (
            f"{self.name}: R²={self.mean_r2:.4f}  "
            f"RMSE={self.mean_rmse:.6f}  "
            f"({len(self.folds)} folds)"
        )


# ── Temporal CV ─────────────────────────────────────────────────

def temporal_cv_splits(
    df: pd.DataFrame,
    date_col: str = "start_time_utc",
    n_folds: int = 3,
    min_train_size: int = 100,
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Expanding-window temporal splits.
    Returns list of (train_df, test_df) tuples, ordered by time.
    """
    df = df.sort_values(date_col).reset_index(drop=True)
    n = len(df)
    fold_size = (n - min_train_size) // n_folds

    if fold_size < 20:
        log.warning("Very small fold size (%d). Consider more data.", fold_size)

    splits = []
    for i in range(n_folds):
        test_start = min_train_size + i * fold_size
        test_end = test_start + fold_size if i < n_folds - 1 else n
        train = df.iloc[:test_start]
        test = df.iloc[test_start:test_end]
        splits.append((train, test))

    return splits


def _prepare_xy(
    df: pd.DataFrame,
    features: list[str],
    target: str = TARGET,
) -> tuple[pd.DataFrame, pd.Series]:
    """Drop rows with NaN in features or target, return X, y."""
    cols = [c for c in features if c in df.columns]
    sub = df[cols + [target]].dropna()
    return sub[cols], sub[target]


# ── Ridge baseline ──────────────────────────────────────────────

def train_ridge(
    df: pd.DataFrame,
    features: list[str] | None = None,
    n_folds: int = 3,
    alpha: float = 1.0,
) -> ModelResult:
    """
    Ridge regression with temporal CV.
    Features are standardized per fold (fit on train, transform test).
    """
    feats = features or DEFAULT_FEATURES
    splits = temporal_cv_splits(df, n_folds=n_folds)
    fold_results: list[FoldResult] = []

    for i, (train_df, test_df) in enumerate(splits):
        X_train, y_train = _prepare_xy(train_df, feats)
        X_test, y_test = _prepare_xy(test_df, feats)

        if len(X_train) < 20 or len(X_test) < 5:
            log.warning("Fold %d too small, skipping", i)
            continue

        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X_train)
        X_te = scaler.transform(X_test)

        model = Ridge(alpha=alpha)
        model.fit(X_tr, y_train)
        preds = model.predict(X_te)

        fold_results.append(FoldResult(
            fold=i,
            train_size=len(X_train),
            test_size=len(X_test),
            rmse=float(np.sqrt(mean_squared_error(y_test, preds))),
            mae=float(mean_absolute_error(y_test, preds)),
            r2=float(r2_score(y_test, preds)),
        ))

    # Feature importances from last fold's coefficients
    coefs = dict(zip(X_train.columns, model.coef_)) if fold_results else None

    result = ModelResult(name="Ridge", folds=fold_results, feature_importances=coefs)
    log.info(result.summary())
    return result


# ── LightGBM ───────────────────────────────────────────────────

def train_lightgbm(
    df: pd.DataFrame,
    features: list[str] | None = None,
    n_folds: int = 3,
    params: dict | None = None,
) -> ModelResult:
    """
    LightGBM regressor with temporal CV.
    No need for standardization; tree models are scale-invariant.
    """
    import lightgbm as lgb

    feats = features or DEFAULT_FEATURES
    default_params = {
        "objective": "regression",
        "metric": "rmse",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "min_child_samples": 20,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "verbose": -1,
        "n_estimators": 500,
        "early_stopping_rounds": 50,
    }
    lgb_params = {**default_params, **(params or {})}
    n_estimators = lgb_params.pop("n_estimators", 500)
    early_stopping = lgb_params.pop("early_stopping_rounds", 50)

    splits = temporal_cv_splits(df, n_folds=n_folds)
    fold_results: list[FoldResult] = []
    importances: dict[str, float] = {}

    for i, (train_df, test_df) in enumerate(splits):
        X_train, y_train = _prepare_xy(train_df, feats)
        X_test, y_test = _prepare_xy(test_df, feats)

        if len(X_train) < 50 or len(X_test) < 10:
            log.warning("Fold %d too small for LGBM, skipping", i)
            continue

        model = lgb.LGBMRegressor(n_estimators=n_estimators, **lgb_params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            callbacks=[lgb.early_stopping(early_stopping), lgb.log_evaluation(0)],
        )
        preds = model.predict(X_test)

        fold_results.append(FoldResult(
            fold=i,
            train_size=len(X_train),
            test_size=len(X_test),
            rmse=float(np.sqrt(mean_squared_error(y_test, preds))),
            mae=float(mean_absolute_error(y_test, preds)),
            r2=float(r2_score(y_test, preds)),
        ))

        # Accumulate importances
        for feat, imp in zip(X_train.columns, model.feature_importances_):
            importances[feat] = importances.get(feat, 0) + imp

    # Average importances across folds
    n = len(fold_results)
    if n > 0:
        importances = {k: v / n for k, v in importances.items()}

    result = ModelResult(name="LightGBM", folds=fold_results, feature_importances=importances)
    log.info(result.summary())
    return result


# ── Quantile regression ────────────────────────────────────────

def train_quantile_lgbm(
    df: pd.DataFrame,
    quantile: float = 0.5,
    features: list[str] | None = None,
    n_folds: int = 3,
) -> ModelResult:
    """
    LightGBM quantile regression.
    Use quantile=0.1/0.5/0.9 to get prediction intervals.
    """
    import lightgbm as lgb

    feats = features or DEFAULT_FEATURES
    params = {
        "objective": "quantile",
        "alpha": quantile,
        "metric": "quantile",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "verbose": -1,
    }
    splits = temporal_cv_splits(df, n_folds=n_folds)
    fold_results: list[FoldResult] = []

    for i, (train_df, test_df) in enumerate(splits):
        X_train, y_train = _prepare_xy(train_df, feats)
        X_test, y_test = _prepare_xy(test_df, feats)

        if len(X_train) < 50 or len(X_test) < 10:
            continue

        model = lgb.LGBMRegressor(n_estimators=300, **params)
        model.fit(X_train, y_train, eval_set=[(X_test, y_test)],
                  callbacks=[lgb.log_evaluation(0)])
        preds = model.predict(X_test)

        fold_results.append(FoldResult(
            fold=i,
            train_size=len(X_train),
            test_size=len(X_test),
            rmse=float(np.sqrt(mean_squared_error(y_test, preds))),
            mae=float(mean_absolute_error(y_test, preds)),
            r2=float(r2_score(y_test, preds)),
        ))

    name = f"Quantile-LGBM-q{quantile}"
    result = ModelResult(name=name, folds=fold_results)
    log.info(result.summary())
    return result


# Alias for backward compatibility (__main__.py uses this name)
train_lgbm = train_lightgbm


# ── SHAP analysis ───────────────────────────────────────────────

def shap_analysis(
    df: pd.DataFrame,
    features: list[str] | None = None,
    save_path: str | None = None,
) -> dict[str, float]:
    """
    Train a single LightGBM on the full dataset (for SHAP only —
    not for evaluation) and return mean |SHAP| per feature.
    """
    import lightgbm as lgb
    import shap

    feats = features or DEFAULT_FEATURES
    X, y = _prepare_xy(df, feats)
    if len(X) < 50:
        log.warning("Too few rows for SHAP analysis")
        return {}

    model = lgb.LGBMRegressor(n_estimators=300, verbose=-1)
    model.fit(X, y)

    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)
    mean_abs = np.abs(shap_values).mean(axis=0)
    importance = dict(sorted(zip(X.columns, mean_abs), key=lambda x: -x[1]))

    if save_path:
        shap.summary_plot(shap_values, X, show=False)
        import matplotlib.pyplot as plt
        plt.tight_layout()
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        plt.close()
        log.info("SHAP plot saved: %s", save_path)

    return importance
