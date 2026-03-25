"""
Odds collector – polls The-Odds-API for DraftKings NCAAB markets.

Responsibilities:
  1. Fetch upcoming events + odds (moneyline, spread, total).
  2. Upsert teams & events.
  3. Insert append-only odds_quotes rows (dedup via ON CONFLICT).
  4. Archive raw JSON payloads.
  5. Rate-limit with exponential backoff + jitter.
  6. Track API request budget (free tier = 500/month).
"""

from __future__ import annotations

import json
import time
import random
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dk_ncaab.config.settings import get_settings
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import (
    League, Event, OddsQuote, OddsRawPayload,
)
from dk_ncaab.etl.normalize import (
    american_to_implied,
    get_or_create_team,
)

log = logging.getLogger(__name__)

# Last known API budget (updated on each successful fetch)
last_api_remaining: int | None = None
last_api_used: int | None = None

_MARKET_MAP = {
    "h2h": "moneyline",
    "spreads": "spread",
    "totals": "total",
}


# ── HTTP helpers ────────────────────────────────────────────────

def _fetch_odds(client: httpx.Client) -> dict:
    """
    GET /v4/sports/{sport}/odds from The-Odds-API.
    Returns raw JSON dict.  Logs remaining API quota from headers.
    """
    cfg = get_settings().odds_api
    url = f"{cfg.base_url}/sports/{cfg.sport}/odds"
    params = {
        "apiKey": cfg.key,
        "regions": cfg.regions,
        "markets": cfg.markets,
        "bookmakers": cfg.bookmaker,
        "oddsFormat": "american",
    }
    resp = client.get(url, params=params, timeout=30)
    resp.raise_for_status()

    # Track API budget from response headers
    global last_api_remaining, last_api_used
    remaining = resp.headers.get("x-requests-remaining")
    used = resp.headers.get("x-requests-used")
    if remaining is not None:
        last_api_remaining = int(remaining)
        last_api_used = int(used) if used else None
        log.info(
            "📊 Odds API budget: %s used, %s remaining this month",
            used or "?", remaining,
        )
    return resp.json()


def _fetch_with_backoff(client: httpx.Client, max_retries: int = 4) -> dict | None:
    """Retry with exponential backoff + jitter on transient errors."""
    for attempt in range(max_retries):
        try:
            return _fetch_odds(client)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 500, 502, 503):
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

def _ensure_league(session: Session) -> League:
    league = session.query(League).filter_by(key="ncaab").first()
    if not league:
        league = League(key="ncaab", name="NCAA Men's Basketball")
        session.add(league)
        session.flush()
    return league


def _upsert_event(
    session: Session,
    league: League,
    api_event: dict,
) -> Event:
    """Create or update an event from the API response.

    Matching priority:
      1. Same external_event_key  (re-poll from Odds API)
      2. Same home_team + away_team on the same calendar date (ESPN match)
      3. Create new
    """
    ext_key = api_event["id"]
    event = session.query(Event).filter_by(external_event_key=ext_key).first()

    home_name = api_event.get("home_team", "")
    away_name = api_event.get("away_team", "")
    start_str = api_event.get("commence_time", "")
    start_utc = datetime.fromisoformat(start_str.replace("Z", "+00:00"))

    home_team = get_or_create_team(session, home_name, "odds_api", league.id)
    away_team = get_or_create_team(session, away_name, "odds_api", league.id)

    if event:
        # Update tip time if it changed (postponement)
        event.start_time_utc = start_utc
        return event

    # ── Try to match an existing ESPN event by teams + date ──
    from datetime import timedelta

    window_start = start_utc - timedelta(hours=6)
    window_end = start_utc + timedelta(hours=6)
    existing = (
        session.query(Event)
        .filter(
            Event.home_team_id == home_team.id,
            Event.away_team_id == away_team.id,
            Event.start_time_utc >= window_start,
            Event.start_time_utc <= window_end,
        )
        .first()
    )
    if existing:
        log.info(
            "Matched odds event to existing %s (id=%d): %s vs %s",
            existing.external_event_key, existing.id, home_name, away_name,
        )
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
    stmt = pg_insert(OddsQuote).values(rows).on_conflict_do_nothing(
        constraint="uq_odds_dedup"
    )
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
    Single poll cycle: fetch → parse → insert → archive.
    Call this on a schedule (see jobs/scheduler.py).
    Returns count of newly inserted quote rows.
    """
    log.info("Starting odds collection cycle")

    with httpx.Client() as client:
        data = _fetch_with_backoff(client)

    if data is None:
        log.error("Odds fetch returned None, skipping cycle")
        return 0

    collected_at = datetime.now(timezone.utc)

    with SessionLocal() as session:
        league = _ensure_league(session)

        total_inserted = 0
        for api_event in data:
            event = _upsert_event(session, league, api_event)
            rows = _parse_outcomes(api_event, event, collected_at)
            total_inserted += _insert_quotes(session, rows)

        _archive_raw(session, data, collected_at)
        session.commit()

    log.info("Odds cycle complete: %d new quote rows", total_inserted)
    return total_inserted
