from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.collectors.mlb_environment import collect_mlb_environment
from dk_ncaab.db.models import (
    Base,
    Event,
    League,
    MlbEnvironmentSnapshot,
    MlbEventVenue,
    MlbVenue,
    Team,
)


class FakeNwsClient:
    def __init__(self):
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=30):
        self.calls.append(url)
        request = httpx.Request("GET", url)
        if "/points/" in url:
            assert headers["User-Agent"]
            return httpx.Response(
                200,
                json={"properties": {"forecastHourly": "https://api.weather.gov/gridpoints/LOT/1,1/forecast/hourly"}},
                request=request,
            )
        if url.endswith("/forecast/hourly"):
            return httpx.Response(
                200,
                json={
                    "properties": {
                        "periods": [
                            {
                                "startTime": "2026-04-23T23:00:00+00:00",
                                "endTime": "2026-04-24T00:00:00+00:00",
                                "temperature": 61,
                                "windSpeed": "10 to 15 mph",
                                "windDirection": "NW",
                                "shortForecast": "Mostly Clear",
                                "probabilityOfPrecipitation": {"value": 12},
                            }
                        ]
                    }
                },
                request=request,
            )
        raise AssertionError(f"Unexpected URL: {url}")

    def close(self):
        return None


def test_collect_mlb_environment_writes_append_only_weather_snapshot():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    fixed_now = datetime.now(timezone.utc).replace(microsecond=0)

    with Session() as session:
        league = League(key="mlb", name="MLB")
        session.add(league)
        session.flush()
        home = Team(league_id=league.id, name="Home", normalized_name="home")
        away = Team(league_id=league.id, name="Away", normalized_name="away")
        session.add_all([home, away])
        session.flush()
        event = Event(
            league_id=league.id,
            external_event_key="env-test",
            start_time_utc=fixed_now + timedelta(hours=3),
            home_team_id=home.id,
            away_team_id=away.id,
            status="upcoming",
        )
        venue = MlbVenue(
            provider="mlb_stats_api",
            provider_venue_key="17",
            name="Wrigley Field",
            latitude=41.9484,
            longitude=-87.6553,
            roof_type="open",
            orientation_deg=135.0,
            weather_exposure_rule="open_air",
            wind_reliable_flag=True,
            source="test",
        )
        session.add_all([event, venue])
        session.flush()
        session.add(
            MlbEventVenue(
                event_id=event.id,
                venue_id=venue.id,
                provider="mlb_stats_api",
                collected_at_utc=fixed_now,
            )
        )
        session.commit()

        result = collect_mlb_environment(
            max_events=1,
            request_delay_sec=0,
            client=FakeNwsClient(),
            session=session,
        )

        assert result.events_considered == 1
        assert result.snapshots_created == 1
        snap = session.query(MlbEnvironmentSnapshot).one()
        assert snap.temperature_f == 61.0
        assert snap.wind_mph == 15.0
        assert snap.wind_direction == "NW"
        assert snap.wind_from_degrees == 315.0
        assert snap.field_wind_label == "blowing out"
        assert snap.wind_out_mph == 15.0
        assert snap.wind_in_mph == 0.0
        assert snap.crosswind_mph == 0.0
        assert snap.precipitation_chance == 12.0
        assert snap.conditions == "Mostly Clear"
