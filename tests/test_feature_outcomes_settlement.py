from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.db.models import Base, Event, EventResult, League, MlbPlayerGameLog, MlbTeamGameLog, Player, Team
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


def test_mlb_team_total_settles_from_team_game_log():
    sess, event = _session_with_event(home_score=0, away_score=0)
    try:
        event.league.key = "mlb"
        sess.add(
            MlbTeamGameLog(
                event_id=event.id,
                team_id=event.home_team_id,
                game_date_utc=event.start_time_utc,
                is_home=True,
                opponent_team_id=event.away_team_id,
                runs_for=5,
                source="mlb_stats_api",
            )
        )
        sess.commit()

        row = FeatureRow(
            event_id=event.id,
            market="team_totals",
            side="over",
            participant_name="Home",
            sport="baseball_mlb",
            line_T60=4.5,
            line_CLOSE=5.5,
        )

        _fill_outcomes(sess, row, event)

        assert row.total_over_T60 == 1
        assert row.total_over_CLOSE == 0
        assert row.total_over == 0
    finally:
        sess.close()


def test_mlb_player_props_settle_from_player_game_log():
    sess, event = _session_with_event(home_score=0, away_score=0)
    try:
        event.league.key = "mlb"
        player = Player(
            league_id=event.league_id,
            provider="mlb_stats_api",
            external_player_key="player-1",
            full_name="Home Hitter",
        )
        sess.add(player)
        sess.flush()
        sess.add(
            MlbPlayerGameLog(
                event_id=event.id,
                player_id=player.id,
                team_id=event.home_team_id,
                game_date_utc=event.start_time_utc,
                is_home=True,
                batting_started=True,
                hits=2,
                doubles=1,
                triples=0,
                home_runs=1,
                source="mlb_stats_api",
            )
        )
        sess.commit()

        hits_row = FeatureRow(
            event_id=event.id,
            market="batter_hits",
            side="over",
            participant_name="Home Hitter",
            sport="baseball_mlb",
            line_CLOSE=1.5,
        )
        bases_row = FeatureRow(
            event_id=event.id,
            market="batter_total_bases",
            side="over",
            participant_name="Home Hitter",
            sport="baseball_mlb",
            line_CLOSE=5.5,
        )

        _fill_outcomes(sess, hits_row, event)
        _fill_outcomes(sess, bases_row, event)

        assert hits_row.total_over_CLOSE == 1
        assert bases_row.total_over_CLOSE == 1
    finally:
        sess.close()
