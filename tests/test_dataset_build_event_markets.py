from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.analysis.dataset_build import build_dataset
from dk_ncaab.db.models import (
    Base,
    Event,
    EventOddsQuote,
    EventResult,
    League,
    MlbPlayerGameLog,
    MlbTeamGameLog,
    Player,
    Team,
)
from dk_ncaab.etl.normalize import american_to_implied


def _quote(
    *,
    event_id: int,
    market_key: str,
    entity_type: str,
    participant_name: str,
    side: str,
    line: float,
    price: int,
    collected_at_utc: datetime,
    team_id: int | None = None,
    player_id: int | None = None,
) -> EventOddsQuote:
    return EventOddsQuote(
        event_id=event_id,
        book="draftkings",
        market_key=market_key,
        provider_market_key=market_key,
        entity_type=entity_type,
        team_id=team_id,
        player_id=player_id,
        participant_name=participant_name,
        side=side,
        line=line,
        price_american=price,
        implied_probability=american_to_implied(price),
        collected_at_utc=collected_at_utc,
        source="the_odds_api_event_odds",
    )


def test_build_dataset_enumerates_mlb_event_market_participants():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    start = datetime(2026, 4, 20, 23, 30, tzinfo=timezone.utc)
    quote_time = start - timedelta(hours=2)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        home = Team(league_id=league.id, name="Home Bats", normalized_name="home bats")
        away = Team(league_id=league.id, name="Away Arms", normalized_name="away arms")
        session.add_all([home, away])
        session.flush()
        event = Event(
            league_id=league.id,
            external_event_key="mlb-1",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="final",
        )
        session.add(event)
        session.flush()
        starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="starter-1",
            full_name="Home Starter",
        )
        session.add(starter)
        session.flush()
        session.add_all(
            [
                EventResult(event_id=event.id, home_score=5, away_score=3, status="final"),
                MlbTeamGameLog(
                    event_id=event.id,
                    team_id=home.id,
                    game_date_utc=start,
                    is_home=True,
                    opponent_team_id=away.id,
                    runs_for=5,
                    source="mlb_stats_api",
                ),
                MlbPlayerGameLog(
                    event_id=event.id,
                    player_id=starter.id,
                    team_id=home.id,
                    game_date_utc=start,
                    is_home=True,
                    pitching_started=True,
                    pitching_strike_outs=7,
                    source="mlb_stats_api",
                ),
                _quote(
                    event_id=event.id,
                    market_key="team_totals",
                    entity_type="team",
                    participant_name=home.name,
                    team_id=home.id,
                    side="over",
                    line=4.5,
                    price=-110,
                    collected_at_utc=quote_time,
                ),
                _quote(
                    event_id=event.id,
                    market_key="team_totals",
                    entity_type="team",
                    participant_name=home.name,
                    team_id=home.id,
                    side="under",
                    line=4.5,
                    price=-110,
                    collected_at_utc=quote_time,
                ),
                _quote(
                    event_id=event.id,
                    market_key="pitcher_strikeouts",
                    entity_type="player",
                    participant_name=starter.full_name,
                    player_id=starter.id,
                    side="over",
                    line=5.5,
                    price=-125,
                    collected_at_utc=quote_time,
                ),
                _quote(
                    event_id=event.id,
                    market_key="pitcher_strikeouts",
                    entity_type="player",
                    participant_name=starter.full_name,
                    player_id=starter.id,
                    side="under",
                    line=5.5,
                    price=105,
                    collected_at_utc=quote_time,
                ),
            ]
        )
        session.commit()

        df = build_dataset(session=session, event_ids=[event.id])

    event_rows = df[df["market"].isin(["team_totals", "pitcher_strikeouts"])]
    assert len(event_rows) == 4
    assert event_rows["participant_name"].notna().all()
    assert set(event_rows["participant_entity_type"]) == {"team", "player"}

    team_over = event_rows[
        (event_rows["market"] == "team_totals")
        & (event_rows["side"] == "over")
    ].iloc[0]
    assert team_over["participant_team_id"] == home.id
    assert team_over["participant_player_id"] != team_over["participant_player_id"]
    assert team_over["line_T60"] == 4.5
    assert team_over["total_over_T60"] == 1
    assert team_over["fair_implied_T60"] == pytest.approx(0.5)

    pitcher_under = event_rows[
        (event_rows["market"] == "pitcher_strikeouts")
        & (event_rows["side"] == "under")
    ].iloc[0]
    assert pitcher_under["participant_player_id"] == starter.id
    assert pitcher_under["line_T60"] == 5.5
    assert pitcher_under["total_over_T60"] == 0
