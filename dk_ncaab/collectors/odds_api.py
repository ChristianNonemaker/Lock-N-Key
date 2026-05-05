"""
Odds collector - polls The-Odds-API for DraftKings markets.

Responsibilities:
  1. Fetch upcoming events + odds (moneyline, spread, total).
  2. Upsert teams & events.
  3. Insert append-only odds_quotes rows (dedup via ON CONFLICT).
  4. Archive raw JSON payloads.
  5. Rate-limit with exponential backoff + jitter.
  6. Track API request budget (free tier = 500/month).
"""

from __future__ import annotations

import random
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from dk_ncaab.config.settings import get_settings
from dk_ncaab.config.sports import league_for_sport, odds_api_sport_for, validate_odds_sports
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import (
    Event,
    EventProviderKey,
    League,
    OddsApiUsage,
    OddsQuote,
    OddsRawPayload,
)
from dk_ncaab.etl.normalize import (
    american_to_implied,
    get_or_create_team,
)

log = logging.getLogger(__name__)

_API_KEY_QUERY_RE = re.compile(r"apiKey=[^&\s\"]*")


def _redact_api_key(value: object) -> object:
    text = str(value)
    redacted = _API_KEY_QUERY_RE.sub("apiKey=***", text)
    return redacted if redacted != text else value


class _ApiKeyRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_api_key(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_api_key(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: _redact_api_key(value) for key, value in record.args.items()}
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(f, _ApiKeyRedactionFilter) for f in _httpx_logger.filters):
    _httpx_logger.addFilter(_ApiKeyRedactionFilter())

# Last known API budget (updated on each successful fetch)
last_api_remaining: int | None = None
last_api_used: int | None = None

_MARKET_MAP = {
    "h2h": "moneyline",
    "spreads": "spread",
    "totals": "total",
}


@dataclass(frozen=True)
class OddsUsageSummary:
    monthly_budget: int
    reserve_requests: int
    recorded_requests_month: int
    requests_used: int | None
    requests_remaining: int | None
    last_request_utc: datetime | None
    requests_by_sport: dict[str, int]


def _to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _month_start(now: datetime) -> datetime:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def record_odds_api_usage(
    *,
    sport_key: str,
    provider_sport_key: str,
    endpoint: str,
    requested_at_utc: datetime,
    status_code: int | None,
    success: bool,
    requests_used: int | None = None,
    requests_remaining: int | None = None,
    error_type: str | None = None,
    notes: str | None = None,
) -> None:
    """Persist one actual Odds API request attempt."""
    try:
        with SessionLocal() as session:
            session.add(
                OddsApiUsage(
                    requested_at_utc=requested_at_utc,
                    sport_key=sport_key,
                    provider_sport_key=provider_sport_key,
                    endpoint=endpoint,
                    request_count=1,
                    status_code=status_code,
                    success=success,
                    requests_used=requests_used,
                    requests_remaining=requests_remaining,
                    error_type=error_type,
                    notes=notes,
                )
            )
            session.commit()
    except Exception:
        log.exception("Failed to persist Odds API usage for %s", sport_key)


def get_odds_usage_summary(
    session: Session,
    monthly_budget: int,
    reserve_requests: int,
    now: datetime | None = None,
) -> OddsUsageSummary:
    now = now or datetime.now(timezone.utc)
    start = _month_start(now)

    recorded = (
        session.execute(
            select(func.coalesce(func.sum(OddsApiUsage.request_count), 0))
            .where(OddsApiUsage.requested_at_utc >= start)
        ).scalar_one()
        or 0
    )

    rows = session.execute(
        select(
            OddsApiUsage.sport_key,
            func.coalesce(func.sum(OddsApiUsage.request_count), 0),
        )
        .where(OddsApiUsage.requested_at_utc >= start)
        .group_by(OddsApiUsage.sport_key)
    ).all()
    by_sport = {sport: int(count or 0) for sport, count in rows}

    latest = session.execute(
        select(OddsApiUsage)
        .where(OddsApiUsage.requested_at_utc >= start)
        .order_by(OddsApiUsage.requested_at_utc.desc(), OddsApiUsage.id.desc())
        .limit(1)
    ).scalar_one_or_none()

    header_used = latest.requests_used if latest and latest.requests_used is not None else None
    header_remaining = (
        latest.requests_remaining
        if latest and latest.requests_remaining is not None
        else None
    )
    used = header_used if header_used is not None else int(recorded)
    remaining = (
        header_remaining
        if header_remaining is not None
        else max(monthly_budget - int(recorded), 0)
    )

    return OddsUsageSummary(
        monthly_budget=monthly_budget,
        reserve_requests=reserve_requests,
        recorded_requests_month=int(recorded),
        requests_used=used,
        requests_remaining=remaining,
        last_request_utc=_ensure_aware(latest.requested_at_utc) if latest else None,
        requests_by_sport=by_sport,
    )


