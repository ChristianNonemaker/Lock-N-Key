from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app, get_db
from dk_ncaab.db.models import Base


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

        status = client.get("/status")
        assert status.status_code == 200
        assert status.json()["events_total"] == 0

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
    finally:
        app.dependency_overrides.clear()
