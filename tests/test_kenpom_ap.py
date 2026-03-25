"""
Tests for KenPom deviation, AP rankings, interaction features,
outcome model, and backtest enhancements.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.db.models import (
    Base, League, Team, Event, OddsQuote, EventResult,
    KenPomRating, APRanking,
)
from dk_ncaab.etl.normalize import american_to_implied
from dk_ncaab.collectors.kenpom import expected_spread, spread_deviation
from dk_ncaab.collectors.ap_rankings import compute_event_ap_features, UNRANKED_SENTINEL


# ── Helpers ─────────────────────────────────────────────────────

def _strip_tz(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@pytest.fixture
def session():
    """In-memory SQLite session with all tables."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()

    league = League(id=1, key="ncaab", name="NCAA Basketball")
    sess.add(league)
    home = Team(id=1, league_id=1, name="Duke", normalized_name="duke")
    away = Team(id=2, league_id=1, name="UNC", normalized_name="unc")
    sess.add_all([home, away])
    sess.flush()
    yield sess
    sess.close()


# ── KenPom tests ────────────────────────────────────────────────

class TestKenPomMath:
    def test_expected_spread_home_favored(self):
        """Home team with higher AdjEM → negative spread (favored)."""
        # Home AdjEM=20, Away AdjEM=10, HCA=3.5
        # margin = 20 - 10 + 3.5 = 13.5, spread = -13.5
        result = expected_spread(20.0, 10.0)
        assert result == -13.5

    def test_expected_spread_away_favored(self):
        """Away team with higher AdjEM → positive spread (home underdog)."""
        result = expected_spread(5.0, 20.0)
        # margin = 5 - 20 + 3.5 = -11.5, spread = 11.5
        assert result == 11.5

    def test_expected_spread_even(self):
        """Equal AdjEM → spread is just home court advantage."""
        result = expected_spread(15.0, 15.0)
        assert result == -3.5  # -(0 + 3.5)

    def test_spread_deviation_positive(self):
        """Market has home as bigger underdog than KenPom."""
        dev = spread_deviation(market_spread=5.0, kenpom_spread=-3.5)
        assert dev == 8.5  # market thinks home is worse than KenPom

    def test_spread_deviation_zero(self):
        """Market agrees with KenPom."""
        dev = spread_deviation(-3.5, -3.5)
        assert dev == 0.0


class TestKenPomDB:
    def test_get_rating_for_event(self, session):
        """Ratings from before event date are returned, not future ones."""
        from dk_ncaab.collectors.kenpom import get_rating_for_event

        # Add a rating 2 days before the event
        rating_date = datetime(2025, 3, 13)
        session.add(KenPomRating(
            team_id=1, rating_date=rating_date,
            adj_o=115.0, adj_d=95.0, adj_em=20.0, tempo=68.5, sos=5.0,
        ))
        # Add a future rating
        session.add(KenPomRating(
            team_id=1, rating_date=datetime(2025, 3, 20),
            adj_o=116.0, adj_d=94.0, adj_em=22.0, tempo=69.0, sos=4.0,
        ))
        session.flush()

        event_date = datetime(2025, 3, 15)
        rating = get_rating_for_event(session, 1, event_date)
        assert rating is not None
        assert rating.adj_em == 20.0  # should get the earlier rating, not the future one

    def test_compute_event_kenpom(self, session):
        """Full KenPom computation for an event."""
        from dk_ncaab.collectors.kenpom import compute_event_kenpom

        session.add(KenPomRating(
            team_id=1, rating_date=datetime(2025, 3, 13),
            adj_o=115.0, adj_d=95.0, adj_em=20.0, tempo=68.5, sos=5.0,
        ))
        session.add(KenPomRating(
            team_id=2, rating_date=datetime(2025, 3, 13),
            adj_o=110.0, adj_d=100.0, adj_em=10.0, tempo=70.0, sos=8.0,
        ))
        session.flush()

        result = compute_event_kenpom(session, 1, 2, datetime(2025, 3, 15))
        assert result is not None
        assert result["adj_em_diff"] == 10.0  # 20 - 10
        assert result["kenpom_expected_spread"] == -13.5  # -(10 + 3.5)

    def test_compute_event_kenpom_missing(self, session):
        """Returns None if no ratings available."""
        from dk_ncaab.collectors.kenpom import compute_event_kenpom
        result = compute_event_kenpom(session, 1, 2, datetime(2025, 3, 15))
        assert result is None


# ── AP Rankings tests ───────────────────────────────────────────

