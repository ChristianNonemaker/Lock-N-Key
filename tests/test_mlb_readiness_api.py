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
    EventOddsQuote,
    EventProviderKey,
    EventResult,
    League,
    MlbPlayerGameLog,
    MlbProbableStarter,
    MlbTeamGameLog,
    OddsQuote,
    Player,
    Team,
)


def test_mlb_readiness_reports_upcoming_game_ready_after_settlement(monkeypatch):
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
    future_start = now + timedelta(days=7)

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
        future_home = Event(
            league_id=league.id,
            external_event_key="future-home",
            start_time_utc=future_start,
            home_team_id=home.id,
            away_team_id=other.id,
            status="upcoming",
        )
        session.add_all([prior_home, prior_away, target, future_home])
        session.flush()
        session.add_all(
            [
                EventResult(
                    event_id=prior_home.id,
                    home_score=5,
                    away_score=2,
                    status="final",
                    completed_at_utc=prior_start + timedelta(hours=3),
                ),
                EventResult(
                    event_id=prior_away.id,
                    home_score=3,
                    away_score=4,
                    status="final",
                    completed_at_utc=prior_start + timedelta(hours=3),
                ),
            ]
        )

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
        home_bat = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="home-bat",
            full_name="Home Bat",
            primary_position="OF",
        )
        away_bat = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="away-bat",
            full_name="Away Bat",
            primary_position="1B",
        )
        home_reliever = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="home-rp",
            full_name="Home Reliever",
            primary_position="P",
        )
        away_reliever = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="away-rp",
            full_name="Away Reliever",
            primary_position="P",
        )
        session.add_all(
            [home_starter, away_starter, home_bat, away_bat, home_reliever, away_reliever]
        )
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
                OddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market="spread",
                    side="home",
                    line=-1.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=target_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market="spread",
                    side="away",
                    line=1.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=target_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market="total",
                    side="over",
                    line=8.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=target_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market="total",
                    side="under",
                    line=8.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=target_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=prior_home.id,
                    book="draftkings",
                    market="moneyline",
                    side="home",
                    line=None,
                    price_american=-130,
                    implied_probability=0.565,
                    collected_at_utc=prior_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=prior_home.id,
                    book="draftkings",
                    market="spread",
                    side="home",
                    line=-1.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=prior_home.id,
                    book="draftkings",
                    market="total",
                    side="over",
                    line=8.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=prior_home.id,
                    book="draftkings",
                    market="total",
                    side="under",
                    line=8.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                EventOddsQuote(
                    event_id=prior_home.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=home.id,
                    participant_name="Home Bats",
                    side="over",
                    line=4.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=2),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=prior_home.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=home.id,
                    participant_name="Home Bats",
                    side="under",
                    line=4.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=2),
                    source="the_odds_api_event_odds",
                ),
                OddsQuote(
                    event_id=prior_away.id,
                    book="draftkings",
                    market="spread",
                    side="home",
                    line=1.0,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=prior_away.id,
                    book="draftkings",
                    market="total",
                    side="over",
                    line=7.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                OddsQuote(
                    event_id=prior_away.id,
                    book="draftkings",
                    market="total",
                    side="under",
                    line=7.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=1),
                    source="the_odds_api",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=home.id,
                    participant_name="Home Bats",
                    side="over",
                    line=5.0,
                    price_american=-105,
                    implied_probability=0.512,
                    collected_at_utc=target_start - timedelta(hours=3),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=home.id,
                    participant_name="Home Bats",
                    side="under",
                    line=5.0,
                    price_american=-115,
                    implied_probability=0.535,
                    collected_at_utc=target_start - timedelta(hours=3),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=away.id,
                    participant_name="Away Bats",
                    side="over",
                    line=3.5,
                    price_american=-120,
                    implied_probability=0.545,
                    collected_at_utc=target_start - timedelta(hours=3),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=away.id,
                    participant_name="Away Bats",
                    side="under",
                    line=3.5,
                    price_american=100,
                    implied_probability=0.5,
                    collected_at_utc=target_start - timedelta(hours=3),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=home.id,
                    participant_name="Home Bats",
                    side="over",
                    line=5.5,
                    price_american=-115,
                    implied_probability=0.535,
                    collected_at_utc=target_start - timedelta(minutes=30),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=home.id,
                    participant_name="Home Bats",
                    side="under",
                    line=5.5,
                    price_american=-105,
                    implied_probability=0.512,
                    collected_at_utc=target_start - timedelta(minutes=30),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=away.id,
                    participant_name="Away Bats",
                    side="over",
                    line=3.0,
                    price_american=-105,
                    implied_probability=0.512,
                    collected_at_utc=target_start - timedelta(minutes=30),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="team_totals",
                    provider_market_key="team_totals",
                    entity_type="team",
                    team_id=away.id,
                    participant_name="Away Bats",
                    side="under",
                    line=3.0,
                    price_american=-115,
                    implied_probability=0.535,
                    collected_at_utc=target_start - timedelta(minutes=30),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="pitcher_strikeouts",
                    provider_market_key="pitcher_strikeouts",
                    entity_type="player",
                    player_id=home_starter.id,
                    participant_name="Home Starter",
                    side="over",
                    line=4.5,
                    price_american=-102,
                    implied_probability=0.505,
                    collected_at_utc=target_start - timedelta(hours=2),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="pitcher_strikeouts",
                    provider_market_key="pitcher_strikeouts",
                    entity_type="player",
                    player_id=home_starter.id,
                    participant_name="Home Starter",
                    side="under",
                    line=4.5,
                    price_american=-118,
                    implied_probability=0.541,
                    collected_at_utc=target_start - timedelta(hours=2),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="pitcher_strikeouts",
                    provider_market_key="pitcher_strikeouts",
                    entity_type="player",
                    player_id=home_starter.id,
                    participant_name="Home Starter",
                    side="over",
                    line=5.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=target_start - timedelta(minutes=45),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="pitcher_strikeouts",
                    provider_market_key="pitcher_strikeouts",
                    entity_type="player",
                    player_id=home_starter.id,
                    participant_name="Home Starter",
                    side="under",
                    line=5.5,
                    price_american=-120,
                    implied_probability=0.545,
                    collected_at_utc=target_start - timedelta(minutes=45),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=prior_home.id,
                    book="draftkings",
                    market_key="pitcher_strikeouts",
                    provider_market_key="pitcher_strikeouts",
                    entity_type="player",
                    player_id=home_starter.id,
                    participant_name="Home Starter",
                    side="over",
                    line=6.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=2),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=prior_home.id,
                    book="draftkings",
                    market_key="pitcher_strikeouts",
                    provider_market_key="pitcher_strikeouts",
                    entity_type="player",
                    player_id=home_starter.id,
                    participant_name="Home Starter",
                    side="under",
                    line=6.5,
                    price_american=-110,
                    implied_probability=0.524,
                    collected_at_utc=prior_start - timedelta(hours=2),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="batter_hits",
                    provider_market_key="batter_hits",
                    entity_type="player",
                    player_id=away_bat.id,
                    participant_name="Away Bat",
                    side="over",
                    line=0.5,
                    price_american=-130,
                    implied_probability=0.565,
                    collected_at_utc=target_start - timedelta(hours=2),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="batter_hits",
                    provider_market_key="batter_hits",
                    entity_type="player",
                    player_id=away_bat.id,
                    participant_name="Away Bat",
                    side="under",
                    line=0.5,
                    price_american=100,
                    implied_probability=0.5,
                    collected_at_utc=target_start - timedelta(hours=2),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="batter_hits",
                    provider_market_key="batter_hits",
                    entity_type="player",
                    player_id=away_bat.id,
                    participant_name="Away Bat",
                    side="over",
                    line=1.5,
                    price_american=100,
                    implied_probability=0.5,
                    collected_at_utc=target_start - timedelta(minutes=45),
                    source="the_odds_api_event_odds",
                ),
                EventOddsQuote(
                    event_id=target.id,
                    book="draftkings",
                    market_key="batter_hits",
                    provider_market_key="batter_hits",
                    entity_type="player",
                    player_id=away_bat.id,
                    participant_name="Away Bat",
                    side="under",
                    line=1.5,
                    price_american=-120,
                    implied_probability=0.545,
                    collected_at_utc=target_start - timedelta(minutes=45),
                    source="the_odds_api_event_odds",
                ),
                MlbTeamGameLog(
                    event_id=prior_home.id,
                    team_id=home.id,
                    game_date_utc=prior_start,
                    is_home=True,
                    opponent_team_id=other.id,
                    runs_for=5,
                    runs_against=2,
                    hits=9,
                    at_bats=34,
                    home_runs=2,
                    base_on_balls=4,
                    strike_outs=8,
                    stolen_bases=1,
                    bullpen_outs=12,
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
                    hits=7,
                    at_bats=32,
                    home_runs=1,
                    base_on_balls=2,
                    strike_outs=10,
                    stolen_bases=0,
                    bullpen_outs=9,
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
                    innings_pitched_outs=18,
                    pitching_hits=6,
                    earned_runs=2,
                    pitching_base_on_balls=1,
                    pitching_strike_outs=7,
                    pitching_home_runs=1,
                    pitches_thrown=95,
                    source="mlb_stats_api",
                ),
                MlbPlayerGameLog(
                    event_id=prior_away.id,
                    player_id=away_starter.id,
                    team_id=away.id,
                    game_date_utc=prior_start,
                    is_home=False,
                    pitching_started=True,
                    innings_pitched_outs=15,
                    pitching_hits=8,
                    earned_runs=3,
                    pitching_base_on_balls=2,
                    pitching_strike_outs=5,
                    pitching_home_runs=2,
                    pitches_thrown=88,
                    source="mlb_stats_api",
                ),
                MlbPlayerGameLog(
                    event_id=prior_home.id,
                    player_id=home_bat.id,
                    team_id=home.id,
                    game_date_utc=prior_start,
                    is_home=True,
                    batting_started=True,
                    batting_order=3,
                    at_bats=4,
                    hits=2,
                    doubles=1,
                    home_runs=1,
                    rbi=3,
                    base_on_balls=1,
                    strike_outs=1,
                    source="mlb_stats_api",
                ),
                MlbPlayerGameLog(
                    event_id=prior_away.id,
                    player_id=away_bat.id,
                    team_id=away.id,
                    game_date_utc=prior_start,
                    is_home=False,
                    batting_started=True,
                    batting_order=2,
                    at_bats=5,
                    hits=3,
                    doubles=1,
                    rbi=2,
                    base_on_balls=1,
                    strike_outs=0,
                    source="mlb_stats_api",
                ),
                MlbPlayerGameLog(
                    event_id=prior_home.id,
                    player_id=home_reliever.id,
                    team_id=home.id,
                    game_date_utc=prior_start,
                    is_home=True,
                    pitching_started=False,
                    innings_pitched_outs=4,
                    earned_runs=0,
                    pitching_base_on_balls=1,
                    pitching_strike_outs=2,
                    pitches_thrown=24,
                    source="mlb_stats_api",
                ),
                MlbPlayerGameLog(
                    event_id=prior_away.id,
                    player_id=away_reliever.id,
                    team_id=away.id,
                    game_date_utc=prior_start,
                    is_home=False,
                    pitching_started=False,
                    innings_pitched_outs=5,
                    earned_runs=1,
                    pitching_base_on_balls=0,
                    pitching_strike_outs=3,
                    pitching_home_runs=1,
                    pitches_thrown=31,
                    source="mlb_stats_api",
                ),
            ]
        )
        session.commit()

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        "dk_ncaab.analysis.mlb_market_readiness.read_latest_entry_ev",
        lambda: {
            "sport": "baseball_mlb",
            "anchor": "T60",
            "generated_at_utc": now.isoformat(),
            "rows_predicted_by_market": {
                "moneyline": 60,
                "spread": 40,
                "total": 55,
                "team_totals": 24,
                "pitcher_strikeouts": 12,
                "batter_hits": 0,
            },
            "recommended_by_market": {
                "moneyline": 3,
                "team_totals": 2,
                "pitcher_strikeouts": 1,
            },
        },
    )
    try:
        client = TestClient(app)
        response = client.get("/analysis/mlb/readiness?days_back=3&days_forward=3")
        assert response.status_code == 200
        payload = response.json()

        summary = payload["summary"]
        assert summary["visible_events"] == 3
        assert summary["events_with_pregame_odds"] == 3
        assert summary["events_with_both_team_history"] == 1
        assert summary["events_with_both_starter_history"] == 1
        assert summary["pending_pregame_events"] == 1
        assert summary["ready_after_settlement_events"] == 1

        target_row = next(row for row in payload["events"] if row["event_id"] == target.id)
        assert target_row["ready_after_settlement"] is True
        assert target_row["gaps"] == ["awaiting_settlement"]
        assert target_row["home_starter"]["player_name"] == "Home Starter"
        assert target_row["away_starter"]["prior_starts"] == 1

        research = client.get(f"/events/{target.id}/research")
        assert research.status_code == 200
        research_payload = research.json()
        market_selections = {row["selection"] for row in research_payload["market_context"]}
        assert "Home Bats moneyline" in market_selections
        total_market = next(
            row for row in research_payload["market_context"]
            if row["market"] == "total" and row["side"] == "over"
        )
        assert total_market["record_vs_current_line_last_n"] == "0-2-0"
        assert total_market["avg_margin_vs_current_line_last_n"] == -1.5
        assert total_market["record_vs_market_line_last_n"] == "0-2-0"
        assert total_market["avg_margin_vs_market_line_last_n"] == -1.0
        assert len(total_market["recent_results_vs_market_lines"]) == 2
        home_moneyline = next(
            row for row in research_payload["market_context"]
            if row["market"] == "moneyline" and row["side"] == "home"
        )
        assert home_moneyline["recent_record_last_n"] == "1-0"
        assert home_moneyline["recent_win_rate_last_n"] == 1.0
        assert home_moneyline["avg_market_price_american_last_n"] == -130
        assert home_moneyline["current_implied_delta_vs_avg_last_n"] == -0.02
        assert home_moneyline["recent_results_vs_market_prices"][0]["result"] == "W"
        evidence_by_market = {
            (row["market"], row["side"], row.get("participant_name")): row
            for row in research_payload["line_evidence_status"]
        }
        total_evidence = evidence_by_market[("total", "over", "Over")]
        assert total_evidence["focus_key"] == "total:over:over"
        assert total_evidence["oof_predicted_rows"] == 55
        assert total_evidence["evidence_tier"] == "thin_validated"
        assert total_evidence["line_lifecycle_status"] == "current"
        home_ml_evidence = evidence_by_market[("moneyline", "home", "Home Bats moneyline")]
        assert home_ml_evidence["current_price_american"] == -120
        assert home_ml_evidence["posted_line_sample_size"] == 1
        assert research_payload["team_trends"]["home"]["avg_runs_for_l5"] == 5.0
        assert research_payload["team_trends"]["away"]["run_diff_l5"] == 1.0
        assert research_payload["team_trends"]["home"]["avg_hits_l5"] == 9.0
        assert research_payload["team_trends"]["away"]["batting_avg_l5"] == 0.219
        assert research_payload["starter_context"]["home"]["player_name"] == "Home Starter"
        assert research_payload["starter_context"]["away"]["prior_starts"] == 1
        assert research_payload["starter_context"]["home"]["days_rest"] == 3
        assert research_payload["starter_context"]["away"]["avg_home_runs_allowed_l3"] == 2.0
        team_line_evidence = {
            row["team_name"]: row for row in research_payload["team_line_evidence"]
        }
        assert team_line_evidence["Home Bats"]["line_source"] == "draftkings_team_total_market"
        assert team_line_evidence["Home Bats"]["current_team_total"] == 5.5
        assert team_line_evidence["Home Bats"]["open_team_total"] == 5.0
        assert team_line_evidence["Home Bats"]["best_entry_anchor"] == "OPEN"
        assert team_line_evidence["Home Bats"]["number_move_from_open"] == 0.5
        assert team_line_evidence["Home Bats"]["over_price_move_american_from_open"] == -10
        assert len(team_line_evidence["Home Bats"]["history_points"]) == 2
        assert team_line_evidence["Home Bats"]["avg_runs_vs_close_implied_last_n"] == 0.0
        assert team_line_evidence["Home Bats"]["posted_line_games_sampled"] == 1
        assert team_line_evidence["Home Bats"]["record_vs_current_line_last_n"] == "0-1-0"
        assert team_line_evidence["Home Bats"]["avg_margin_vs_current_line_last_n"] == -0.5
        assert team_line_evidence["Home Bats"]["recent_results_vs_current_line"][0]["result"] == "U"
        assert team_line_evidence["Home Bats"]["record_vs_market_line_last_n"] == "1-0-0"
        assert team_line_evidence["Home Bats"]["avg_margin_vs_market_line_last_n"] == 0.5
        assert team_line_evidence["Home Bats"]["recent_results_vs_market_lines"][0]["line"] == 4.5
        assert team_line_evidence["Home Bats"]["settled_market_history"][0]["event_id"] == prior_home.id
        assert team_line_evidence["Home Bats"]["settled_market_history"][0]["over_price_american"] == -110
        assert team_line_evidence["Away Bats"]["current_team_total"] == 3.0
        team_total_evidence = evidence_by_market[("team_totals", "over", "Home Bats")]
        assert team_total_evidence["focus_key"] == "team_totals:over:home-bats"
        assert team_total_evidence["current_line"] == 5.5
        assert team_total_evidence["oof_predicted_rows"] == 24
        assert team_total_evidence["posted_line_sample_size"] == 1
        recent_home_event_ids = {
            row["event_id"] for row in research_payload["team_metrics"]["home"]["recent_games"]
        }
        assert future_home.id not in recent_home_event_ids
        assert target.id not in recent_home_event_ids
        assert research_payload["player_stats"][0]["player_name"] == "Away Starter"
        hitter_names = {
            row["player_name"]
            for row in research_payload["player_stats"]
            if row.get("role") == "recent_hitter"
        }
        assert hitter_names == {"Away Bat", "Home Bat"}
        prop_markets = {row["market_key"] for row in research_payload["player_prop_insights"]}
        assert prop_markets == {"pitcher_strikeouts", "batter_hits"}
        starter_prop = next(
            row for row in research_payload["player_prop_insights"]
            if row["market_key"] == "pitcher_strikeouts"
        )
        assert starter_prop["open_line"] == 4.5
        assert starter_prop["best_entry_anchor"] == "OPEN"
        assert starter_prop["best_entry_line"] == 4.5
        assert starter_prop["number_move_from_open"] == 1.0
        assert starter_prop["over_price_move_american_from_open"] == -8
        assert starter_prop["avg_last_n"] == 7.0
        assert starter_prop["posted_line_games_sampled"] == 1
        assert starter_prop["hit_rate_over_last_n"] == 1.0
        assert starter_prop["recent_results"][0]["value"] == 7.0
        assert starter_prop["record_vs_current_line_last_n"] == "1-0-0"
        assert starter_prop["avg_margin_vs_current_line_last_n"] == 1.5
        assert starter_prop["recent_results_vs_current_line"][0]["result"] == "O"
        assert starter_prop["record_vs_market_line_last_n"] == "1-0-0"
        assert starter_prop["avg_margin_vs_market_line_last_n"] == 0.5
        assert starter_prop["recent_results_vs_market_lines"][0]["line"] == 6.5
        assert starter_prop["settled_market_history"][0]["event_id"] == prior_home.id
        assert starter_prop["settled_market_history"][0]["under_price_american"] == -110
        assert len(starter_prop["history_points"]) == 2
        starter_prop_evidence = evidence_by_market[("pitcher_strikeouts", "over", "Home Starter")]
        assert starter_prop_evidence["focus_key"] == "pitcher_strikeouts:over:home-starter"
        assert starter_prop_evidence["current_line"] == 5.5
        assert starter_prop_evidence["oof_recommended_rows"] == 1
        thesis_by_key = {row["focus_key"]: row for row in research_payload["line_thesis"]}
        starter_thesis = thesis_by_key["pitcher_strikeouts:over:home-starter"]
        assert starter_thesis["action_status"] == "thin validated"
        assert starter_thesis["current_summary"] == "O 5.5 -110"
        assert starter_thesis["line_quality_score"] >= 70
        assert "Grow settled priced sample" in starter_thesis["next_step"]
        total_thesis = thesis_by_key["total:over:over"]
        assert total_thesis["history_summary"] == "0-2-0 vs today's line; 0-2-0 vs posted lines"
        assert research_payload["matchup_snapshot"]
        assert any(
            row["metric"] == "AVG L5" and row["away_value"] == 0.219
            for row in research_payload["matchup_snapshot"]
        )
        bullpen_names = {row["pitcher_name"] for row in research_payload["bullpen_usage"]}
        assert bullpen_names == {"Away Reliever", "Home Reliever"}
        factor_names = {row["factor"] for row in research_payload["why_this_line"]}
        assert "Market Pressure" in factor_names
        assert "Starting Pitching" in factor_names
        assert "Team Form" in factor_names
        assert "weather_wind_pending" in research_payload["data_gaps"]
        assert "park_factor_source_pending" in research_payload["data_gaps"]
    finally:
        app.dependency_overrides.clear()


