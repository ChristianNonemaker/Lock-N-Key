"""MLB weather/wind environment collection.

This collector uses the National Weather Service API for U.S. ballparks. It is
separate from odds collection and stores append-only snapshots so the UI can
explain conditions without spending Odds API quota.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.config.settings import get_settings
from dk_ncaab.config.mlb_venues import lookup_mlb_venue
from dk_ncaab.collectors.mlb_wind import derive_field_wind
from dk_ncaab.db.models import (
    Event,
    EventProviderKey,
    League,
    MlbEnvironmentSnapshot,
    MlbEventVenue,
    MlbStatsRawPayload,
    MlbVenue,
)
from dk_ncaab.db.session import SessionLocal

log = logging.getLogger(__name__)

PROVIDER = "nws_api"


@dataclass(frozen=True)
class MlbEnvironmentResult:
    events_considered: int
    events_skipped: int
    snapshots_created: int


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _wind_mph(value: str | None) -> float | None:
    if not value:
        return None
    numbers = [float(part) for part in re.findall(r"\d+(?:\.\d+)?", value)]
    if not numbers:
        return None
    return round(max(numbers), 1)


def _period_for_start(periods: list[dict[str, Any]], start_time: datetime) -> dict[str, Any] | None:
    start_time = _ensure_utc(start_time) or start_time
    nearest: tuple[float, dict[str, Any]] | None = None
    for period in periods:
        p_start = _parse_dt(period.get("startTime"))
        p_end = _parse_dt(period.get("endTime"))
        if p_start and p_end and p_start <= start_time < p_end:
            return period
        if p_start:
            distance = abs((p_start - start_time).total_seconds())
            if nearest is None or distance < nearest[0]:
                nearest = (distance, period)
    return nearest[1] if nearest else None


def _fetch_json(
    client: httpx.Client,
    url: str,
    *,
    user_agent: str,
) -> dict[str, Any]:
    resp = client.get(
        url,
        headers={
            "Accept": "application/geo+json",
            "User-Agent": user_agent,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _latest_snapshot_recent(session: Session, event_id: int, now: datetime) -> bool:
    latest = session.execute(
        select(MlbEnvironmentSnapshot.collected_at_utc)
        .where(MlbEnvironmentSnapshot.event_id == event_id)
        .order_by(MlbEnvironmentSnapshot.collected_at_utc.desc())
        .limit(1)
    ).scalar_one_or_none()
    latest = _ensure_utc(latest)
    return bool(latest and latest >= now - timedelta(hours=3))


def _upsert_event_venue_from_game(session: Session, provider_event_key: str, game: dict[str, Any]) -> bool:
    provider_key = session.execute(
        select(EventProviderKey).where(
            EventProviderKey.provider == "mlb_stats_api",
            EventProviderKey.provider_event_key == provider_event_key,
        )
    ).scalar_one_or_none()
    if provider_key is None:
        return False

    venue_payload = game.get("venue") or {}
    venue_key = str(venue_payload.get("id") or "").strip()
    venue_name = (venue_payload.get("name") or "").strip()
    if not venue_key and not venue_name:
        return False
    if not venue_key:
        venue_key = venue_name.lower()

    spec = lookup_mlb_venue(venue_name)
    venue = session.execute(
        select(MlbVenue).where(
            MlbVenue.provider == "mlb_stats_api",
            MlbVenue.provider_venue_key == venue_key,
        )
    ).scalar_one_or_none()
    created = venue is None
    if venue is None:
        venue = MlbVenue(provider="mlb_stats_api", provider_venue_key=venue_key)
        session.add(venue)
    venue.name = spec.name if spec else (venue_name or venue_key)
    venue.latitude = spec.latitude if spec else venue.latitude
    venue.longitude = spec.longitude if spec else venue.longitude
    venue.roof_type = spec.roof_type if spec else venue.roof_type
    venue.orientation_deg = spec.orientation_deg if spec else venue.orientation_deg
    venue.weather_exposure_rule = spec.weather_exposure_rule if spec else venue.weather_exposure_rule
    venue.wind_reliable_flag = spec.wind_reliable_flag if spec else venue.wind_reliable_flag
    venue.park_factor_runs = spec.park_factor_runs if spec else venue.park_factor_runs
    venue.park_factor_hr = spec.park_factor_hr if spec else venue.park_factor_hr
    venue.source = "mlb_stats_raw_schedule"
    venue.notes = spec.notes if spec else "Venue coordinates pending manual review."
    session.flush()

    event_venue = session.get(MlbEventVenue, provider_key.event_id)
    if event_venue is None:
        event_venue = MlbEventVenue(
            event_id=provider_key.event_id,
            venue_id=venue.id,
            provider="mlb_stats_api",
            collected_at_utc=datetime.now(timezone.utc),
        )
        session.add(event_venue)
        return True
    if event_venue.venue_id != venue.id:
        event_venue.venue_id = venue.id
        event_venue.collected_at_utc = datetime.now(timezone.utc)
        return True
    return created


def backfill_mlb_event_venues_from_raw(session: Session) -> int:
    """Resolve event venues from archived MLB schedule payloads without network calls."""
    count = 0
    schedules = session.execute(
        select(MlbStatsRawPayload)
        .where(MlbStatsRawPayload.endpoint == "/schedule")
        .order_by(MlbStatsRawPayload.collected_at_utc.desc())
    ).scalars()
    for raw in schedules:
        for date_block in (raw.payload_json or {}).get("dates", []):
            for game in date_block.get("games", []):
                game_pk = game.get("gamePk")
                if game_pk is None:
                    continue
                count += int(_upsert_event_venue_from_game(session, str(game_pk), game))
    if count:
        session.flush()
    return count


def collect_mlb_environment(
    *,
    max_events: int | None = None,
    request_delay_sec: float | None = None,
    client: httpx.Client | None = None,
    session: Session | None = None,
) -> MlbEnvironmentResult:
    """Collect bounded NWS weather/wind snapshots for upcoming MLB events."""
    cfg = get_settings().mlb_environment
    max_events = cfg.max_events_per_run if max_events is None else max_events
    request_delay_sec = (
        cfg.request_delay_sec if request_delay_sec is None else request_delay_sec
    )
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=cfg.forecast_lookahead_hours)
    own_client = client is None
    own_session = session is None
    client = client or httpx.Client()
    session = session or SessionLocal()

    considered = 0
    skipped = 0
    created = 0
    try:
        backfill_mlb_event_venues_from_raw(session)
        rows = list(
            session.execute(
                select(Event, MlbEventVenue, MlbVenue)
                .join(League, League.id == Event.league_id)
                .join(MlbEventVenue, MlbEventVenue.event_id == Event.id)
                .join(MlbVenue, MlbVenue.id == MlbEventVenue.venue_id)
                .where(League.key == "mlb")
                .where(Event.status.in_(["upcoming", "live"]))
                .where(Event.start_time_utc >= now - timedelta(hours=2))
                .where(Event.start_time_utc <= window_end)
                .order_by(Event.start_time_utc.asc())
                .limit(max_events)
            ).all()
        )
        for event, event_venue, venue in rows:
            considered += 1
            if venue.latitude is None or venue.longitude is None:
                skipped += 1
                continue
            if _latest_snapshot_recent(session, event.id, now):
                skipped += 1
                continue

            point_url = f"{cfg.nws_base_url.rstrip('/')}/points/{venue.latitude:.4f},{venue.longitude:.4f}"
            points_payload = _fetch_json(client, point_url, user_agent=cfg.user_agent)
            hourly_url = (
                points_payload.get("properties", {}).get("forecastHourly")
                or points_payload.get("properties", {}).get("forecast")
            )
            if not hourly_url:
                session.add(
                    MlbEnvironmentSnapshot(
                        event_id=event.id,
                        venue_id=event_venue.venue_id,
                        provider=PROVIDER,
                        collected_at_utc=now,
                        payload_json=points_payload,
                        source_url=point_url,
                        notes="NWS points payload did not include forecastHourly.",
                    )
                )
                created += 1
                continue

            if request_delay_sec and request_delay_sec > 0:
                time.sleep(request_delay_sec)
            forecast_payload = _fetch_json(client, hourly_url, user_agent=cfg.user_agent)
            period = _period_for_start(
                forecast_payload.get("properties", {}).get("periods", []),
                event.start_time_utc,
            )
            if period is None:
                notes = "NWS forecast payload had no usable periods."
                forecast_for_utc = None
                temperature = wind_mph = precip = None
                wind_direction = conditions = None
            else:
                notes = None
                forecast_for_utc = _parse_dt(period.get("startTime"))
                temperature = period.get("temperature")
                wind_mph = _wind_mph(period.get("windSpeed"))
                precip = (period.get("probabilityOfPrecipitation") or {}).get("value")
                wind_direction = period.get("windDirection")
                conditions = period.get("shortForecast")

            field_wind = derive_field_wind(
                wind_direction=wind_direction,
                wind_mph=wind_mph,
                center_field_orientation_deg=venue.orientation_deg,
                roof_type=venue.roof_type,
                weather_exposure_rule=venue.weather_exposure_rule,
                wind_reliable_flag=venue.wind_reliable_flag,
            )
            session.add(
                MlbEnvironmentSnapshot(
                    event_id=event.id,
                    venue_id=event_venue.venue_id,
                    provider=PROVIDER,
                    collected_at_utc=now,
                    forecast_for_utc=forecast_for_utc,
                    temperature_f=float(temperature) if temperature is not None else None,
                    wind_mph=wind_mph,
                    wind_direction=wind_direction,
                    wind_from_degrees=field_wind.wind_from_degrees,
                    wind_to_center_alignment=field_wind.wind_to_center_alignment,
                    wind_out_mph=field_wind.wind_out_mph,
                    wind_in_mph=field_wind.wind_in_mph,
                    crosswind_mph=field_wind.crosswind_mph,
                    field_wind_label=field_wind.field_wind_label,
                    precipitation_chance=float(precip) if precip is not None else None,
                    conditions=conditions,
                    source_url=hourly_url,
                    payload_json={"points": points_payload, "forecast": forecast_payload},
                    notes=notes,
                )
            )
            created += 1
        session.commit()
    finally:
        if own_session:
            session.close()
        if own_client:
            client.close()

    result = MlbEnvironmentResult(
        events_considered=considered,
        events_skipped=skipped,
        snapshots_created=created,
    )
    log.info("MLB environment collection complete: %s", result)
    return result
