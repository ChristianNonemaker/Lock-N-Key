"""Settlement helpers for entry-time betting evaluation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettledBet:
    status: str
    profit_units: float | None
    stake_units: float
    reason: str | None = None


def american_win_profit(price_american: int) -> float:
    """Return profit units for a one-unit winning bet at American odds."""
    if price_american > 0:
        return price_american / 100.0
    if price_american < 0:
        return 100.0 / abs(price_american)
    return 1.0


def settle_profit_units(price_american: int | None, outcome: int | None) -> SettledBet:
    """
    Settle a one-unit bet from a binary outcome.

    outcome: 1=win, 0=loss, None=push. Unknown/void bets should be filtered
    before calling this helper.
    """
    if price_american is None:
        return SettledBet("void", None, 0.0, "missing_price")
    if outcome is None:
        return SettledBet("push", 0.0, 1.0)
    if outcome == 1:
        return SettledBet("win", american_win_profit(int(price_american)), 1.0)
    if outcome == 0:
        return SettledBet("loss", -1.0, 1.0)
    return SettledBet("void", None, 0.0, "invalid_outcome")


def break_even_probability(price_american: int) -> float:
    """Return the win probability needed to break even at American odds."""
    if price_american > 0:
        return 100.0 / (price_american + 100.0)
    if price_american < 0:
        return abs(price_american) / (abs(price_american) + 100.0)
    return 0.5


def expected_value_units(model_win_prob: float, price_american: int) -> float:
    """Expected profit units for a one-unit stake at American odds."""
    win_profit = american_win_profit(price_american)
    return model_win_prob * win_profit - (1.0 - model_win_prob)
