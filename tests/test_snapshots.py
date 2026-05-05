"""
Snapshot extraction correctness tests.

Uses an in-memory SQLite database (swapped for Postgres in CI).
Validates all anchor selection rules, edge cases, and leakage prevention.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.db.models import Base, League, Team, Event, OddsQuote
from dk_ncaab.etl.snapshots import get_snapshot, get_snapshot_set
from dk_ncaab.etl.normalize import american_to_implied


# ── Helpers ─────────────────────────────────────────────────────

def _strip_tz(dt: datetime) -> datetime:
    """Strip timezone for SQLite comparison (SQLite returns naive datetimes)."""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def session():
    """In-memory SQLite session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    sess = Session()

    # Seed reference data
    league = League(id=1, key="ncaab", name="NCAA Basketball")
    sess.add(league)
    home = Team(id=1, league_id=1, name="Duke", normalized_name="duke")
    away = Team(id=2, league_id=1, name="UNC", normalized_name="unc")
    sess.add_all([home, away])
    sess.flush()

    yield sess
    sess.close()


@pytest.fixture
def event_with_quotes(session):
    """
    Create an event at 19:00 UTC with 20 quote rows spanning
    12:00 → 19:10 UTC (some are post-tip).
    """
    tip = datetime(2025, 3, 15, 19, 0, 0, tzinfo=timezone.utc)
    event = Event(
        id=1, league_id=1, external_event_key="test-001",
        start_time_utc=tip, home_team_id=1, away_team_id=2,
        first_seen_at_utc=datetime(2025, 3, 15, 8, 0, 0, tzinfo=timezone.utc),
    )
    session.add(event)

    # Generate quotes every 30 minutes from 12:00 to 19:00, plus one at 19:10
    base = datetime(2025, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    prices = [-110, -115, -110, -120, -115, -125, -130, -125, -120,
              -115, -120, -125, -130, -135, -140, -130, -125, -120, -115, -110]
    for i, price in enumerate(prices):
        t = base + timedelta(minutes=30 * i)
        session.add(OddsQuote(
            event_id=1, book="draftkings", market="spread", side="home",
            line=-3.5, price_american=price,
            implied_probability=round(american_to_implied(price), 6),
            collected_at_utc=t, source="test",
        ))

    session.flush()
    return event


# ── Tests ───────────────────────────────────────────────────────

class TestSnapshotExtraction:
    """Validates OPEN/T60/T30/CLOSE anchor selection."""

    def test_open_is_earliest(self, session, event_with_quotes):
        snap = get_snapshot(session, 1, "spread", "home", "OPEN")
        assert snap is not None
        assert snap.price_american == -110  # first quote
        assert snap.collected_at_utc.hour == 12

    def test_close_is_latest_before_tip(self, session, event_with_quotes):
        snap = get_snapshot(session, 1, "spread", "home", "CLOSE")
        assert snap is not None
        # Tip is 19:00. Last quote at or before 19:00
        assert _strip_tz(snap.collected_at_utc) <= _strip_tz(event_with_quotes.start_time_utc)

    def test_t60_is_latest_before_tip_minus_60(self, session, event_with_quotes):
        snap = get_snapshot(session, 1, "spread", "home", "T60")
        assert snap is not None
        cutoff = _strip_tz(event_with_quotes.start_time_utc) - timedelta(minutes=60)
        assert _strip_tz(snap.collected_at_utc) <= cutoff

    def test_t30_is_latest_before_tip_minus_30(self, session, event_with_quotes):
        snap = get_snapshot(session, 1, "spread", "home", "T30")
        assert snap is not None
        cutoff = _strip_tz(event_with_quotes.start_time_utc) - timedelta(minutes=30)
        assert _strip_tz(snap.collected_at_utc) <= cutoff

    def test_post_tip_excluded_from_close(self, session, event_with_quotes):
        """A quote after tip-off must not be selected as CLOSE."""
        # Add a post-tip quote
        post_tip = event_with_quotes.start_time_utc + timedelta(minutes=5)
        session.add(OddsQuote(
            event_id=1, book="draftkings", market="spread", side="home",
            line=-3.5, price_american=-200,
            implied_probability=round(american_to_implied(-200), 6),
            collected_at_utc=post_tip, source="test",
        ))
        session.flush()

        snap = get_snapshot(session, 1, "spread", "home", "CLOSE")
        assert snap is not None
        assert snap.price_american != -200  # must NOT pick the post-tip row
        assert _strip_tz(snap.collected_at_utc) <= _strip_tz(event_with_quotes.start_time_utc)

    def test_exact_tip_quote_is_excluded_from_close(self, session):
        tip = datetime(2025, 3, 15, 19, 0, 0, tzinfo=timezone.utc)
        session.add(Event(
            id=3,
            league_id=1,
            external_event_key="test-003",
            start_time_utc=tip,
            home_team_id=1,
            away_team_id=2,
            first_seen_at_utc=tip - timedelta(hours=2),
        ))
        session.add_all(
            [
                OddsQuote(
                    event_id=3,
                    book="draftkings",
                    market="spread",
                    side="home",
                    line=-3.5,
                    price_american=-115,
                    implied_probability=round(american_to_implied(-115), 6),
                    collected_at_utc=tip - timedelta(minutes=5),
                    source="test",
                ),
                OddsQuote(
                    event_id=3,
                    book="draftkings",
                    market="spread",
                    side="home",
                    line=-4.0,
                    price_american=-130,
                    implied_probability=round(american_to_implied(-130), 6),
                    collected_at_utc=tip,
                    source="test",
                ),
            ]
        )
        session.flush()

        snap = get_snapshot(session, 3, "spread", "home", "CLOSE")
        assert snap is not None
        assert snap.price_american == -115
        assert _strip_tz(snap.collected_at_utc) == _strip_tz(tip - timedelta(minutes=5))

    def test_no_data_returns_none(self, session, event_with_quotes):
        """Querying a market/side with no quotes returns None."""
        snap = get_snapshot(session, 1, "moneyline", "home", "OPEN")
        assert snap is None

    def test_snapshot_set_all_anchors(self, session, event_with_quotes):
        ss = get_snapshot_set(session, 1, "spread", "home")
        assert ss.OPEN is not None
        assert ss.CLOSE is not None
        # OPEN should be earlier than CLOSE
        assert ss.OPEN.collected_at_utc <= ss.CLOSE.collected_at_utc

    def test_idempotent(self, session, event_with_quotes):
        """Running snapshot extraction twice yields identical results."""
        s1 = get_snapshot_set(session, 1, "spread", "home")
        s2 = get_snapshot_set(session, 1, "spread", "home")
        assert s1.OPEN == s2.OPEN
        assert s1.CLOSE == s2.CLOSE
        assert s1.T60 == s2.T60
        assert s1.T30 == s2.T30


class TestEdgeCases:
    def test_single_quote(self, session):
        """When only one quote exists, OPEN = CLOSE = that quote."""
        tip = datetime(2025, 3, 15, 19, 0, 0, tzinfo=timezone.utc)
        session.add(Event(
            id=2, league_id=1, external_event_key="test-002",
            start_time_utc=tip, home_team_id=1, away_team_id=2,
            first_seen_at_utc=tip - timedelta(hours=1),
        ))
        session.add(OddsQuote(
            event_id=2, book="draftkings", market="moneyline", side="home",
            line=None, price_american=-150,
            implied_probability=round(american_to_implied(-150), 6),
            collected_at_utc=tip - timedelta(hours=1),
            source="test",
        ))
        session.flush()

        ss = get_snapshot_set(session, 2, "moneyline", "home")
        assert ss.OPEN is not None
        assert ss.CLOSE is not None
        assert ss.OPEN.price_american == ss.CLOSE.price_american
        assert ss.T60 is None
        assert ss.T30 is not None
        assert ss.T30.price_american == -150

    def test_nonexistent_event(self, session):
        snap = get_snapshot(session, 999, "spread", "home", "OPEN")
        assert snap is None
