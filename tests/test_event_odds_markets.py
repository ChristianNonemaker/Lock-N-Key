from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.collectors import odds_event_markets
from dk_ncaab.config.settings import OddsApiCfg
from dk_ncaab.db.models import (
    Base,
    Event,
    EventOddsQuote,
    EventProviderKey,
    League,
    MlbPlayerGameLog,
    MlbProbableStarter,
    Player,
    Team,
)


def test_collect_event_odds_markets_inserts_team_and_player_quotes(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now + timedelta(hours=4)
    provider_last_update = (now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")

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
            external_event_key="evt-1",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        session.add(event)
        session.flush()
        session.add(
            EventProviderKey(
                event_id=event.id,
                sport_key="baseball_mlb",
                provider="odds_api",
                provider_event_key="odds-1",
            )
        )

        starter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="starter-1",
            full_name="Home Starter",
            primary_position="P",
        )
        hitter = Player(
            league_id=league.id,
            provider="mlb_stats_api",
            external_player_key="hitter-1",
            full_name="Away Bat",
            primary_position="OF",
        )
        session.add_all([starter, hitter])
        session.flush()
        session.add(
            MlbProbableStarter(
                event_id=event.id,
                team_id=home.id,
                player_id=starter.id,
                is_home=True,
                source="schedule",
                collected_at_utc=now,
            )
        )
        session.add(
            MlbPlayerGameLog(
                event_id=event.id,
                player_id=hitter.id,
                team_id=away.id,
                game_date_utc=now - timedelta(days=1),
                is_home=False,
                batting_started=True,
                at_bats=4,
                hits=2,
                home_runs=1,
                source="mlb_stats_api",
            )
        )
        session.commit()

    monkeypatch.setattr(odds_event_markets, "SessionLocal", Session)
    monkeypatch.setattr(
        odds_event_markets,
        "get_settings",
        lambda: SimpleNamespace(
            odds_api=OddsApiCfg(
                key="test-key",
                sports=["baseball_mlb"],
                reserve_requests=50,
            )
        ),
    )

    def fake_fetch_event_odds(client, *, sport_key: str, provider_event_key: str, market_keys: list[str]):
        assert sport_key == "baseball_mlb"
        assert provider_event_key == "odds-1"
        assert "team_totals" in market_keys
        return (
            {
                "bookmakers": [
                    {
                        "key": "draftkings",
                        "markets": [
                            {
                                "key": "team_totals",
                                "last_update": provider_last_update,
                                "outcomes": [
                                    {"name": "Over", "description": "Home Bats", "price": -115, "point": 4.5},
                                    {"name": "Under", "description": "Home Bats", "price": -105, "point": 4.5},
                                ],
                            },
                            {
                                "key": "pitcher_strikeouts",
                                "last_update": provider_last_update,
                                "outcomes": [
                                    {"name": "Over", "description": "Home Starter", "price": -110, "point": 5.5},
                                    {"name": "Under", "description": "Home Starter", "price": -120, "point": 5.5},
                                ],
                            },
                            {
                                "key": "batter_hits",
                                "last_update": provider_last_update,
                                "outcomes": [
                                    {"name": "Over", "description": "Away Bat", "price": +100, "point": 1.5},
                                    {"name": "Under", "description": "Away Bat", "price": -120, "point": 1.5},
                                ],
                            },
                        ],
                    }
                ]
            },
            12,
            488,
        )

    monkeypatch.setattr(odds_event_markets, "_fetch_event_odds", fake_fetch_event_odds)

    summary = odds_event_markets.collect_event_odds_markets(
        sport_key="baseball_mlb",
        max_events=1,
        lookahead_hours=24,
        stale_after_minutes=60,
    )

    assert summary.events_fetched == 1
    assert summary.rows_inserted == 6
    assert summary.requests_remaining == 488

    with Session() as session:
        rows = session.query(EventOddsQuote).order_by(EventOddsQuote.market_key, EventOddsQuote.side).all()
        assert len(rows) == 6
        team_total = next(row for row in rows if row.market_key == "team_totals" and row.side == "over")
        pitcher = next(row for row in rows if row.market_key == "pitcher_strikeouts" and row.side == "over")
        hitter_row = next(row for row in rows if row.market_key == "batter_hits" and row.side == "over")
        assert team_total.team_id is not None
        assert pitcher.player_id is not None
        assert hitter_row.player_id is not None
