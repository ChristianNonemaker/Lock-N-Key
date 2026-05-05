from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dk_ncaab.collectors.mlb_park_factors import import_mlb_park_factors_csv
from dk_ncaab.db.models import Base, MlbParkFactor, MlbVenue


def _csv_path() -> Path:
    path = Path("artifacts/test_tmp/park_factors")
    path.mkdir(parents=True, exist_ok=True)
    return path / f"{uuid4().hex}.csv"


def test_import_mlb_park_factors_upserts_and_refreshes_latest_venue_values():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    csv_path = _csv_path()
    csv_path.write_text(
        "\n".join(
            [
                "venue_name,season,rolling_years,runs_factor,hr_factor,woba_factor,source,source_url",
                "Wrigley Field,2025,3,104,112,101,statcast_csv,https://example.test/park",
            ]
        ),
        encoding="utf-8",
    )
    try:
        with Session() as session:
            venue = MlbVenue(
                provider="mlb_stats_api",
                provider_venue_key="17",
                name="Wrigley Field",
                latitude=41.9484,
                longitude=-87.6553,
                roof_type="open",
                source="test",
            )
            session.add(venue)
            session.commit()

            result = import_mlb_park_factors_csv(csv_path, session=session)
            assert result.rows_read == 1
            assert result.rows_imported == 1
            assert result.venues_created == 0
            assert session.query(MlbParkFactor).count() == 1

            factor = session.query(MlbParkFactor).one()
            assert factor.venue_id == venue.id
            assert factor.season == 2025
            assert factor.rolling_years == 3
            assert factor.runs_factor == 104.0
            assert factor.hr_factor == 112.0
            assert factor.source == "statcast_csv"
            refreshed = session.get(MlbVenue, venue.id)
            assert refreshed.park_factor_runs == 104.0
            assert refreshed.park_factor_hr == 112.0

            csv_path.write_text(
                "\n".join(
                    [
                        "venue_name,season,rolling_years,runs_factor,hr_factor,woba_factor,source,source_url",
                        "Wrigley Field,2025,3,106,115,102,statcast_csv,https://example.test/park",
                    ]
                ),
                encoding="utf-8",
            )
            second = import_mlb_park_factors_csv(csv_path, session=session)
            assert second.rows_imported == 1
            assert session.query(MlbParkFactor).count() == 1
            assert session.query(MlbParkFactor).one().hr_factor == 115.0
            assert session.get(MlbVenue, venue.id).park_factor_runs == 106.0
    finally:
        csv_path.unlink(missing_ok=True)


def test_import_mlb_park_factors_can_create_curated_venue():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    csv_path = _csv_path()
    csv_path.write_text(
        "\n".join(
            [
                "park,year,run_factor,home_run_factor",
                "Fenway Park,2025,101,93",
            ]
        ),
        encoding="utf-8",
    )
    try:
        with Session() as session:
            result = import_mlb_park_factors_csv(csv_path, session=session)
            assert result.venues_created == 1
            venue = session.query(MlbVenue).one()
            assert venue.name == "Fenway Park"
            assert venue.latitude is not None
            assert venue.park_factor_runs == 101.0
            assert venue.park_factor_hr == 93.0
    finally:
        csv_path.unlink(missing_ok=True)


def test_import_mlb_park_factors_accepts_fangraphs_team_export_shape():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    csv_path = _csv_path()
    csv_path.write_text(
        "\n".join(
            [
                "Season,Team,Basic (5yr),3yr,1yr,1B,2B,3B,HR,SO,BB,GB,FB,LD,IFFB,FIP",
                "2025,Cubs,98,96,99,100,94,109,98,101,99,100,97,100,100,98",
            ]
        ),
        encoding="utf-8",
    )
    try:
        with Session() as session:
            result = import_mlb_park_factors_csv(
                csv_path,
                default_source="fangraphs_guts_pf",
                default_source_url="https://www.fangraphs.com/tools/guts?type=pf",
                session=session,
            )
            assert result.rows_read == 1
            assert result.rows_imported == 1
            assert result.venues_created == 1

            venue = session.query(MlbVenue).one()
            factor = session.query(MlbParkFactor).one()
            assert venue.name == "Wrigley Field"
            assert factor.season == 2025
            assert factor.rolling_years == 5
            assert factor.runs_factor == 98.0
            assert factor.hr_factor == 98.0
            assert factor.doubles_factor == 94.0
            assert factor.triples_factor == 109.0
            assert factor.source == "fangraphs_guts_pf"
            assert factor.source_url == "https://www.fangraphs.com/tools/guts?type=pf"
            assert "FanGraphs" in (factor.notes or "")
            assert venue.park_factor_runs == 98.0
            assert venue.park_factor_hr == 98.0
    finally:
        csv_path.unlink(missing_ok=True)
