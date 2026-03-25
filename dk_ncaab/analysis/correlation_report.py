"""
Correlation report – quantifies relationships between features and targets.

Outputs:
  1. Pairwise correlation matrix (Pearson + Spearman).
  2. OPEN → CLOSE R² baseline.
  3. Feature importance ranking for closing-line prediction.
  4. Splits ↔ movement cross-correlations.
  5. CLV ↔ outcome correlations.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt
import seaborn as sns

log = logging.getLogger(__name__)

# Feature groups for focused analysis
_MOVEMENT_FEATS = [
    "d_implied_OPEN_T60", "d_implied_T60_T30", "d_implied_T30_CLOSE",
    "d_implied_OPEN_CLOSE",
    "velocity_implied_OPEN_T60", "velocity_implied_T60_T30",
    "accel_implied",
    "late_steam", "late_steam_direction",
]
_SPLITS_FEATS = [
    "bets_pct_T60", "handle_pct_T60",
    "handle_minus_bets_T60",
    "d_handle_minus_bets_OPEN_T60",
    "sharp_money_proxy", "contrarian_intensity",
]
_KENPOM_FEATS = [
    "adj_em_diff", "kenpom_expected_spread",
    "spread_dev_OPEN", "spread_dev_T60", "spread_dev_T30", "spread_dev_CLOSE",
]
_AP_FEATS = [
    "ap_rank_home", "ap_rank_away", "ap_rank_diff", "ranked_vs_unranked",
]
_INTERACTION_FEATS = [
    "deviation_x_public_extreme", "movement_x_public_extreme", "hmb_x_deviation",
]
_VOL_FEATS = ["std_implied", "n_price_changes", "max_implied_drawdown"]
_TARGET = "implied_CLOSE"
_OUTCOME_COLS = ["home_win", "spread_cover", "spread_cover_entry", "total_over"]
_CLV_COLS = ["clv_OPEN", "clv_T60", "clv_T30", "clv_fair_OPEN", "clv_fair_T60", "clv_fair_T30"]


def _available(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Filter to columns that actually exist in the DataFrame."""
    return [c for c in cols if c in df.columns]


def open_close_r2(df: pd.DataFrame) -> float | None:
    """R² of implied_OPEN vs implied_CLOSE (baseline)."""
    sub = df[["implied_OPEN", _TARGET]].dropna()
    if len(sub) < 10:
        return None
    ss_res = ((sub["implied_OPEN"] - sub[_TARGET]) ** 2).sum()
    ss_tot = ((sub[_TARGET] - sub[_TARGET].mean()) ** 2).sum()
    return float(1 - ss_res / ss_tot) if ss_tot > 0 else None


def correlation_matrix(df: pd.DataFrame, method: str = "pearson") -> pd.DataFrame:
    """
    Full pairwise correlations for numeric columns.
    method: 'pearson' or 'spearman'.
    """
    numeric = df.select_dtypes(include=[np.number])
    return numeric.corr(method=method)


def feature_target_correlations(
    df: pd.DataFrame,
    target: str = _TARGET,
) -> pd.Series:
    """
    Correlation of every numeric feature with the target.
    Sorted by absolute magnitude.
    """
    numeric = df.select_dtypes(include=[np.number]).drop(columns=[target], errors="ignore")
    corrs = numeric.corrwith(df[target]).dropna()
    return corrs.reindex(corrs.abs().sort_values(ascending=False).index)


