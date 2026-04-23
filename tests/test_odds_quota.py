from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.collectors import odds_api
from dk_ncaab.config.settings import OddsApiCfg
from dk_ncaab.db.models import Base, OddsApiUsage


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    sess = session_factory()
    yield sess
    sess.close()


def _usage(sport: str, requested_at: datetime, **kwargs) -> OddsApiUsage:
    return OddsApiUsage(
        requested_at_utc=requested_at,
        sport_key=sport,
        provider_sport_key=sport,
        endpoint=f"/sports/{sport}/odds",
        request_count=1,
        status_code=kwargs.get("status_code", 200),
        success=kwargs.get("success", True),
        requests_used=kwargs.get("requests_used"),
        requests_remaining=kwargs.get("requests_remaining"),
    )


def test_select_due_odds_sports_enforces_interval_and_limit(session):
    now = datetime(2026, 4, 20, 18, 0, tzinfo=timezone.utc)
    session.add(_usage("basketball_ncaab", now - timedelta(minutes=15)))
    session.add(_usage("baseball_mlb", now - timedelta(hours=4)))
    session.commit()

    due = odds_api.select_due_odds_sports(
        session,
        ["basketball_ncaab", "baseball_mlb", "americanfootball_nfl"],
        max_sports_per_run=1,
        min_interval_minutes=60,
        now=now,
    )

    assert due == ["americanfootball_nfl"]


def test_usage_summary_counts_current_month_and_headers(session):
    now = datetime(2026, 4, 20, 18, 0, tzinfo=timezone.utc)
    session.add(_usage("baseball_mlb", now - timedelta(days=1)))
    session.add(
        _usage(
            "basketball_ncaab",
            now,
            requests_used=179,
            requests_remaining=321,
        )
    )
    session.add(_usage("baseball_mlb", datetime(2026, 3, 31, 18, 0, tzinfo=timezone.utc)))
    session.commit()

    summary = odds_api.get_odds_usage_summary(
        session,
        monthly_budget=500,
        reserve_requests=50,
        now=now,
    )

    assert summary.recorded_requests_month == 2
    assert summary.requests_used == 179
    assert summary.requests_remaining == 321
    assert summary.requests_by_sport == {"baseball_mlb": 1, "basketball_ncaab": 1}
    assert summary.last_request_utc == now


def test_fetch_odds_records_one_usage_row(monkeypatch, session_factory):
    monkeypatch.setattr(odds_api, "SessionLocal", session_factory)
    cfg = OddsApiCfg(key="test-key", sports=["baseball_mlb"])
    monkeypatch.setattr(odds_api, "get_settings", lambda: SimpleNamespace(odds_api=cfg))

    class FakeResponse:
        status_code = 200
        is_success = True
        headers = {"x-requests-used": "12", "x-requests-remaining": "488"}

        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": "evt"}]

    class FakeClient:
        def get(self, url, params, timeout):
            assert url.endswith("/sports/baseball_mlb/odds")
            assert params["apiKey"] == "test-key"
            assert timeout == 30
            return FakeResponse()

    assert odds_api._fetch_odds(FakeClient(), "baseball_mlb") == [{"id": "evt"}]

    with session_factory() as sess:
        rows = sess.query(OddsApiUsage).all()
        assert len(rows) == 1
        assert rows[0].sport_key == "baseball_mlb"
        assert rows[0].request_count == 1
        assert rows[0].requests_used == 12
        assert rows[0].requests_remaining == 488
        assert rows[0].success is True


def test_collect_odds_skips_before_http_when_sport_not_due(monkeypatch, session_factory):
    now = datetime.now(timezone.utc)
    with session_factory() as sess:
        sess.add(_usage("baseball_mlb", now - timedelta(minutes=5)))
        sess.commit()

    monkeypatch.setattr(odds_api, "SessionLocal", session_factory)
    cfg = OddsApiCfg(
        key="test-key",
        sports=["baseball_mlb"],
        max_sports_per_run=1,
        min_interval_minutes=60,
    )
    monkeypatch.setattr(odds_api, "get_settings", lambda: SimpleNamespace(odds_api=cfg))

    def fail_fetch(*args, **kwargs):
        raise AssertionError("HTTP fetch should not be reached")

    monkeypatch.setattr(odds_api, "_fetch_with_backoff", fail_fetch)

    assert odds_api.collect_odds() == 0


def test_collect_odds_skips_before_http_when_reserve_reached(monkeypatch, session_factory):
    now = datetime.now(timezone.utc)
    with session_factory() as sess:
        sess.add(
            _usage(
                "baseball_mlb",
                now - timedelta(hours=2),
                requests_used=450,
                requests_remaining=50,
            )
        )
        sess.commit()

    monkeypatch.setattr(odds_api, "SessionLocal", session_factory)
    cfg = OddsApiCfg(
        key="test-key",
        sports=["baseball_mlb"],
        reserve_requests=50,
        max_sports_per_run=1,
        min_interval_minutes=60,
    )
    monkeypatch.setattr(odds_api, "get_settings", lambda: SimpleNamespace(odds_api=cfg))

    def fail_fetch(*args, **kwargs):
        raise AssertionError("HTTP fetch should not be reached")

    monkeypatch.setattr(odds_api, "_fetch_with_backoff", fail_fetch)

    assert odds_api.collect_odds() == 0


def test_collect_odds_passes_configured_request_attempt_cap(monkeypatch, session_factory):
    monkeypatch.setattr(odds_api, "SessionLocal", session_factory)
    cfg = OddsApiCfg(
        key="test-key",
        sports=["baseball_mlb"],
        max_sports_per_run=1,
        min_interval_minutes=60,
        max_request_attempts=1,
    )
    monkeypatch.setattr(odds_api, "get_settings", lambda: SimpleNamespace(odds_api=cfg))

    seen: dict[str, int] = {}

    def fake_fetch(_client, sport, max_retries):
        seen["sport"] = sport
        seen["max_retries"] = max_retries
        return []

    monkeypatch.setattr(odds_api, "_fetch_with_backoff", fake_fetch)

    assert odds_api.collect_odds() == 0
    assert seen == {"sport": "baseball_mlb", "max_retries": 1}
