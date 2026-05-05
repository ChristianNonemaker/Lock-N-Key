"""Quota-gated event-specific odds markets such as team totals and player props."""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from dk_ncaab.collectors.odds_api import get_odds_usage_summary, record_odds_api_usage
from dk_ncaab.config.props import (
    prop_market_spec,
    provider_prop_market_keys_for_sport,
)
from dk_ncaab.config.settings import get_settings
from dk_ncaab.config.sports import get_sport, odds_api_sport_for
from dk_ncaab.db.models import (
    Event,
    EventOddsQuote,
    EventProviderKey,
    MlbPlayerGameLog,
    MlbProbableStarter,
    OddsRawPayload,
    Player,
    Team,
)
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.etl.normalize import american_to_implied, normalize_team_name

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventOddsCollectionSummary:
    sport_key: str
    events_considered: int
    events_fetched: int
    rows_inserted: int
    requests_used: int | None
    requests_remaining: int | None
    markets: tuple[str, ...]
    warnings: tuple[str, ...] = ()


def _to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_person_name(raw: str) -> str:
    text = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _eligible_events(
    session: Session,
    *,
    sport_key: str,
    max_events: int,
    lookahead_hours: int,
    stale_after_minutes: int,
) -> list[tuple[Event, str]]:
    now = datetime.now(timezone.utc)
    lookahead = now + timedelta(hours=max(1, lookahead_hours))
    rows = list(
        session.execute(
            select(Event, EventProviderKey.provider_event_key)
            .join(EventProviderKey, EventProviderKey.event_id == Event.id)
            .where(EventProviderKey.provider == "odds_api")
            .where(EventProviderKey.sport_key == sport_key)
            .where(Event.status == "upcoming")
            .where(Event.start_time_utc >= now)
            .where(Event.start_time_utc <= lookahead)
            .order_by(Event.start_time_utc.asc(), Event.id.asc())
        ).all()
    )

    eligible: list[tuple[Event, str]] = []
    stale_cutoff = now - timedelta(minutes=max(1, stale_after_minutes))
    for event, provider_event_key in rows:
        latest = session.execute(
            select(func.max(EventOddsQuote.collected_at_utc))
            .where(EventOddsQuote.event_id == event.id)
            .where(EventOddsQuote.book == get_settings().odds_api.bookmaker)
        ).scalar_one_or_none()
        latest = latest.replace(tzinfo=timezone.utc) if latest and latest.tzinfo is None else latest
        if latest and latest >= stale_cutoff:
            continue
        eligible.append((event, provider_event_key))
        if len(eligible) >= max_events:
            break
    return eligible


def _resolve_event_team(session: Session, event: Event, participant_name: str) -> Team | None:
    normalized = normalize_team_name(participant_name)
    home = session.get(Team, event.home_team_id)
    away = session.get(Team, event.away_team_id)
    for team in (home, away):
        if team and normalize_team_name(team.name) == normalized:
            return team
    return None


def _resolve_event_player(
    session: Session,
    event: Event,
    participant_name: str,
) -> Player | None:
    normalized = _normalize_person_name(participant_name)
    if not normalized:
        return None

    probable_matches = list(
        session.execute(
            select(Player)
            .join(MlbProbableStarter, MlbProbableStarter.player_id == Player.id)
            .where(MlbProbableStarter.event_id == event.id)
        ).scalars()
    )
    exact_probable = [player for player in probable_matches if _normalize_person_name(player.full_name) == normalized]
    if len(exact_probable) == 1:
        return exact_probable[0]

    lookback = (event.start_time_utc.replace(tzinfo=timezone.utc) if event.start_time_utc.tzinfo is None else event.start_time_utc) - timedelta(days=45)
    candidates = list(
        session.execute(
            select(Player)
            .join(MlbPlayerGameLog, MlbPlayerGameLog.player_id == Player.id)
            .where(MlbPlayerGameLog.team_id.in_([event.home_team_id, event.away_team_id]))
            .where(MlbPlayerGameLog.game_date_utc >= lookback)
            .where(MlbPlayerGameLog.game_date_utc < event.start_time_utc)
            .group_by(Player.id)
        ).scalars()
    )
    exact = [player for player in candidates if _normalize_person_name(player.full_name) == normalized]
    return exact[0] if len(exact) == 1 else None


