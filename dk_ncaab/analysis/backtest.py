"""
Backtesting framework.

Evaluates strategies using CLV (primary) and ROI (secondary).
All strategies are entry-time-specific: the bet price is taken from
the snapshot at the entry anchor (T-60 or T-30).

Baselines:
  - "blind_T60": bet every game at T-60, on the side that moved toward you.
  - "blind_T30": same, at T-30.

Model-driven:
  - "model_clv": bet when model-predicted close implies CLV > threshold.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dk_ncaab.analysis.settlement import settle_profit_units

log = logging.getLogger(__name__)


@dataclass
class BetRecord:
    event_id: int
    market: str
    side: str
    entry_anchor: str           # "T60" or "T30"
    entry_implied: float
    entry_price_american: int | None
    close_implied: float
    clv: float                  # close - entry (positive = good)
    outcome: int | None         # 1=win, 0=loss, None=push/unknown
    payout: float | None        # +1 unit or -1 unit (flat bet)
    settlement_status: str = "unknown"
    sport: str | None = None
    league_key: str | None = None


@dataclass
class BacktestResult:
    strategy: str
    n_bets: int
    mean_clv: float
    median_clv: float
    clv_positive_rate: float    # fraction of bets with positive CLV
    total_roi: float            # sum(payout) / n_bets
    win_rate: float | None
    max_drawdown: float         # worst peak-to-trough equity decline (§11)
    sharpe_ratio: float | None  # annualized Sharpe-like metric (§11)
    bets: list[BetRecord] = field(repr=False, default_factory=list)

    def summary(self) -> str:
        wr = f"{self.win_rate:.1%}" if self.win_rate is not None else "N/A"
        sr = f"{self.sharpe_ratio:.2f}" if self.sharpe_ratio is not None else "N/A"
        return (
            f"{self.strategy}: {self.n_bets} bets | "
            f"CLV={self.mean_clv:+.4f} (pos={self.clv_positive_rate:.1%}) | "
            f"ROI={self.total_roi:+.1%} | WR={wr} | "
            f"MaxDD={self.max_drawdown:+.1%} | Sharpe={sr}"
        )


# ── Strategy helpers ────────────────────────────────────────────

def _compute_payout(price_american: int | None, outcome: int | None) -> tuple[float | None, str]:
    """Flat-bet payout using the actual entry American price."""
    settled = settle_profit_units(price_american, outcome)
    return settled.profit_units, settled.status


def _build_bet(row: pd.Series, anchor: str) -> BetRecord | None:
    """Build a BetRecord from a feature row at the given anchor."""
    entry_col = f"implied_{anchor}"
    price_col = f"price_american_{anchor}"
    entry_imp = row.get(entry_col)
    price_american = row.get(price_col)
    close_imp = row.get("implied_CLOSE")

    if entry_imp is None or close_imp is None or np.isnan(entry_imp) or np.isnan(close_imp):
        return None
    if price_american is None or pd.isna(price_american):
        return None

    # Determine outcome based on market/side
    market = row.get("market", "")
    side = row.get("side", "")
    final_known = row.get("home_win") is not None and not pd.isna(row.get("home_win"))
    if market in {"spread", "total"}:
        entry_line = row.get(f"line_{anchor}")
        if entry_line is None or pd.isna(entry_line):
            return None
    if market == "moneyline":
        outcome = row.get("home_win") if side == "home" else row.get("away_win")
    elif market == "spread":
        outcome_col = f"spread_cover_{anchor}"
        if outcome_col in row.index:
            outcome = row.get(outcome_col)
        elif anchor == "OPEN" and "spread_cover_entry" in row.index:
            outcome = row.get("spread_cover_entry")
        elif anchor == "CLOSE" and "spread_cover" in row.index:
            outcome = row.get("spread_cover")
        else:
            return None
    elif market == "total":
        outcome_col = f"total_over_{anchor}"
        if outcome_col in row.index:
            outcome = row.get(outcome_col)
        elif anchor == "CLOSE" and "total_over" in row.index:
            outcome = row.get("total_over")
        else:
            return None
    else:
        outcome = None

    if outcome is not None and not np.isnan(outcome):
        outcome = int(outcome)
    else:
        outcome = None
        if not (market in {"spread", "total"} and final_known):
            return None

    clv = close_imp - entry_imp
    payout, settlement_status = _compute_payout(int(price_american), outcome)

    return BetRecord(
        event_id=int(row.get("event_id", 0)),
        market=market,
        side=side,
        entry_anchor=anchor,
        entry_implied=entry_imp,
        entry_price_american=int(price_american),
        close_implied=close_imp,
        clv=clv,
        outcome=outcome,
        payout=payout,
        settlement_status=settlement_status,
        sport=row.get("sport"),
        league_key=row.get("league_key"),
    )


def _compute_drawdown_and_sharpe(
    payouts: list[float],
) -> tuple[float, float | None]:
    """
    Compute max drawdown and Sharpe-like metric from a payout series (§11).

    Max drawdown: largest peak-to-trough decline in cumulative PnL.
    Sharpe: mean(payout) / std(payout) × sqrt(N) — annualized approximation.
    """
    if not payouts:
        return 0.0, None

    arr = np.array(payouts)
    cum = np.cumsum(arr)
    running_max = np.maximum.accumulate(cum)
    drawdowns = running_max - cum
    max_dd = float(np.max(drawdowns)) / max(len(payouts), 1)  # normalized

    if len(arr) < 2:
        return max_dd, None

    mean_ret = float(np.mean(arr))
    std_ret = float(np.std(arr, ddof=1))
    if std_ret == 0:
        return max_dd, None

    # Sharpe-like: mean / std × sqrt(n_bets) as a seasonal approximation
    sharpe = (mean_ret / std_ret) * np.sqrt(len(arr))
    return max_dd, float(sharpe)


def _aggregate_bets(strategy: str, bets: list[BetRecord]) -> BacktestResult:
    """Compute summary stats from a list of bet records."""
    if not bets:
        return BacktestResult(strategy=strategy, n_bets=0, mean_clv=0, median_clv=0,
                              clv_positive_rate=0, total_roi=0, win_rate=None,
                              max_drawdown=0.0, sharpe_ratio=None)

    clvs = [b.clv for b in bets]
    payouts = [b.payout for b in bets if b.payout is not None]
    outcomes = [b.outcome for b in bets if b.outcome is not None]
    max_dd, sharpe = _compute_drawdown_and_sharpe(payouts)

    return BacktestResult(
        strategy=strategy,
        n_bets=len(bets),
        mean_clv=float(np.mean(clvs)),
        median_clv=float(np.median(clvs)),
        clv_positive_rate=float(np.mean([c > 0 for c in clvs])),
        total_roi=float(np.sum(payouts) / len(bets)) if payouts else 0.0,
        win_rate=float(np.mean(outcomes)) if outcomes else None,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe,
        bets=bets,
    )


def aggregate_bets(strategy: str, bets: list[BetRecord]) -> BacktestResult:
    """Public wrapper for aggregating externally selected bet records."""
    return _aggregate_bets(strategy, bets)


def settlement_breakdown(
    bets: list[BetRecord],
    group_fields: tuple[str, ...] = ("sport", "market", "entry_anchor"),
) -> list[dict[str, object]]:
    """Return W/L/P/V counts and ROI grouped by sport, market, and entry anchor."""
    buckets: dict[tuple[object, ...], dict[str, object]] = {}
    for bet in bets:
        key = tuple(getattr(bet, field, None) or "unknown" for field in group_fields)
        bucket = buckets.setdefault(
            key,
            {
                **dict(zip(group_fields, key, strict=True)),
                "n_bets": 0,
                "wins": 0,
                "losses": 0,
                "pushes": 0,
                "voids": 0,
                "unknown": 0,
                "profit_units": 0.0,
                "roi": 0.0,
            },
        )
        bucket["n_bets"] = int(bucket["n_bets"]) + 1
        status = bet.settlement_status or "unknown"
        if status == "win":
            bucket["wins"] = int(bucket["wins"]) + 1
        elif status == "loss":
            bucket["losses"] = int(bucket["losses"]) + 1
        elif status == "push":
            bucket["pushes"] = int(bucket["pushes"]) + 1
        elif status == "void":
            bucket["voids"] = int(bucket["voids"]) + 1
        else:
            bucket["unknown"] = int(bucket["unknown"]) + 1

        if bet.payout is not None:
            bucket["profit_units"] = float(bucket["profit_units"]) + float(bet.payout)
        bucket["roi"] = float(bucket["profit_units"]) / int(bucket["n_bets"])

    return list(buckets.values())


# ── Strategies ──────────────────────────────────────────────────

def backtest_blind(
    df: pd.DataFrame,
    anchor: str = "T60",
) -> BacktestResult:
    """
    Naive baseline: bet every row at the given anchor.
    No selection logic — just measures what happens if you always bet.
    """
    bets = []
    for _, row in df.iterrows():
        bet = _build_bet(row, anchor)
        if bet:
            bets.append(bet)

    return _aggregate_bets(f"blind_{anchor}", bets)


def backtest_fade_public(
    df: pd.DataFrame,
    anchor: str = "T60",
    bets_pct_threshold: float = 65.0,
) -> BacktestResult:
    """
    Baseline comparator: bet the side with < threshold% of bets
    (classic "fade the public").
    """
    bets_col = f"bets_pct_{anchor}"
    bets = []
    for _, row in df.iterrows():
        bp = row.get(bets_col)
        if bp is None or np.isnan(bp):
            continue
        # Only bet if this side is the contrarian side
        if bp < (100 - bets_pct_threshold):
            bet = _build_bet(row, anchor)
            if bet:
                bets.append(bet)

    return _aggregate_bets(f"fade_public_{anchor}_{bets_pct_threshold}", bets)


def backtest_model_clv(
    df: pd.DataFrame,
    predicted_close: pd.Series,
    anchor: str = "T60",
    clv_threshold: float = 0.01,
) -> BacktestResult:
    """
    Model-driven strategy: bet when model predicts closing implied
    probability differs from current price by more than clv_threshold.

    predicted_close: model's prediction of implied_CLOSE, aligned with df index.
    """
    bets = []
    entry_col = f"implied_{anchor}"
    if not predicted_close.index.equals(df.index):
        raise ValueError("predicted_close must be aligned one-to-one with df.index")

    for idx, row in df.iterrows():
        entry_imp = row.get(entry_col)
        pred_close = predicted_close.get(idx)

        if entry_imp is None or pred_close is None:
            continue
        if np.isnan(entry_imp) or np.isnan(pred_close):
            continue

        # Model says close will be higher than current → bet (for this side)
        expected_clv = pred_close - entry_imp
        if expected_clv > clv_threshold:
            bet = _build_bet(row, anchor)
            if bet:
                bets.append(bet)

    return _aggregate_bets(f"model_clv_{anchor}_thresh{clv_threshold}", bets)


# ── Full backtest suite ─────────────────────────────────────────

def run_backtest_suite(df: pd.DataFrame) -> list[BacktestResult]:
    """
    Run all baseline strategies across OPEN/T60/T30 entry points (§11)
    and print summaries including drawdown and Sharpe.
    """
    results = [
        # Blind baselines at each entry anchor
        backtest_blind(df, "OPEN"),
        backtest_blind(df, "T60"),
        backtest_blind(df, "T30"),
        # Fade-public at each entry anchor
        backtest_fade_public(df, "OPEN", 65),
        backtest_fade_public(df, "T60", 65),
        backtest_fade_public(df, "T30", 65),
        backtest_fade_public(df, "T60", 70),
    ]

    log.info("=== Backtest Results ===")
    for r in results:
        log.info(r.summary())

    return results
