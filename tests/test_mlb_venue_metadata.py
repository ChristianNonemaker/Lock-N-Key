from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.main import _environment_context_for_event
from dk_ncaab.collectors.mlb_venue_metadata import (
    export_mlb_venue_metadata_template_csv,
    import_mlb_venue_metadata_csv,
)
from dk_ncaab.db.models import (
    Base,
    Event,
    League,
    MlbEnvironmentSnapshot,
    MlbEventVenue,
    MlbVenue,
    Team,
)


def _csv_path() -> Path:
    path = Path("artifacts/test_tmp/venue_metadata")
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{uuid4().hex}.csv"


def test_export_mlb_venue_metadata_template_writes_fillable_csv():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    csv_path = _csv_path()
    try:
        with Session() as session:
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
                notes="Existing reviewed row.",
            )
            session.add(venue)
            session.commit()

            result = export_mlb_venue_metadata_template_csv(csv_path, session=session)
            assert result.rows_written >= 30
            text = csv_path.read_text(encoding="utf-8")
            assert "venue_name" in text
            assert "Wrigley Field" in text
            assert "135.0" in text
    finally:
        csv_path.unlink(missing_ok=True)


def test_import_mlb_venue_metadata_updates_existing_venue_and_unblocks_field_wind():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    csv_path = _csv_path()
    csv_path.write_text(
        "\n".join(
            [
                "provider,provider_venue_key,venue_name,orientation_deg,weather_exposure_rule,wind_reliable_flag,orientation_source,orientation_source_url",
                (
                    "mlb_stats_api,17,Wrigley Field,135,open_air,true,"
                    "Baseball Almanac,https://www.baseball-almanac.com/stadium/ballpark_NSEW_AL.shtml"
                ),
            ]
        ),
        encoding="utf-8",
    )
    try:
        with Session() as session:
            league = League(key="mlb", name="MLB")
            session.add(league)
            session.flush()
            home = Team(league_id=league.id, name="Cubs", normalized_name="cubs")
            away = Team(league_id=league.id, name="Dodgers", normalized_name="dodgers")
            session.add_all([home, away])
            session.flush()
            event = Event(
                league_id=league.id,
                external_event_key="venue-metadata-test",
                start_time_utc=datetime.now(timezone.utc) + timedelta(hours=2),
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
                source="test",
            )
            session.add_all([event, venue])
            session.flush()
            session.add(
                MlbEventVenue(
                    event_id=event.id,
                    venue_id=venue.id,
                    provider="mlb_stats_api",
                    collected_at_utc=datetime.now(timezone.utc),
                )
            )
            session.add(
                MlbEnvironmentSnapshot(
                    event_id=event.id,
                    venue_id=venue.id,
                    provider="nws_api",
                    collected_at_utc=datetime.now(timezone.utc),
                    temperature_f=61.0,
                    wind_mph=15.0,
                    wind_direction="NW",
                    conditions="Mostly Clear",
                )
            )
            session.commit()

            result = import_mlb_venue_metadata_csv(csv_path, session=session)
            assert result.rows_read == 1
            assert result.rows_imported == 1
            updated = session.query(MlbVenue).one()
            assert updated.orientation_deg == 135.0
            assert updated.wind_reliable_flag is True
            assert "orientation_source=Baseball Almanac" in (updated.notes or "")

            context = _environment_context_for_event(session, event, "baseball_mlb")
            assert context.available is True
            assert context.field_wind_label == "blowing out"
            assert context.wind_out_mph == 15.0
            assert context.wind_in_mph == 0.0
    finally:
        csv_path.unlink(missing_ok=True)
