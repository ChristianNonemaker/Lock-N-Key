"""Manual MLB venue metadata import/export.

Use this for the small hand-reviewed venue layer: orientation, roof handling,
weather exposure, and notes. This keeps the source of truth explicit instead of
scraping ballpark diagrams repeatedly.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.config.mlb_venues import curated_mlb_venues, lookup_mlb_venue
from dk_ncaab.db.models import MlbVenue
from dk_ncaab.db.session import SessionLocal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MlbVenueMetadataImportResult:
    rows_read: int
    rows_imported: int
    venues_created: int
    rows_skipped: int


@dataclass(frozen=True)
class MlbVenueMetadataExportResult:
    rows_written: int
    csv_path: str


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _column_key(value: str | None) -> str:
    return _norm(value).replace(" ", "_").replace("-", "_").replace("/", "_")


def _safe_float(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _safe_bool(value: str | None) -> bool | None:
    text = _norm(value)
    if not text:
        return None
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return None


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    lower = {_column_key(name): name for name in fieldnames}
    aliases = {
        "provider": ["provider"],
        "provider_venue_key": ["provider_venue_key", "venue_id", "mlb_venue_id"],
        "venue_name": ["venue_name", "venue", "park", "park_name", "stadium", "name"],
        "latitude": ["latitude", "lat"],
        "longitude": ["longitude", "lon", "lng"],
        "roof_type": ["roof_type", "roof"],
        "orientation_deg": ["orientation_deg", "orientation", "center_field_bearing_deg"],
        "weather_exposure_rule": ["weather_exposure_rule", "exposure_rule"],
        "wind_reliable_flag": ["wind_reliable_flag", "wind_reliable"],
        "notes": ["notes", "note"],
        "orientation_source": ["orientation_source", "source"],
        "orientation_source_url": ["orientation_source_url", "source_url", "reference_url"],
    }
    mapped: dict[str, str] = {}
    for key, candidates in aliases.items():
        for candidate in candidates:
            if candidate in lower:
                mapped[key] = lower[candidate]
                break
    return mapped


def _compose_notes(
    notes: str | None,
    orientation_source: str | None,
    orientation_source_url: str | None,
) -> str | None:
    parts = []
    if notes:
        parts.append(notes.strip())
    if orientation_source:
        parts.append(f"orientation_source={orientation_source.strip()}")
    if orientation_source_url:
        parts.append(f"orientation_source_url={orientation_source_url.strip()}")
    return "; ".join(part for part in parts if part) or None


def _lookup_existing_venue(
    session: Session,
    *,
    provider: str | None,
    provider_venue_key: str | None,
    venue_name: str | None,
) -> MlbVenue | None:
    provider = (provider or "").strip()
    provider_venue_key = (provider_venue_key or "").strip()
    venue_name = (venue_name or "").strip()

    if provider and provider_venue_key:
        venue = session.execute(
            select(MlbVenue).where(
                MlbVenue.provider == provider,
                MlbVenue.provider_venue_key == provider_venue_key,
            )
        ).scalar_one_or_none()
        if venue:
            return venue
    if provider_venue_key:
        venue = session.execute(
            select(MlbVenue).where(MlbVenue.provider_venue_key == provider_venue_key)
        ).scalar_one_or_none()
        if venue:
            return venue
    if venue_name:
        return session.execute(
            select(MlbVenue).where(MlbVenue.name.ilike(venue_name))
        ).scalar_one_or_none()
    return None


def import_mlb_venue_metadata_csv(
    csv_path: str | Path,
    *,
    session: Session | None = None,
) -> MlbVenueMetadataImportResult:
    """Import reviewed MLB venue orientation/weather metadata from CSV."""
    own_session = session is None
    session = session or SessionLocal()
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    rows_read = rows_imported = venues_created = rows_skipped = 0
    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ValueError("Empty venue metadata CSV")
            col = _map_columns(reader.fieldnames)
            if "venue_name" not in col and "provider_venue_key" not in col:
                raise ValueError("CSV must include venue_name or provider_venue_key")

            for row in reader:
                rows_read += 1
                provider = row.get(col.get("provider", ""), "").strip() or None
                provider_venue_key = row.get(col.get("provider_venue_key", ""), "").strip() or None
                venue_name = row.get(col.get("venue_name", ""), "").strip() or None
                venue = _lookup_existing_venue(
                    session,
                    provider=provider,
                    provider_venue_key=provider_venue_key,
                    venue_name=venue_name,
                )
                created = False
                if venue is None:
                    if not venue_name:
                        rows_skipped += 1
                        continue
                    spec = lookup_mlb_venue(venue_name)
                    canonical_name = spec.name if spec else venue_name
                    venue = MlbVenue(
                        provider=provider or "manual",
                        provider_venue_key=provider_venue_key or venue_name.lower(),
                        name=canonical_name,
                        latitude=spec.latitude if spec else None,
                        longitude=spec.longitude if spec else None,
                        roof_type=spec.roof_type if spec else None,
                        orientation_deg=spec.orientation_deg if spec else None,
                        weather_exposure_rule=(
                            spec.weather_exposure_rule if spec else "open_air"
                        ),
                        wind_reliable_flag=spec.wind_reliable_flag if spec else True,
                        source="manual_venue_metadata_csv",
                        notes=spec.notes if spec else "Created by venue metadata CSV import.",
                    )
                    session.add(venue)
                    session.flush()
                    created = True

                latitude = _safe_float(row.get(col.get("latitude", "")))
                longitude = _safe_float(row.get(col.get("longitude", "")))
                orientation_deg = _safe_float(row.get(col.get("orientation_deg", "")))
                wind_reliable_flag = _safe_bool(row.get(col.get("wind_reliable_flag", "")))
                roof_type = row.get(col.get("roof_type", ""), "").strip() or None
                weather_exposure_rule = (
                    row.get(col.get("weather_exposure_rule", ""), "").strip() or None
                )
                notes = _compose_notes(
                    row.get(col.get("notes", ""), "").strip() or None,
                    row.get(col.get("orientation_source", ""), "").strip() or None,
                    row.get(col.get("orientation_source_url", ""), "").strip() or None,
                )

                if provider:
                    venue.provider = provider
                if provider_venue_key:
                    venue.provider_venue_key = provider_venue_key
                if venue_name:
                    spec = lookup_mlb_venue(venue_name)
                    venue.name = spec.name if spec else venue_name
                if latitude is not None:
                    venue.latitude = latitude
                if longitude is not None:
                    venue.longitude = longitude
                if roof_type is not None:
                    venue.roof_type = roof_type
                if orientation_deg is not None:
                    venue.orientation_deg = orientation_deg % 360.0
                if weather_exposure_rule is not None:
                    venue.weather_exposure_rule = weather_exposure_rule
                if wind_reliable_flag is not None:
                    venue.wind_reliable_flag = wind_reliable_flag
                if notes is not None:
                    venue.notes = notes
                venue.source = "manual_venue_metadata_csv"

                venues_created += int(created)
                rows_imported += 1

            session.commit()
    finally:
        if own_session:
            session.close()

    result = MlbVenueMetadataImportResult(
        rows_read=rows_read,
        rows_imported=rows_imported,
        venues_created=venues_created,
        rows_skipped=rows_skipped,
    )
    log.info("MLB venue metadata import complete: %s", result)
    return result


def export_mlb_venue_metadata_template_csv(
    csv_path: str | Path,
    *,
    session: Session | None = None,
) -> MlbVenueMetadataExportResult:
    """Write a fillable MLB venue metadata template CSV."""
    own_session = session is None
    session = session or SessionLocal()
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "provider",
        "provider_venue_key",
        "venue_name",
        "latitude",
        "longitude",
        "roof_type",
        "orientation_deg",
        "weather_exposure_rule",
        "wind_reliable_flag",
        "orientation_source",
        "orientation_source_url",
        "notes",
    ]

    rows: list[dict[str, str | float | bool | None]] = []
    try:
        db_by_name = {
            venue.name.strip().lower(): venue
            for venue in session.execute(select(MlbVenue).order_by(MlbVenue.name.asc())).scalars()
        }
        for spec in curated_mlb_venues():
            venue = db_by_name.get(spec.name.strip().lower())
            rows.append(
                {
                    "provider": venue.provider if venue else "mlb_stats_api",
                    "provider_venue_key": venue.provider_venue_key if venue else "",
                    "venue_name": spec.name,
                    "latitude": venue.latitude if venue and venue.latitude is not None else spec.latitude,
                    "longitude": (
                        venue.longitude if venue and venue.longitude is not None else spec.longitude
                    ),
                    "roof_type": venue.roof_type if venue and venue.roof_type else spec.roof_type,
                    "orientation_deg": venue.orientation_deg if venue else spec.orientation_deg,
                    "weather_exposure_rule": (
                        venue.weather_exposure_rule
                        if venue and venue.weather_exposure_rule
                        else spec.weather_exposure_rule
                    ),
                    "wind_reliable_flag": (
                        venue.wind_reliable_flag
                        if venue and venue.wind_reliable_flag is not None
                        else spec.wind_reliable_flag
                    ),
                    "orientation_source": "",
                    "orientation_source_url": "",
                    "notes": venue.notes if venue and venue.notes else spec.notes,
                }
            )

        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    finally:
        if own_session:
            session.close()

    result = MlbVenueMetadataExportResult(rows_written=len(rows), csv_path=str(csv_path))
    log.info("MLB venue metadata template export complete: %s", result)
    return result