def _fetch_event_odds(
    client: httpx.Client,
    *,
    sport_key: str,
    provider_event_key: str,
    market_keys: list[str],
) -> tuple[dict, int | None, int | None]:
    cfg = get_settings().odds_api
    provider_sport_key = odds_api_sport_for(sport_key)
    endpoint = f"/sports/{provider_sport_key}/events/{provider_event_key}/odds"
    url = f"{cfg.base_url}{endpoint}"
    params = {
        "apiKey": cfg.key,
        "regions": cfg.regions,
        "markets": ",".join(market_keys),
        "bookmakers": cfg.bookmaker,
        "oddsFormat": "american",
    }
    requested_at = datetime.now(timezone.utc)
    try:
        response = client.get(url, params=params, timeout=30)
    except httpx.RequestError as exc:
        record_odds_api_usage(
            sport_key=sport_key,
            provider_sport_key=provider_sport_key,
            endpoint=endpoint,
            requested_at_utc=requested_at,
            status_code=None,
            success=False,
            error_type=type(exc).__name__,
            notes=str(exc)[:500],
        )
        raise

    requests_used = _to_int(response.headers.get("x-requests-used"))
    requests_remaining = _to_int(response.headers.get("x-requests-remaining"))
    record_odds_api_usage(
        sport_key=sport_key,
        provider_sport_key=provider_sport_key,
        endpoint=endpoint,
        requested_at_utc=requested_at,
        status_code=response.status_code,
        success=response.is_success,
        requests_used=requests_used,
        requests_remaining=requests_remaining,
        error_type=None if response.is_success else "HTTPStatusError",
        notes=f"event_markets={','.join(market_keys)}",
    )
    response.raise_for_status()
    return response.json(), requests_used, requests_remaining


def _archive_raw(
    session: Session,
    *,
    sport_key: str,
    provider_event_key: str,
    payload: dict,
    collected_at: datetime,
    market_keys: list[str],
) -> None:
    session.add(
        OddsRawPayload(
            collected_at_utc=collected_at,
            source="the_odds_api_event_odds",
            payload_json={
                "sport_key": sport_key,
                "provider_event_key": provider_event_key,
                "markets": market_keys,
                "response": payload,
            },
            notes="event-specific markets",
        )
    )


def _insert_rows(session: Session, rows: list[dict]) -> int:
    if not rows:
        return 0
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        stmt = sqlite_insert(EventOddsQuote).values(rows).on_conflict_do_nothing(
            index_elements=[
                "event_id",
                "book",
                "provider_market_key",
                "participant_name",
                "side",
                "price_american",
                "line",
                "collected_at_utc",
            ]
        )
    elif dialect == "postgresql":
        stmt = pg_insert(EventOddsQuote).values(rows).on_conflict_do_nothing(
            constraint="uq_event_odds_quotes_dedup"
        )
    else:
        raise RuntimeError(f"Unsupported event odds insert dialect: {dialect}")
    result = session.execute(stmt)
    return result.rowcount  # type: ignore[return-value]


def _parse_market_rows(
    session: Session,
    *,
    sport_key: str,
    event: Event,
    payload: dict,
    collected_at: datetime,
) -> list[dict]:
    rows: list[dict] = []
    bookmaker_key = get_settings().odds_api.bookmaker
    for bookmaker in payload.get("bookmakers", []):
        if bookmaker.get("key") != bookmaker_key:
            continue
        for market in bookmaker.get("markets", []):
            spec = prop_market_spec(sport_key, market.get("key", ""))
            if spec is None:
                continue
            updated_at = market.get("last_update")
            provider_updated_at = None
            if updated_at:
                provider_updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            for outcome in market.get("outcomes", []):
                price = outcome.get("price")
                if price is None:
                    continue
                side = str(outcome.get("name", "")).strip().lower()
                if side not in {"over", "under", "yes", "no"}:
                    continue
                participant_name = (
                    str(outcome.get("description") or outcome.get("participant") or outcome.get("name") or "")
                    .strip()
                )
                team = None
                player = None
                if spec.entity_type == "team":
                    team = _resolve_event_team(session, event, participant_name)
                elif spec.entity_type == "player":
                    player = _resolve_event_player(session, event, participant_name)

                rows.append(
                    {
                        "event_id": event.id,
                        "book": bookmaker_key,
                        "market_key": spec.market_key,
                        "provider_market_key": spec.provider_market_key,
                        "entity_type": spec.entity_type,
                        "team_id": team.id if team else None,
                        "player_id": player.id if player else None,
                        "participant_name": participant_name or "Unknown participant",
                        "side": side,
                        "line": outcome.get("point"),
                        "price_american": int(price),
                        "implied_probability": round(american_to_implied(int(price)), 6),
                        "provider_updated_at_utc": provider_updated_at,
                        "collected_at_utc": collected_at,
                        "source": "the_odds_api_event_odds",
                    }
                )
    return rows


