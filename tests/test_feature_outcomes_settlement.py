from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.db.models import Base, Event, EventResult, League, Team
from dk_ncaab.etl.features import FeatureRow, _fill_outcomes


def _session_with_event(home_score: int, away_score: int):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    sess = Session()
    league = League(id=1, key="ncaab", name="NCAAB")
    home = Team(id=1, league_id=1, name="Home", normalized_name="home")
    away = Team(id=2, league_id=1, name="Away", normalized_name="away")
    event = Event(
        id=1,
        league_id=1,
        external_event_key="espn:1",
        start_time_utc=datetime(2026, 4, 20, 23, 30, tzinfo=timezone.utc),
        home_team_id=1,
        away_team_id=2,
        status="final",
    )
    result = EventResult(
        event_id=1,
        home_score=home_score,
        away_score=away_score,
        status="final",
    )
    sess.add_all([league, home, away, event, result])
    sess.commit()
    return sess, event


def test_spread_push_sets_cover_none_for_home_and_away():
    sess, event = _session_with_event(home_score=70, away_score=67)
    try:
        home_row = FeatureRow(event_id=1, market="spread", side="home", line_CLOSE=-3.0)
        away_row = FeatureRow(event_id=1, market="spread", side="away", line_CLOSE=3.0)

        _fill_outcomes(sess, home_row, event)
        _fill_outcomes(sess, away_row, event)

        assert home_row.spread_cover is None
        assert away_row.spread_cover is None
        assert home_row.spread_cover_CLOSE is None
        assert away_row.spread_cover_CLOSE is None
    finally:
        sess.close()


def test_total_push_sets_total_outcome_none_for_over_and_under():
    sess, event = _session_with_event(home_score=70, away_score=70)
    try:
        over_row = FeatureRow(event_id=1, market="total", side="over", line_CLOSE=140.0)
        under_row = FeatureRow(event_id=1, market="total", side="under", line_CLOSE=140.0)

        _fill_outcomes(sess, over_row, event)
        _fill_outcomes(sess, under_row, event)

        assert over_row.total_over is None
        assert under_row.total_over is None
        assert over_row.total_over_CLOSE is None
        assert under_row.total_over_CLOSE is None
    finally:
        sess.close()


def test_anchor_specific_spread_and_total_outcomes_can_differ():
    sess, event = _session_with_event(home_score=70, away_score=67)
    try:
        spread_row = FeatureRow(
            event_id=1,
            market="spread",
            side="home",
            line_T60=-2.5,
            line_CLOSE=-3.5,
        )
        total_row = FeatureRow(
            event_id=1,
            market="total",
            side="over",
            line_T60=136.5,
            line_CLOSE=137.5,
        )

        _fill_outcomes(sess, spread_row, event)
        _fill_outcomes(sess, total_row, event)

        assert spread_row.spread_cover_T60 == 1
        assert spread_row.spread_cover_CLOSE == 0
        assert spread_row.spread_cover == 0
        assert total_row.total_over_T60 == 1
        assert total_row.total_over_CLOSE == 0
        assert total_row.total_over == 0
    finally:
        sess.close()
