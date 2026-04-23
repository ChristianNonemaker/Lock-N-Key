from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.db.models import (
    Base,
    Event,
    League,
    MlbPlayerGameLog,
    MlbProbableStarter,
    MlbTeamGameLog,
    Player,
    Team,
)
from dk_ncaab.etl.features import build_features
from dk_ncaab.etl.normalize import normalize_team_name


def _dt(day: int) -> datetime:
    return datetime(2026, 4, day, 18, 0, tzinfo=timezone.utc)


def _team(session, league, name):
    team = Team(league_id=league.id, name=name, normalized_name=normalize_team_name(name))
    session.add(team)
    session.flush()
    return team


def _event(session, league, home, away, day, key):
    event = Event(
        league_id=league.id,
        external_event_key=key,
        start_time_utc=_dt(day),
        home_team_id=home.id,
        away_team_id=away.id,
        status="final",
    )
    session.add(event)
    session.flush()
    return event


def test_mlb_features_use_only_prior_team_and_starter_logs():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        home = _team(session, league, "Home Bats")
        away = _team(session, league, "Away Bats")
        opponent = _team(session, league, "Other Club")
        home_starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="9002",
            full_name="Home Starter",
        )
        away_starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="9001",
            full_name="Away Starter",
        )
        session.add_all([home_starter, away_starter])
        session.flush()

        prior_home = _event(session, league, home, opponent, 18, "prior-home")
        prior_away = _event(session, league, away, opponent, 18, "prior-away")
        target = _event(session, league, home, away, 20, "target")

        session.add_all(
            [
                MlbTeamGameLog(
                    event_id=prior_home.id,
                    team_id=home.id,
                    game_date_utc=_dt(18),
                    is_home=True,
                    opponent_team_id=opponent.id,
                    runs_for=6,
                    runs_against=2,
                    bullpen_outs=9,
                    source="mlb_stats_api",
                ),
                MlbTeamGameLog(
                    event_id=prior_away.id,
                    team_id=away.id,
                    game_date_utc=_dt(18),
                    is_home=False,
                    opponent_team_id=opponent.id,
                    runs_for=1,
                    runs_against=5,
                    bullpen_outs=12,
                    source="mlb_stats_api",
                ),
                MlbTeamGameLog(
                    event_id=target.id,
                    team_id=home.id,
                    game_date_utc=_dt(20),
                    is_home=True,
                    opponent_team_id=away.id,
                    runs_for=99,
                    runs_against=0,
                    bullpen_outs=0,
                    source="mlb_stats_api",
                ),
                MlbProbableStarter(
                    event_id=target.id,
                    team_id=home.id,
                    player_id=home_starter.id,
                    is_home=True,
                    source="schedule",
                    collected_at_utc=_dt(19),
                ),
                MlbProbableStarter(
                    event_id=target.id,
                    team_id=away.id,
                    player_id=away_starter.id,
                    is_home=False,
                    source="schedule",
                    collected_at_utc=_dt(19),
                ),
                MlbPlayerGameLog(
                    event_id=prior_home.id,
                    player_id=home_starter.id,
                    team_id=home.id,
                    game_date_utc=_dt(18),
                    is_home=True,
                    pitching_started=True,
                    innings_pitched_outs=18,
                    pitching_hits=4,
                    earned_runs=2,
                    pitching_base_on_balls=1,
                    pitching_strike_outs=7,
                    source="mlb_stats_api",
                ),
                MlbPlayerGameLog(
                    event_id=prior_away.id,
                    player_id=away_starter.id,
                    team_id=away.id,
                    game_date_utc=_dt(18),
                    is_home=False,
                    pitching_started=True,
                    innings_pitched_outs=15,
                    pitching_hits=8,
                    earned_runs=5,
                    pitching_base_on_balls=3,
                    pitching_strike_outs=4,
                    source="mlb_stats_api",
                ),
            ]
        )
        session.commit()

        row = build_features(session, target.id, "moneyline", "home")

        assert row.sport == "baseball_mlb"
        assert row.home_mlb_runs_for_l5 == 6
        assert row.away_mlb_runs_for_l5 == 1
        assert row.home_mlb_run_diff_l5 == 4
        assert row.away_mlb_run_diff_l5 == -4
        assert row.mlb_run_diff_delta_l5 == 8
        assert row.home_mlb_rest_days == 2
        assert row.home_mlb_starter_era_l3 == 3.0
        assert round(row.home_mlb_starter_whip_l3, 3) == 0.833
        assert row.home_mlb_starter_k_bb_l3 == 6
