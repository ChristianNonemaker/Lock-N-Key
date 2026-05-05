from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app, get_db
from dk_ncaab.db.models import Base, Event, League, OddsQuote, Team


def test_empty_sqlite_api_baseline_no_network():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)

        docs = client.get("/docs")
        assert docs.status_code == 404

        status = client.get("/status")
        assert status.status_code == 200
        assert status.json()["events_total"] == 0
        assert status.json()["settled_events_with_pregame_odds"] == 0
        assert status.json()["strict_entry_ev_events_modelable"] >= 0

        board = client.get("/board?sport=basketball_ncaab&mode=today&limit=5")
        assert board.status_code == 200
        assert board.json()["games"] == []

        single = client.get("/events/1/research")
        assert single.status_code == 404

        batch = client.get("/events/research?event_ids=1")
        assert batch.status_code == 200
        assert batch.json()["events"] == []
        assert batch.json()["warnings"]

        ev = client.get("/analysis/entry-ev/latest")
        assert ev.status_code == 200
        assert "available" in ev.json()

        mlb = client.get("/analysis/mlb/readiness")
        assert mlb.status_code == 200
        assert mlb.json()["events"] == []

        market_readiness = client.get("/analysis/mlb/market-readiness")
        assert market_readiness.status_code == 200
        assert market_readiness.json()["markets"] == []

        registry = client.get("/registry/props?sport=baseball_mlb")
        assert registry.status_code == 200
        assert registry.json()["count"] >= 1
    finally:
        app.dependency_overrides.clear()


def test_board_returns_lifecycle_fields_for_best_entry_and_moves():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()

        home = Team(league_id=league.id, name="Cubs", normalized_name="cubs")
        away = Team(league_id=league.id, name="Cardinals", normalized_name="cardinals")
        session.add_all([home, away])
        session.flush()

        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = now + timedelta(minutes=20)
        event = Event(
            league_id=league.id,
            external_event_key="board-1",
            start_time_utc=start,
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        session.add(event)
        session.flush()

        quotes = [
            OddsQuote(
                event_id=event.id,
                book="draftkings",
                market="spread",
                side="home",
                line=-1.5,
                price_american=-110,
                implied_probability=0.5238,
                collected_at_utc=now - timedelta(hours=2),
                source="test",
            ),
            OddsQuote(
                event_id=event.id,
                book="draftkings",
                market="spread",
                side="home",
                line=-2.0,
                price_american=-118,
                implied_probability=0.5413,
                collected_at_utc=now - timedelta(minutes=50),
                source="test",
            ),
            OddsQuote(
                event_id=event.id,
                book="draftkings",
                market="spread",
                side="home",
                line=-2.5,
                price_american=-125,
                implied_probability=0.5556,
                collected_at_utc=now - timedelta(minutes=15),
                source="test",
            ),
            OddsQuote(
                event_id=event.id,
                book="draftkings",
                market="spread",
                side="home",
                line=-3.0,
                price_american=-130,
                implied_probability=0.5652,
                collected_at_utc=now - timedelta(minutes=2),
                source="test",
            ),
        ]
        session.add_all(quotes)
        session.commit()

    def override_db():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        board = client.get("/board?sport=baseball_mlb&mode=upcoming&limit=5")
        assert board.status_code == 200
        payload = board.json()
        assert payload["count"] == 1

        line = payload["games"][0]["lines"][0]
        assert line["book"] == "draftkings"
        assert line["best_entry_anchor"] == "T30"
        assert line["best_entry_line"] == -2.5
        assert line["best_entry_price_american"] == -125
        assert line["number_move_from_open"] == -1.5
        assert line["price_move_american_from_open"] == -20

        intelligence = payload["games"][0]["slate_intelligence"]
        assert intelligence["score"] >= 70
        assert intelligence["tier"] == "high_interest"
        assert intelligence["headline"] == "Open first"
        assert "current DK lines" in intelligence["reasons"]
        assert "market moved" in intelligence["reasons"]
        assert intelligence["strongest_move_label"] == "Cubs spread"
        assert intelligence["strongest_number_move"] == -1.5
        assert any(signal["label"] == "Strongest Move" for signal in intelligence["signals"])
    finally:
        app.dependency_overrides.clear()
