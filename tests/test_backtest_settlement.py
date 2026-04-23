from __future__ import annotations

import pandas as pd
import pytest

from dk_ncaab.analysis.backtest import backtest_blind, backtest_model_clv
from dk_ncaab.analysis.settlement import american_win_profit, settle_profit_units


def test_american_odds_profit_units():
    assert american_win_profit(-110) == pytest.approx(100 / 110)
    assert american_win_profit(150) == pytest.approx(1.5)
    assert american_win_profit(100) == pytest.approx(1.0)
    assert settle_profit_units(-110, 0).profit_units == -1.0
    assert settle_profit_units(-110, None).profit_units == 0.0


def test_backtest_uses_entry_american_price_not_fair_implied():
    df = pd.DataFrame(
        [
            {
                "event_id": 1,
                "market": "moneyline",
                "side": "home",
                "implied_T60": 0.50,
                "implied_CLOSE": 0.60,
                "price_american_T60": -110,
                "home_win": 1,
                "away_win": 0,
            }
        ]
    )

    result = backtest_blind(df, "T60")

    assert result.n_bets == 1
    assert result.bets[0].payout == pytest.approx(100 / 110)
    assert result.total_roi == pytest.approx(100 / 110)


def test_push_counts_as_zero_unit_in_roi_series():
    df = pd.DataFrame(
        [
            {
                "event_id": 1,
                "market": "spread",
                "side": "home",
                "implied_T60": 0.52,
                "implied_CLOSE": 0.55,
                "price_american_T60": -110,
                "line_T60": -3.0,
                "spread_cover_T60": 1,
                "home_win": 1,
            },
            {
                "event_id": 2,
                "market": "spread",
                "side": "home",
                "implied_T60": 0.52,
                "implied_CLOSE": 0.53,
                "price_american_T60": -110,
                "line_T60": -3.0,
                "spread_cover_T60": None,
                "home_win": 1,
            },
            {
                "event_id": 3,
                "market": "spread",
                "side": "home",
                "implied_T60": 0.52,
                "implied_CLOSE": 0.50,
                "price_american_T60": -110,
                "line_T60": -3.0,
                "spread_cover_T60": 0,
                "home_win": 0,
            },
        ]
    )

    result = backtest_blind(df, "T60")

    assert result.n_bets == 3
    assert [b.settlement_status for b in result.bets] == ["win", "push", "loss"]
    assert [b.payout for b in result.bets] == pytest.approx([100 / 110, 0.0, -1.0])
    assert result.total_roi == pytest.approx(((100 / 110) + 0.0 - 1.0) / 3)


def test_spread_backtest_uses_entry_anchor_outcome_not_close_alias():
    df = pd.DataFrame(
        [
            {
                "event_id": 1,
                "sport": "basketball_ncaab",
                "league_key": "ncaab",
                "market": "spread",
                "side": "home",
                "implied_T60": 0.52,
                "implied_CLOSE": 0.55,
                "price_american_T60": -110,
                "line_T60": 3.5,
                "line_CLOSE": 2.5,
                "spread_cover_T60": 1,
                "spread_cover_CLOSE": 0,
                "spread_cover": 0,
                "home_win": 0,
            }
        ]
    )

    result = backtest_blind(df, "T60")

    assert result.n_bets == 1
    assert result.bets[0].sport == "basketball_ncaab"
    assert result.bets[0].settlement_status == "win"
    assert result.bets[0].payout == pytest.approx(100 / 110)


def test_backtest_model_clv_rejects_misaligned_predictions():
    df = pd.DataFrame(
        [
            {
                "event_id": 1,
                "market": "moneyline",
                "side": "home",
                "implied_T60": 0.50,
                "implied_CLOSE": 0.60,
                "price_american_T60": -110,
                "home_win": 1,
            }
        ],
        index=[10],
    )
    predicted = pd.Series([0.65], index=[11])

    with pytest.raises(ValueError):
        backtest_model_clv(df, predicted, "T60")
