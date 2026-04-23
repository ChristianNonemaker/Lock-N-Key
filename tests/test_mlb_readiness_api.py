from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app, get_db
from dk_ncaab.db.models import (
    Base,
    Event,
    EventProviderKey,
    League,
    MlbPlayerGameLog,
    MlbProbableStarter,
    MlbTeamGameLog,
    OddsQuote,
    Player,
    Team,
)


def test_mlb_readiness_reports_upcoming_game_ready_after_settlement():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    target_start = now + timedelta(days=1)
    prior_start = now - timedelta(days=2)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()

        home = Team(league_id=league.id, name="Home Bats", normalized_name="home bats")
        away = Team(league_id=league.id, name="Away Bats", normalized_name="away bats")
        other = Team(league_id=league.id, name="Other Club", normalized_name="other club")
        session.add_all([home, away, other])
        session.flush()

        prior_home = Event(
            league_id=league.id,
            external_event_key="prior-home",
            start_time_utc=prior_start,
            home_team_id=home.id,
            away_team_id=other.id,
            status="final",
        )
        prior_away = Event(
            league_id=league.id,
            external_event_key="prior-away",
            start_time_utc=prior_start,
            home_team_id=other.id,
            away_team_id=away.id,
            status="final",
        )
        target = Event(
            league_id=league.id,
            external_event_key="target",
            start_time_utc=target_start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        session.add_all([prior_home, prior_away, target])
        session.flush()

        home_starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="home-starter",
            full_name="Home Starter",
        )
        away_starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="away-starter",
            full_name="Away Starter",
        )
        session.add_all([home_starter, away_starter])
        session.flush()

        session.add_all(
            [
                EventProviderKey(
                    event_id=target.id,
                    sport_key="baseball_mlb",
                    provider="mlb_stats_api",
                    provider_event_key="777",
                ),
                OddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market="moneyline",
                    side="home",
                    line=None,
                    price_american=-120,
                    implied_probability=0.545,
                    collected_at_utc=target_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                MlbTeamGameLog(
                    event_id=prior_home.id,
                    team_id=home.id,
                    game_date_utc=prior_start,
                    is_home=True,
                    opponent_team_id=other.id,
                    runs_for=5,
                    runs_against=2,
                    source="mlb_stats_api",
                ),
                MlbTeamGameLog(
                    event_id=prior_away.id,
                    team_id=away.id,
                    game_date_utc=prior_start,
                    is_home=False,
                    opponent_team_id=other.id,
                    runs_for=4,
                    runs_against=3,
                    source="mlb_stats_api",
                ),
                MlbProbableStarter(
                    event_id=target.id,
                    team_id=home.id,
                    player_id=home_starter.id,
                    is_home=True,
                    source="schedule",
                    collected_at_utc=now,
                ),
                MlbProbableStarter(
                    event_id=target.id,
                    team_id=away.id,
                    player_id=away_starter.id,
                    is_home=False,
                    source="schedule",
                    collected_at_utc=now,
                ),
                MlbPlayerGameLog(
                    event_id=prior_home.id,
                    player_id=home_starter.id,
                    team_id=home.id,
                    game_date_utc=prior_start,
                    is_home=True,
                    pitching_started=True,
                    source="mlb_stats_api",
                ),
                MlbPlayerGameLog(
                    event_id=prior_away.id,
                    player_id=away_starter.id,
                    team_id=away.id,
                    game_date_utc=prior_start,
                    is_home=False,
                    pitching_started=True,
                    source="mlb_stats_api",
                ),
            ]
        )
        session.commit()

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/analysis/mlb/readiness?days_back=3&days_forward=3")
        assert response.status_code == 200
        payload = response.json()

        summary = payload["summary"]
        assert summary["visible_events"] == 3
        assert summary["events_with_pregame_odds"] == 1
        assert summary["events_with_both_team_history"] == 1
        assert summary["events_with_both_starter_history"] == 1
        assert summary["pending_pregame_events"] == 1
        assert summary["ready_after_settlement_events"] == 1

        target_row = next(row for row in payload["events"] if row["event_id"] == target.id)
        assert target_row["ready_after_settlement"] is True
        assert target_row["gaps"] == ["awaiting_settlement"]
        assert target_row["home_starter"]["player_name"] == "Home Starter"
        assert target_row["away_starter"]["prior_starts"] == 1
    finally:
        app.dependency_overrides.clear()