def kenpom_movement_cross(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-correlation: KenPom deviation features × movement features (§4 + §6)."""
    k_cols = _available(df, _KENPOM_FEATS)
    m_cols = _available(df, _MOVEMENT_FEATS)
    if not k_cols or not m_cols:
        return pd.DataFrame()
    return df[k_cols + m_cols].corr().loc[k_cols, m_cols]


def interaction_target_correlations(df: pd.DataFrame) -> pd.DataFrame:
    """Correlation of interaction features + AP features with target & outcomes."""
    feat_cols = _available(df, _INTERACTION_FEATS + _AP_FEATS)
    target_cols = _available(df, [_TARGET] + _OUTCOME_COLS)
    if not feat_cols or not target_cols:
        return pd.DataFrame()
    return df[feat_cols + target_cols].corr().loc[feat_cols, target_cols]


def splits_movement_cross(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-correlation table: splits features × movement features."""
    s_cols = _available(df, _SPLITS_FEATS)
    m_cols = _available(df, _MOVEMENT_FEATS)
    if not s_cols or not m_cols:
        return pd.DataFrame()
    return df[s_cols + m_cols].corr().loc[s_cols, m_cols]


def clv_outcome_correlations(df: pd.DataFrame) -> pd.DataFrame:
    """Correlation between CLV metrics and outcome labels."""
    c_cols = _available(df, _CLV_COLS)
    o_cols = _available(df, _OUTCOME_COLS)
    if not c_cols or not o_cols:
        return pd.DataFrame()
    return df[c_cols + o_cols].corr().loc[c_cols, o_cols]


# ── Visualization ───────────────────────────────────────────────

def plot_correlation_heatmap(
    corr: pd.DataFrame,
    title: str = "Correlation Matrix",
    save_path: str | None = None,
) -> None:
    """Save a heatmap of the correlation matrix."""
    fig, ax = plt.subplots(figsize=(14, 10))
    sns.heatmap(corr, annot=True, fmt=".2f", cmap="RdBu_r", center=0,
                vmin=-1, vmax=1, ax=ax, square=True)
    ax.set_title(title)
    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150)
        log.info("Saved heatmap: %s", save_path)
    plt.close(fig)


# ── Main report ─────────────────────────────────────────────────

def generate_report(df: pd.DataFrame, out_dir: str = "artifacts/reports") -> dict:
    """
    Generate the full correlation report.
    Returns a summary dict with key metrics.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    report: dict = {}

    # 1. OPEN → CLOSE R²
    r2 = open_close_r2(df)
    report["open_close_r2"] = r2
    log.info("OPEN → CLOSE R² = %s", f"{r2:.4f}" if r2 else "N/A")

    # 2. Feature → CLOSE correlations
    ft_corr = feature_target_correlations(df)
    report["top_features"] = ft_corr.head(15).to_dict()
    ft_corr.to_csv(f"{out_dir}/feature_close_correlations.csv")

    # 3. Splits × movement cross-correlations
    sm = splits_movement_cross(df)
    if not sm.empty:
        report["splits_movement_summary"] = sm.to_dict()
        plot_correlation_heatmap(
            sm, "Splits × Movement Correlations",
            f"{out_dir}/splits_movement_heatmap.png",
        )

    # 3b. KenPom deviation × movement (§4 + §6)
    km = kenpom_movement_cross(df)
    if not km.empty:
        report["kenpom_movement_summary"] = km.to_dict()
        plot_correlation_heatmap(
            km, "KenPom Deviation × Movement Correlations",
            f"{out_dir}/kenpom_movement_heatmap.png",
        )

    # 3c. Interaction + AP features vs targets (§5 + §7)
    it = interaction_target_correlations(df)
    if not it.empty:
        report["interaction_target_summary"] = it.to_dict()
        plot_correlation_heatmap(
            it, "Interaction & AP Features × Targets",
            f"{out_dir}/interaction_target_heatmap.png",
        )

    # 4. CLV × outcome
    co = clv_outcome_correlations(df)
    if not co.empty:
        report["clv_outcome_summary"] = co.to_dict()

    # 5. Full matrix heatmap (top features only)
    top_cols = list(ft_corr.head(20).index) + [_TARGET]
    top_cols = _available(df, top_cols)
    if len(top_cols) > 2:
        top_corr = df[top_cols].corr()
        plot_correlation_heatmap(
            top_corr, "Top Features Correlation Matrix",
            f"{out_dir}/top_features_heatmap.png",
        )

    log.info("Report saved to %s", out_dir)
    return report