class TestAPRankings:
    def test_ranked_team(self, session):
        """Ranked team returns its rank."""
        from dk_ncaab.collectors.ap_rankings import get_rank_for_event

        session.add(APRanking(
            team_id=1, poll_date=datetime(2025, 3, 10), rank=5, votes=1200,
        ))
        session.flush()

        rank = get_rank_for_event(session, 1, datetime(2025, 3, 15))
        assert rank == 5

    def test_unranked_team(self, session):
        """Unranked team returns sentinel 0."""
        from dk_ncaab.collectors.ap_rankings import get_rank_for_event
        rank = get_rank_for_event(session, 2, datetime(2025, 3, 15))
        assert rank == UNRANKED_SENTINEL

    def test_stale_ranking(self, session):
        """Ranking older than 14 days is treated as stale → unranked."""
        from dk_ncaab.collectors.ap_rankings import get_rank_for_event

        session.add(APRanking(
            team_id=1, poll_date=datetime(2025, 2, 20), rank=10,
        ))
        session.flush()

        rank = get_rank_for_event(session, 1, datetime(2025, 3, 15))
        assert rank == UNRANKED_SENTINEL  # 23 days old

    def test_compute_event_features(self, session):
        """Full AP feature computation."""
        session.add(APRanking(team_id=1, poll_date=datetime(2025, 3, 10), rank=3))
        session.flush()

        result = compute_event_ap_features(session, 1, 2, datetime(2025, 3, 15))
        assert result["ap_rank_home"] == 3
        assert result["ap_rank_away"] == 0  # unranked
        assert result["ranked_vs_unranked"] == 1
        assert result["ap_rank_diff"] > 0  # home is ranked higher → positive

    def test_both_ranked(self, session):
        """Both teams ranked → ranked_vs_unranked = 0."""
        session.add(APRanking(team_id=1, poll_date=datetime(2025, 3, 10), rank=3))
        session.add(APRanking(team_id=2, poll_date=datetime(2025, 3, 10), rank=15))
        session.flush()

        result = compute_event_ap_features(session, 1, 2, datetime(2025, 3, 15))
        assert result["ranked_vs_unranked"] == 0
        assert result["ap_rank_diff"] == 15 - 3  # = 12


# ── Feature integration tests ──────────────────────────────────

class TestFeatureIntegration:
    def test_feature_row_has_all_new_fields(self):
        """FeatureRow contains all direction-mandated fields."""
        from dk_ncaab.etl.features import FeatureRow
        fr = FeatureRow(event_id=1, market="spread", side="home")

        # §4: KenPom
        assert hasattr(fr, "kenpom_expected_spread")
        assert hasattr(fr, "spread_dev_OPEN")
        assert hasattr(fr, "spread_dev_CLOSE")
        assert hasattr(fr, "adj_em_diff")

        # §5: AP
        assert hasattr(fr, "ap_rank_home")
        assert hasattr(fr, "ap_rank_away")
        assert hasattr(fr, "ap_rank_diff")
        assert hasattr(fr, "ranked_vs_unranked")

        # §6: Late steam
        assert hasattr(fr, "late_steam")
        assert hasattr(fr, "late_steam_direction")

        # §7: Interactions
        assert hasattr(fr, "deviation_x_public_extreme")
        assert hasattr(fr, "movement_x_public_extreme")
        assert hasattr(fr, "hmb_x_deviation")

        # §9: Model expected value + entry spread cover
        assert hasattr(fr, "model_expected_value")
        assert hasattr(fr, "spread_cover_entry")


class TestBacktestEnhancements:
    def test_backtest_result_has_drawdown_and_sharpe(self):
        """BacktestResult includes drawdown and Sharpe from §11."""
        from dk_ncaab.analysis.backtest import BacktestResult
        br = BacktestResult(
            strategy="test", n_bets=10, mean_clv=0.01, median_clv=0.005,
            clv_positive_rate=0.6, total_roi=0.02, win_rate=0.55,
            max_drawdown=0.15, sharpe_ratio=1.2,
        )
        assert br.max_drawdown == 0.15
        assert br.sharpe_ratio == 1.2
        assert "MaxDD" in br.summary()
        assert "Sharpe" in br.summary()

    def test_drawdown_computation(self):
        """_compute_drawdown_and_sharpe calculates correctly."""
        from dk_ncaab.analysis.backtest import _compute_drawdown_and_sharpe

        # Simple payout series: +1, +1, -3, +1 → cumulative: 1, 2, -1, 0
        # Peak = 2, trough after peak = -1, drawdown = 3
        payouts = [1.0, 1.0, -3.0, 1.0]
        dd, sharpe = _compute_drawdown_and_sharpe(payouts)
        assert dd > 0  # there is a drawdown
        assert sharpe is not None

    def test_empty_payouts(self):
        from dk_ncaab.analysis.backtest import _compute_drawdown_and_sharpe
        dd, sharpe = _compute_drawdown_and_sharpe([])
        assert dd == 0.0
        assert sharpe is None


class TestDefaultFeatures:
    def test_kenpom_features_in_default(self):
        """KenPom deviation features are in DEFAULT_FEATURES (§4)."""
        from dk_ncaab.analysis.models_close_predict import DEFAULT_FEATURES
        assert "adj_em_diff" in DEFAULT_FEATURES
        assert "kenpom_expected_spread" in DEFAULT_FEATURES
        assert "spread_dev_OPEN" in DEFAULT_FEATURES
        assert "spread_dev_T60" in DEFAULT_FEATURES

    def test_ap_features_in_default(self):
        """AP ranking features are in DEFAULT_FEATURES (§5)."""
        from dk_ncaab.analysis.models_close_predict import DEFAULT_FEATURES
        assert "ap_rank_home" in DEFAULT_FEATURES
        assert "ap_rank_diff" in DEFAULT_FEATURES
        assert "ranked_vs_unranked" in DEFAULT_FEATURES

    def test_interaction_features_in_default(self):
        """Interaction features are in DEFAULT_FEATURES (§7)."""
        from dk_ncaab.analysis.models_close_predict import DEFAULT_FEATURES
        assert "deviation_x_public_extreme" in DEFAULT_FEATURES
        assert "hmb_x_deviation" in DEFAULT_FEATURES

    def test_late_steam_in_default(self):
        """Late steam indicator is in DEFAULT_FEATURES (§6)."""
        from dk_ncaab.analysis.models_close_predict import DEFAULT_FEATURES
        assert "late_steam" in DEFAULT_FEATURES
        assert "late_steam_direction" in DEFAULT_FEATURES
