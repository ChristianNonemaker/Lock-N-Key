"""Manual MLB park-factor import.

Park factors are source-sensitive, so we import reviewed CSVs with lineage
instead of scraping a leaderboard implicitly.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.config.mlb_venues import lookup_mlb_venue, lookup_mlb_venue_for_fangraphs_team
from dk_ncaab.db.models import MlbParkFactor, MlbVenue
from dk_ncaab.db.session import SessionLocal

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MlbParkFactorImportResult:
    rows_read: int
    rows_imported: int
    venues_created: int
    rows_skipped: int


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _column_key(value: str | None) -> str:
    return (
        _norm(value)
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace("(", "")
        .replace(")", "")
    )


def _safe_int(value: str | None) -> int | None:
    try:
        text = (value or "").strip()
        return int(text) if text else None
    except ValueError:
        return None


def _safe_float(value: str | None) -> float | None:
    try:
        text = (value or "").strip()
        return float(text) if text else None
    except ValueError:
        return None


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    lower = {_column_key(name): name for name in fieldnames}
    aliases = {
        "venue_name": ["venue_name", "venue", "park", "park_name", "stadium", "name"],
        "team_name": ["team_name", "team", "fangraphs_team"],
        "provider_venue_key": ["provider_venue_key", "venue_id", "mlb_venue_id", "mlb_park_id"],
        "season": ["season", "year"],
        "rolling_years": ["rolling_years", "rolling", "years", "sample_years"],
        "source": ["source", "provider"],
        "source_url": ["source_url", "url", "reference_url"],
        "runs_factor": ["runs_factor", "run_factor", "runs", "r", "index_r", "basic_5yr"],
        "hr_factor": ["hr_factor", "home_run_factor", "home_runs_factor", "hr", "index_hr"],
        "woba_factor": ["woba_factor", "index_woba", "woba"],
        "hits_factor": ["hits_factor", "hit_factor", "hits", "index_h", "1b"],
        "doubles_factor": ["doubles_factor", "double_factor", "doubles", "index_2b", "2b"],
        "triples_factor": ["triples_factor", "triple_factor", "triples", "index_3b", "3b"],
        "notes": ["notes", "note"],
    }
    mapped: dict[str, str] = {}
    for key, candidates in aliases.items():
        for candidate in candidates:
            if candidate in lower:
                mapped[key] = lower[candidate]
                break
    return mapped


def _find_or_create_venue(
    session: Session,
    venue_name: str,
    provider_venue_key: str | None,
    source: str,
) -> tuple[MlbVenue | None, bool]:
    venue_name = venue_name.strip()
    provider_venue_key = (provider_venue_key or "").strip()
    if provider_venue_key:
        venue = session.execute(
            select(MlbVenue).where(MlbVenue.provider_venue_key == provider_venue_key)
        ).scalar_one_or_none()
        if venue:
            return venue, False
    if venue_name:
        venue = session.execute(
            select(MlbVenue).where(MlbVenue.name.ilike(venue_name))
        ).scalar_one_or_none()
        if venue:
            return venue, False
    if not venue_name:
        return None, False

    spec = lookup_mlb_venue(venue_name)
    canonical_name = spec.name if spec else venue_name
    venue = MlbVenue(
        provider="manual",
        provider_venue_key=provider_venue_key or venue_name.lower(),
        name=canonical_name,
        latitude=spec.latitude if spec else None,
        longitude=spec.longitude if spec else None,
        roof_type=spec.roof_type if spec else None,
        orientation_deg=spec.orientation_deg if spec else None,
        weather_exposure_rule=spec.weather_exposure_rule if spec else None,
        wind_reliable_flag=spec.wind_reliable_flag if spec else None,
        park_factor_runs=None,
        park_factor_hr=None,
        source=source,
        notes=spec.notes if spec else "Created by park-factor CSV import.",
    )
    session.add(venue)
    session.flush()
    return venue, True


def _venue_name_from_row(row: dict[str, str], col: dict[str, str]) -> str:
    venue_name = row.get(col.get("venue_name", ""), "").strip()
    if venue_name:
        return venue_name
    team_name = row.get(col.get("team_name", ""), "").strip()
    spec = lookup_mlb_venue_for_fangraphs_team(team_name)
    return spec.name if spec else ""


def _rolling_years_from_row(row: dict[str, str], col: dict[str, str]) -> int:
    explicit = _safe_int(row.get(col.get("rolling_years", "")))
    if explicit:
        return explicit
    runs_col = col.get("runs_factor")
    if runs_col and _column_key(runs_col) == "basic_5yr":
        return 5
    return 1


def _notes_from_row(row: dict[str, str], col: dict[str, str]) -> str | None:
    note = row.get(col.get("notes", ""), "").strip()
    if note:
        return note
    if "team_name" in col:
        return "Imported from FanGraphs park-factor table; verify team-to-venue mapping by season."
    return None


def _latest_factor_for_venue(session: Session, venue_id: int) -> MlbParkFactor | None:
    return session.execute(
        select(MlbParkFactor)
        .where(MlbParkFactor.venue_id == venue_id)
        .order_by(
            MlbParkFactor.season.desc(),
            MlbParkFactor.rolling_years.desc().nullslast(),
            MlbParkFactor.imported_at_utc.desc(),
        )
        .limit(1)
    ).scalar_one_or_none()


def _refresh_venue_latest_factors(session: Session, venue_id: int) -> None:
    latest = _latest_factor_for_venue(session, venue_id)
    venue = session.get(MlbVenue, venue_id)
    if not latest or not venue:
        return
    venue.park_factor_runs = latest.runs_factor
    venue.park_factor_hr = latest.hr_factor


def import_mlb_park_factors_csv(
    csv_path: str | Path,
    *,
    default_source: str = "manual_csv",
    default_source_url: str | None = None,
    session: Session | None = None,
) -> MlbParkFactorImportResult:
    """Import reviewed MLB park-factor values from CSV."""
    own_session = session is None
    session = session or SessionLocal()
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    rows_read = rows_imported = venues_created = rows_skipped = 0
    touched_venue_ids: set[int] = set()
    imported_at = datetime.now(timezone.utc)

    try:
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ValueError("Empty park-factor CSV")
            col = _map_columns(reader.fieldnames)
            if "venue_name" not in col and "provider_venue_key" not in col and "team_name" not in col:
                raise ValueError("CSV must include venue_name, provider_venue_key, or team")
            if "season" not in col:
                raise ValueError("CSV must include season/year")

            for row in reader:
                rows_read += 1
                venue_name = _venue_name_from_row(row, col)
                provider_venue_key = row.get(col.get("provider_venue_key", ""), "").strip()
                season = _safe_int(row.get(col["season"]))
                source = row.get(col.get("source", ""), "").strip() or default_source
                source_url = row.get(col.get("source_url", ""), "").strip() or default_source_url
                rolling_years = _rolling_years_from_row(row, col)
                if season is None:
                    rows_skipped += 1
                    continue
                venue, created = _find_or_create_venue(
                    session,
                    venue_name,
                    provider_venue_key,
                    source,
                )
                if venue is None:
                    rows_skipped += 1
                    continue
                venues_created += int(created)

                existing = session.execute(
                    select(MlbParkFactor).where(
                        MlbParkFactor.venue_id == venue.id,
                        MlbParkFactor.season == season,
                        MlbParkFactor.rolling_years == rolling_years,
                        MlbParkFactor.source == source,
                    )
                ).scalar_one_or_none()
                factor = existing or MlbParkFactor(
                    venue_id=venue.id,
                    season=season,
                    rolling_years=rolling_years,
                    source=source,
                    imported_at_utc=imported_at,
                )
                if existing is None:
                    session.add(factor)

                factor.source_url = source_url
                factor.imported_at_utc = imported_at
                factor.runs_factor = _safe_float(row.get(col.get("runs_factor", "")))
                factor.hr_factor = _safe_float(row.get(col.get("hr_factor", "")))
                factor.woba_factor = _safe_float(row.get(col.get("woba_factor", "")))
                factor.hits_factor = _safe_float(row.get(col.get("hits_factor", "")))
                factor.doubles_factor = _safe_float(row.get(col.get("doubles_factor", "")))
                factor.triples_factor = _safe_float(row.get(col.get("triples_factor", "")))
                factor.notes = _notes_from_row(row, col)
                rows_imported += 1
                touched_venue_ids.add(venue.id)

            session.flush()
            for venue_id in touched_venue_ids:
                _refresh_venue_latest_factors(session, venue_id)
            session.commit()
    finally:
        if own_session:
            session.close()

    result = MlbParkFactorImportResult(
        rows_read=rows_read,
        rows_imported=rows_imported,
        venues_created=venues_created,
        rows_skipped=rows_skipped,
    )
    log.info("MLB park-factor CSV import complete: %s", result)
    return result