def select_due_odds_sports(
    session: Session,
    sports: list[str],
    max_sports_per_run: int,
    min_interval_minutes: int,
    now: datetime | None = None,
) -> list[str]:
    """Choose due sports before making any HTTP calls."""
    now = now or datetime.now(timezone.utc)
    sports = validate_odds_sports(sports)
    limit = max(0, int(max_sports_per_run))
    if limit == 0 or not sports:
        return []

    latest_rows = session.execute(
        select(OddsApiUsage.sport_key, func.max(OddsApiUsage.requested_at_utc))
        .where(OddsApiUsage.sport_key.in_(sports))
        .group_by(OddsApiUsage.sport_key)
    ).all()
    last_by_sport = {sport: _ensure_aware(last_seen) for sport, last_seen in latest_rows}
    min_interval_sec = max(0, int(min_interval_minutes)) * 60

    due: list[str] = []
    for sport in sports:
        last_seen = last_by_sport.get(sport)
        if last_seen is None or (now - last_seen).total_seconds() >= min_interval_sec:
            due.append(sport)

    order = {sport: idx for idx, sport in enumerate(sports)}

    def due_sort_key(sport: str) -> tuple[int, datetime, int]:
        last_seen = last_by_sport.get(sport)
        return (
            0 if last_seen is None else 1,
            last_seen or datetime.min.replace(tzinfo=timezone.utc),
            order[sport],
        )

    return sorted(due, key=due_sort_key)[:limit]

# ── HTTP helpers ────────────────────────────────────────────────

def _fetch_odds(client: httpx.Client, sport: str) -> dict:
    """
    GET /v4/sports/{sport}/odds from The-Odds-API.
    Returns raw JSON dict.  Logs remaining API quota from headers.
    """
    cfg = get_settings().odds_api
    odds_sport = odds_api_sport_for(sport)
    url = f"{cfg.base_url}/sports/{odds_sport}/odds"
    endpoint = f"/sports/{odds_sport}/odds"
    params = {
        "apiKey": cfg.key,
        "regions": cfg.regions,
        "markets": cfg.markets,
        "bookmakers": cfg.bookmaker,
        "oddsFormat": "american",
    }
    requested_at = datetime.now(timezone.utc)
    try:
        resp = client.get(url, params=params, timeout=30)
    except httpx.RequestError as exc:
        record_odds_api_usage(
            sport_key=sport,
            provider_sport_key=odds_sport,
            endpoint=endpoint,
            requested_at_utc=requested_at,
            status_code=None,
            success=False,
            error_type=type(exc).__name__,
            notes=str(exc)[:500],
        )
        raise

    # Track API budget from response headers
    global last_api_remaining, last_api_used
    remaining = resp.headers.get("x-requests-remaining")
    used = resp.headers.get("x-requests-used")
    last_api_remaining = _to_int(remaining)
    last_api_used = _to_int(used)
    record_odds_api_usage(
        sport_key=sport,
        provider_sport_key=odds_sport,
        endpoint=endpoint,
        requested_at_utc=requested_at,
        status_code=resp.status_code,
        success=resp.is_success,
        requests_used=last_api_used,
        requests_remaining=last_api_remaining,
        error_type=None if resp.is_success else "HTTPStatusError",
    )
    if remaining is not None:
        log.info(
            "Odds API budget: %s used, %s remaining this month",
            used or "?", remaining,
        )
    resp.raise_for_status()
    return resp.json()


def _fetch_with_backoff(client: httpx.Client, sport: str, max_retries: int = 4) -> dict | None:
    """Retry with exponential backoff + jitter on transient errors."""
    for attempt in range(max_retries):
        try:
            return _fetch_odds(client, sport)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (500, 502, 503):
                wait = (2 ** attempt) + random.uniform(0, 1)
                log.warning("HTTP %s, retrying in %.1fs (attempt %d)", e.response.status_code, wait, attempt + 1)
                time.sleep(wait)
            else:
                raise
        except httpx.RequestError as e:
            wait = (2 ** attempt) + random.uniform(0, 1)
            log.warning("Request error %s, retrying in %.1fs", e, wait)
            time.sleep(wait)
    log.error("Max retries exhausted for odds fetch")
    return None


# ── Parse + insert ──────────────────────────────────────────────