def test_mlb_readiness_separates_settled_quoted_from_trainable():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    target_start = now - timedelta(days=1)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()

        home = Team(league_id=league.id, name="Home Club", normalized_name="home club")
        away = Team(league_id=league.id, name="Away Club", normalized_name="away club")
        session.add_all([home, away])
        session.flush()

        event = Event(
            league_id=league.id,
            external_event_key="final-1",
            start_time_utc=target_start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="final",
        )
        session.add(event)
        session.flush()

        session.add_all(
            [
                EventProviderKey(
                    event_id=event.id,
                    sport_key="baseball_mlb",
                    provider="mlb_stats_api",
                    provider_event_key="provider-final-1",
                ),
                OddsQuote(
                    event_id=event.id,
                    book="draftkings",
                    market="moneyline",
                    side="home",
                    line=None,
                    price_american=-120,
                    implied_probability=0.545,
                    collected_at_utc=target_start - timedelta(hours=1),
                    source="the_odds_api",
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
        response = client.get("/analysis/mlb/readiness?days_back=3&days_forward=2")
        assert response.status_code == 200
        payload = response.json()
        summary = payload["summary"]

        assert summary["visible_events"] == 1
        assert summary["events_with_pregame_odds"] == 1
        assert summary["settled_quoted_events"] == 1
        assert summary["settled_trainable_events"] == 0
        assert payload["warnings"] == [
            "No settled MLB events with pregame odds are modelable yet."
        ]
    finally:
        app.dependency_overrides.clear()