def collect_event_odds_markets(
    *,
    sport_key: str = "baseball_mlb",
    max_events: int = 1,
    lookahead_hours: int = 24,
    stale_after_minutes: int = 180,
    markets: list[str] | None = None,
) -> EventOddsCollectionSummary:
    cfg = get_settings().odds_api
    if not cfg.key.strip():
        return EventOddsCollectionSummary(
            sport_key=sport_key,
            events_considered=0,
            events_fetched=0,
            rows_inserted=0,
            requests_used=None,
            requests_remaining=None,
            markets=tuple(markets or ()),
            warnings=("Odds API key is not configured.",),
        )

    spec = get_sport(sport_key)
    if spec.props_source is None:
        return EventOddsCollectionSummary(
            sport_key=sport_key,
            events_considered=0,
            events_fetched=0,
            rows_inserted=0,
            requests_used=None,
            requests_remaining=None,
            markets=tuple(markets or ()),
            warnings=(f"No props source configured for {sport_key}.",),
        )

    market_keys = markets or provider_prop_market_keys_for_sport(sport_key)
    warnings: list[str] = []
    with SessionLocal() as session:
        usage = get_odds_usage_summary(
            session,
            monthly_budget=cfg.monthly_request_budget,
            reserve_requests=cfg.reserve_requests,
        )
        if usage.requests_remaining is not None and usage.requests_remaining <= cfg.reserve_requests:
            return EventOddsCollectionSummary(
                sport_key=sport_key,
                events_considered=0,
                events_fetched=0,
                rows_inserted=0,
                requests_used=usage.requests_used,
                requests_remaining=usage.requests_remaining,
                markets=tuple(market_keys),
                warnings=("Odds API reserve reached; skipping event-specific markets.",),
            )
        remaining_budget = (
            max((usage.requests_remaining or 0) - cfg.reserve_requests, 0)
            if usage.requests_remaining is not None
            else max_events
        )
        capped_events = min(max_events, max(remaining_budget, 0))
        if capped_events <= 0:
            return EventOddsCollectionSummary(
                sport_key=sport_key,
                events_considered=0,
                events_fetched=0,
                rows_inserted=0,
                requests_used=usage.requests_used,
                requests_remaining=usage.requests_remaining,
                markets=tuple(market_keys),
                warnings=("No budget available for event-specific markets.",),
            )
        events = _eligible_events(
            session,
            sport_key=sport_key,
            max_events=capped_events,
            lookahead_hours=lookahead_hours,
            stale_after_minutes=stale_after_minutes,
        )

    if not events:
        return EventOddsCollectionSummary(
            sport_key=sport_key,
            events_considered=0,
            events_fetched=0,
            rows_inserted=0,
            requests_used=None,
            requests_remaining=None,
            markets=tuple(market_keys),
            warnings=("No eligible upcoming events needed a refresh.",),
        )

    events_fetched = 0
    rows_inserted = 0
    requests_used = None
    requests_remaining = None

    with httpx.Client() as client:
        with SessionLocal() as session:
            for candidate_event, provider_event_key in events:
                event = session.get(Event, candidate_event.id)
                if event is None:
                    warnings.append(
                        f"Event disappeared before props fetch: event_id={candidate_event.id}."
                    )
                    continue
                payload, requests_used, requests_remaining = _fetch_event_odds(
                    client,
                    sport_key=sport_key,
                    provider_event_key=provider_event_key,
                    market_keys=market_keys,
                )
                collected_at = datetime.now(timezone.utc)
                rows = _parse_market_rows(
                    session,
                    sport_key=sport_key,
                    event=event,
                    payload=payload,
                    collected_at=collected_at,
                )
                inserted = _insert_rows(session, rows)
                _archive_raw(
                    session,
                    sport_key=sport_key,
                    provider_event_key=provider_event_key,
                    payload=payload,
                    collected_at=collected_at,
                    market_keys=market_keys,
                )
                rows_inserted += inserted
                events_fetched += 1
                if not rows:
                    warnings.append(
                        f"No supported {sport_key} event markets returned for event_id={event.id}."
                    )
                session.commit()

    return EventOddsCollectionSummary(
        sport_key=sport_key,
        events_considered=len(events),
        events_fetched=events_fetched,
        rows_inserted=rows_inserted,
        requests_used=requests_used,
        requests_remaining=requests_remaining,
        markets=tuple(market_keys),
        warnings=tuple(dict.fromkeys(warnings)),
    )