def _ensure_league(session: Session, sport: str) -> League:
    league_key, league_name = league_for_sport(sport)
    league = session.query(League).filter_by(key=league_key).first()
    if not league:
        league = League(key=league_key, name=league_name)
        session.add(league)
        session.flush()
    return league


def _find_event_by_odds_key(session: Session, odds_event_key: str) -> Event | None:
    event = session.query(Event).filter_by(external_event_key=odds_event_key).first()
    if event is not None:
        return event

    provider_key = (
        session.query(EventProviderKey)
        .filter_by(provider="odds_api", provider_event_key=odds_event_key)
        .first()
    )
    if provider_key is None:
        return None
    return session.query(Event).filter_by(id=provider_key.event_id).first()


def _ensure_odds_provider_key(
    session: Session,
    *,
    event: Event,
    sport: str,
    odds_event_key: str,
) -> None:
    existing = (
        session.query(EventProviderKey)
        .filter_by(provider="odds_api", provider_event_key=odds_event_key)
        .first()
    )
    if existing is not None:
        return

    session.add(
        EventProviderKey(
            event_id=event.id,
            sport_key=sport,
            provider="odds_api",
            provider_event_key=odds_event_key,
        )
    )
    session.flush()


def _match_existing_event(
    session: Session,
    *,
    home_team_id: int,
    away_team_id: int,
    start_utc: datetime,
) -> Event | None:
    window_start = start_utc - timedelta(hours=6)
    window_end = start_utc + timedelta(hours=6)
    candidates = list(
        session.query(Event)
        .filter(
            Event.home_team_id == home_team_id,
            Event.away_team_id == away_team_id,
            Event.start_time_utc >= window_start,
            Event.start_time_utc <= window_end,
        )
    )
    if not candidates:
        return None

    def sort_key(event: Event) -> tuple[int, int, float, int]:
        provider_keys = (
            session.query(EventProviderKey)
            .filter(EventProviderKey.event_id == event.id)
            .count()
        )
        has_result = 1 if event.result is not None else 0
        distance_sec = abs(((_ensure_aware(event.start_time_utc) or start_utc) - start_utc).total_seconds())
        return (-provider_keys, -has_result, distance_sec, event.id)

    return sorted(candidates, key=sort_key)[0]


def _upsert_event(
    session: Session,
    league: League,
    sport: str,
    api_event: dict,
) -> Event:
    """Create or update an event from the API response.

    Matching priority:
      1. Same external_event_key  (re-poll from Odds API)
      2. Same home_team + away_team on the same calendar date (ESPN match)
      3. Create new
    """
    ext_key = api_event["id"]
    event = _find_event_by_odds_key(session, ext_key)

    home_name = api_event.get("home_team", "")
    away_name = api_event.get("away_team", "")
    start_str = api_event.get("commence_time", "")
    start_utc = datetime.fromisoformat(start_str.replace("Z", "+00:00"))

    home_team = get_or_create_team(session, home_name, "odds_api", league.id)
    away_team = get_or_create_team(session, away_name, "odds_api", league.id)

    if event:
        # Update tip time if it changed (postponement)
        event.start_time_utc = start_utc
        _ensure_odds_provider_key(session, event=event, sport=sport, odds_event_key=ext_key)
        return event

    # ── Try to match an existing ESPN event by teams + date ──
    existing = _match_existing_event(
        session,
        home_team_id=home_team.id,
        away_team_id=away_team.id,
        start_utc=start_utc,
    )
    if existing:
        log.info(
            "Matched odds event to existing %s (id=%d): %s vs %s",
            existing.external_event_key, existing.id, home_name, away_name,
        )
        _ensure_odds_provider_key(session, event=existing, sport=sport, odds_event_key=ext_key)
        return existing

    now = datetime.now(timezone.utc)
    event = Event(
        league_id=league.id,
        external_event_key=ext_key,
        start_time_utc=start_utc,
        home_team_id=home_team.id,
        away_team_id=away_team.id,
        first_seen_at_utc=now,
    )
    session.add(event)
    session.flush()
    _ensure_odds_provider_key(session, event=event, sport=sport, odds_event_key=ext_key)
    log.info("New event: %s vs %s @ %s", home_name, away_name, start_utc)

    return event


