from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.collectors.event_odds_identity import reconcile_event_odds_identities
from dk_ncaab.db.models import (
    Base,
    Event,
    EventOddsQuote,
    League,
    MlbPlayerGameLog,
    MlbProbableStarter,
    Player,
    Team,
)


def _quote(
    event_id: int,
    *,
    participant_name: str,
    market_key: str = "pitcher_strikeouts",
) -> EventOddsQuote:
    return EventOddsQuote(
        event_id=event_id,
        book="draftkings",
        market_key=market_key,
        provider_market_key=market_key,
        entity_type="player",
        participant_name=participant_name,
        side="over",
        line=5.5,
        price_american=-110,
        implied_probability=0.52381,
        collected_at_utc=datetime(2026, 5, 3, 16, tzinfo=timezone.utc),
        source="the_odds_api_event_odds",
    )


def test_reconcile_event_odds_identities_dry_run_then_apply():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    start = datetime(2026, 5, 3, 19, 10, tzinfo=timezone.utc)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        home = Team(league_id=league.id, name="Colorado Rockies", normalized_name="colorado rockies")
        away = Team(league_id=league.id, name="Atlanta Braves", normalized_name="atlanta braves")
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
        strider = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="675911",
            full_name="Spencer Strider",
            primary_position="P",
        )
        session.add(strider)
        session.flush()
        session.add(
            MlbProbableStarter(
                event_id=event.id,
                team_id=away.id,
                player_id=strider.id,
                is_home=False,
                source="schedule",
                collected_at_utc=start - timedelta(hours=5),
            )
        )
        session.add(
            MlbPlayerGameLog(
                event_id=event.id,
                player_id=strider.id,
                team_id=away.id,
                game_date_utc=start,
                is_home=False,
                pitching_started=True,
                pitching_strike_outs=7,
                source="mlb_stats_api",
            )
        )
        session.add_all(
            [
                _quote(event.id, participant_name="Spencer Strider"),
                _quote(event.id, participant_name="Joe Mack", market_key="batter_hits"),
            ]
        )
        session.commit()

    with Session() as session:
        dry_run = reconcile_event_odds_identities(session=session)
        assert dry_run.scanned == 2
        assert dry_run.resolvable == 1
        assert dry_run.updated == 0
        assert dry_run.unresolved == 1
        assert dry_run.resolutions[0].participant_name == "Spencer Strider"
        assert dry_run.resolutions[0].method == "probable_starter"
        assert session.query(EventOddsQuote).filter(EventOddsQuote.player_id.isnot(None)).count() == 0

    with Session() as session:
        applied = reconcile_event_odds_identities(session=session, apply=True)
        assert applied.updated == 1
        linked = (
            session.query(EventOddsQuote)
            .filter(EventOddsQuote.participant_name == "Spencer Strider")
            .one()
        )
        unresolved = session.query(EventOddsQuote).filter(EventOddsQuote.participant_name == "Joe Mack").one()
        assert linked.player_id is not None
        assert unresolved.player_id is None