def _parse_outcomes(
    api_event: dict,
    event: Event,
    collected_at: datetime,
) -> list[dict]:
    """
    Extract (market, side, line, price) from The-Odds-API bookmaker outcomes.
    Returns list of dicts ready for OddsQuote insert.
    """
    rows: list[dict] = []
    for bookmaker in api_event.get("bookmakers", []):
        if bookmaker.get("key") != get_settings().odds_api.bookmaker:
            continue
        for mkt in bookmaker.get("markets", []):
            market_key = _MARKET_MAP.get(mkt["key"])
            if not market_key:
                continue
            for outcome in mkt.get("outcomes", []):
                price = outcome.get("price")
                if price is None:
                    continue

                # Determine side
                name = outcome.get("name", "")
                point = outcome.get("point")
                if market_key == "total":
                    side = "over" if name == "Over" else "under"
                    line = point
                elif market_key == "spread":
                    side = (
                        "home" if name == api_event.get("home_team") else "away"
                    )
                    line = point
                else:  # moneyline
                    side = (
                        "home" if name == api_event.get("home_team") else "away"
                    )
                    line = None

                rows.append(dict(
                    event_id=event.id,
                    book="draftkings",
                    market=market_key,
                    side=side,
                    line=line,
                    price_american=int(price),
                    implied_probability=round(american_to_implied(int(price)), 6),
                    collected_at_utc=collected_at,
                    source="the_odds_api",
                ))
    return rows


def _insert_quotes(session: Session, rows: list[dict]) -> int:
    """Bulk insert with ON CONFLICT DO NOTHING (dedup)."""
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        stmt = sqlite_insert(OddsQuote).values(rows).on_conflict_do_nothing(
            index_elements=[
                "event_id",
                "book",
                "market",
                "side",
                "price_american",
                "line",
                "collected_at_utc",
            ]
        )
    elif dialect == "postgresql":
        stmt = pg_insert(OddsQuote).values(rows).on_conflict_do_nothing(
            constraint="uq_odds_dedup"
        )
    else:
        raise RuntimeError(f"Unsupported odds insert dialect: {dialect}")
    result = session.execute(stmt)
    return result.rowcount  # type: ignore[return-value]


def _archive_raw(session: Session, payload: list | dict, collected_at: datetime) -> None:
    session.add(OddsRawPayload(
        collected_at_utc=collected_at,
        source="the_odds_api",
        payload_json=payload,
    ))


# ── Public entry point ──────────────────────────────────────────

def collect_odds() -> int:
    """
    Single poll cycle: fetch -> parse -> insert -> archive.
    Call this on a schedule (see jobs/scheduler.py).
    Returns count of newly inserted quote rows.
    """
    log.info("Starting odds collection cycle")

    cfg = get_settings().odds_api
    if not cfg.key.strip():
        log.warning("Odds API key is not configured; skipping odds collection")
        return 0

    configured_sports = validate_odds_sports(cfg.active_sports())
    with SessionLocal() as session:
        usage = get_odds_usage_summary(
            session,
            monthly_budget=cfg.monthly_request_budget,
            reserve_requests=cfg.reserve_requests,
        )
        if usage.requests_remaining is not None and usage.requests_remaining <= cfg.reserve_requests:
            log.warning(
                "Odds API budget reserve reached: %s remaining, reserve=%s; skipping",
                usage.requests_remaining,
                cfg.reserve_requests,
            )
            return 0
        budget_limited_max = min(
            cfg.max_sports_per_run,
            max((usage.requests_remaining or 0) - cfg.reserve_requests, 0),
        )
        sports = select_due_odds_sports(
            session,
            configured_sports,
            max_sports_per_run=budget_limited_max,
            min_interval_minutes=cfg.min_interval_minutes,
        )

    if not sports:
        log.info(
            "No odds sports due; configured=%s max_sports_per_run=%s min_interval_minutes=%s",
            configured_sports,
            cfg.max_sports_per_run,
            cfg.min_interval_minutes,
        )
        return 0

    total_inserted = 0
    with httpx.Client() as client:
        with SessionLocal() as session:
            for sport in sports:
                data = _fetch_with_backoff(
                    client,
                    sport,
                    max_retries=max(1, int(cfg.max_request_attempts)),
                )
                if data is None:
                    log.error("Odds fetch returned None for %s, skipping sport", sport)
                    continue

                collected_at = datetime.now(timezone.utc)
                league = _ensure_league(session, sport)

                inserted_for_sport = 0
                for api_event in data:
                    event = _upsert_event(session, league, sport, api_event)
                    rows = _parse_outcomes(api_event, event, collected_at)
                    inserted_for_sport += _insert_quotes(session, rows)

                _archive_raw(
                    session,
                    {"sport": sport, "events": data},
                    collected_at,
                )
                total_inserted += inserted_for_sport
                log.info(
                    "Odds %s cycle complete: %d new quote rows",
                    sport,
                    inserted_for_sport,
                )

            session.commit()

    log.info("Odds multi-sport cycle complete: %d new quote rows", total_inserted)
    return total_inserted
