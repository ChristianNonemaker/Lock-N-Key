"""Local MLB data inventory for line history, stats coverage, and join gaps."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import distinct, func, inspect, select
from sqlalchemy.orm import Session

from dk_ncaab.config.sports import league_for_sport
from dk_ncaab.db.models import (
    Event,
    EventOddsQuote,
    EventProviderKey,
    EventResult,
    MlbEnvironmentSnapshot,
    MlbEventVenue,
    MlbParkFactor,
    MlbPlayerGameLog,
    MlbPlayerIdCrosswalk,
    MlbProbableStarter,
    MlbStatcastDaily,
    MlbStatsRawPayload,
    MlbTeamGameLog,
    MlbVenue,
    OddsQuote,
    Team,
)
from dk_ncaab.db.session import SessionLocal


@dataclass(frozen=True)
class MlbInventoryResult:
    summary: dict[str, Any]
    json_path: str | None = None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _count(session: Session, stmt: Any) -> int:
    value = session.execute(stmt).scalar()
    return int(value or 0)


def _range(session: Session, column: Any, where_clause: Any | None = None) -> dict[str, str | None]:
    stmt = select(func.min(column), func.max(column))
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    lo, hi = session.execute(stmt).one()
    return {"min": _iso(lo), "max": _iso(hi)}


def _counts_by(session: Session, label: str, stmt: Any) -> dict[str, int]:
    rows = session.execute(stmt).all()
    return {str(getattr(row, label) or "unknown"): int(row.count or 0) for row in rows}


def _identity_inventory(session: Session, table_exists: bool) -> dict[str, Any]:
    if not table_exists:
        return {
            "schema_present": False,
            "crosswalk_rows": 0,
            "crosswalk_linked_to_local_player": 0,
        }
    return {
        "schema_present": True,
        "crosswalk_rows": _count(session, select(func.count(MlbPlayerIdCrosswalk.id))),
        "crosswalk_linked_to_local_player": _count(
            session,
            select(func.count(MlbPlayerIdCrosswalk.id)).where(
                MlbPlayerIdCrosswalk.player_id.is_not(None)
            ),
        ),
    }


def _statcast_inventory(session: Session, table_exists: bool) -> dict[str, Any]:
    if not table_exists:
        return {
            "schema_present": False,
            "daily_rows": 0,
            "daily_rows_by_type": {},
            "date_range": {"min": None, "max": None},
            "unlinked_daily_rows": 0,
        }
    return {
        "schema_present": True,
        "daily_rows": _count(session, select(func.count(MlbStatcastDaily.id))),
        "daily_rows_by_type": _counts_by(
            session,
            "player_type",
            select(
                MlbStatcastDaily.player_type.label("player_type"),
                func.count(MlbStatcastDaily.id).label("count"),
            )
            .group_by(MlbStatcastDaily.player_type)
            .order_by(MlbStatcastDaily.player_type.asc()),
        ),
        "date_range": _range(session, MlbStatcastDaily.game_date_utc),
        "unlinked_daily_rows": _count(
            session,
            select(func.count(MlbStatcastDaily.id)).where(MlbStatcastDaily.player_id.is_(None)),
        ),
    }


def build_mlb_data_inventory(
    *,
    session: Session | None = None,
    out_dir: str | Path | None = None,
) -> MlbInventoryResult:
    """Build a compact, read-only inventory of local MLB data coverage."""
    own_session = session is None
    session = session or SessionLocal()
    try:
        inspector = inspect(session.bind)
        has_crosswalk_table = inspector.has_table("mlb_player_id_crosswalks")
        has_statcast_table = inspector.has_table("mlb_statcast_daily")
        league_key, _ = league_for_sport("baseball_mlb")
        mlb_league_id = session.execute(
            select(Event.league_id)
            .join(EventProviderKey, EventProviderKey.event_id == Event.id)
            .where(EventProviderKey.sport_key == "baseball_mlb")
            .limit(1)
        ).scalar_one_or_none()
        if mlb_league_id is None:
            mlb_league_id = session.execute(
                select(Team.league_id)
                .join(MlbTeamGameLog, MlbTeamGameLog.team_id == Team.id)
                .limit(1)
            ).scalar_one_or_none()

        event_filter = Event.league.has(key=league_key)
        if mlb_league_id is not None:
            event_filter = Event.league_id == mlb_league_id

        event_ids = select(Event.id).where(event_filter).subquery()
        final_event_ids = (
            select(Event.id)
            .join(EventResult, EventResult.event_id == Event.id)
            .where(event_filter)
            .subquery()
        )
        dk_pregame_event_ids = (
            select(distinct(OddsQuote.event_id))
            .join(Event, Event.id == OddsQuote.event_id)
            .where(event_filter)
            .where(OddsQuote.book == "draftkings")
            .where(OddsQuote.collected_at_utc < Event.start_time_utc)
            .subquery()
        )
        event_odds_pregame_event_ids = (
            select(distinct(EventOddsQuote.event_id))
            .join(Event, Event.id == EventOddsQuote.event_id)
            .where(event_filter)
            .where(EventOddsQuote.book == "draftkings")
            .where(EventOddsQuote.collected_at_utc < Event.start_time_utc)
            .subquery()
        )

        final_events = _count(session, select(func.count()).select_from(final_event_ids))
        settled_dk_events = _count(
            session,
            select(func.count(distinct(Event.id)))
            .select_from(Event)
            .join(EventResult, EventResult.event_id == Event.id)
            .join(OddsQuote, OddsQuote.event_id == Event.id)
            .where(event_filter)
            .where(OddsQuote.book == "draftkings")
            .where(OddsQuote.collected_at_utc < Event.start_time_utc),
        )
        final_events_with_team_logs = _count(
            session,
            select(func.count(distinct(MlbTeamGameLog.event_id))).where(
                MlbTeamGameLog.event_id.in_(select(final_event_ids.c.id))
            ),
        )
        final_events_with_player_logs = _count(
            session,
            select(func.count(distinct(MlbPlayerGameLog.event_id))).where(
                MlbPlayerGameLog.event_id.in_(select(final_event_ids.c.id))
            ),
        )
        events_with_mlb_provider = _count(
            session,
            select(func.count(distinct(EventProviderKey.event_id))).where(
                EventProviderKey.sport_key == "baseball_mlb",
                EventProviderKey.provider == "mlb_stats_api",
            ),
        )
        events_with_odds_provider = _count(
            session,
            select(func.count(distinct(EventProviderKey.event_id))).where(
                EventProviderKey.sport_key == "baseball_mlb",
                EventProviderKey.provider == "odds_api",
            ),
        )

        summary: dict[str, Any] = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "events": {
                "total": _count(session, select(func.count()).select_from(event_ids)),
                "final": final_events,
                "by_status": _counts_by(
                    session,
                    "status",
                    select(Event.status.label("status"), func.count(Event.id).label("count"))
                    .where(event_filter)
                    .group_by(Event.status)
                    .order_by(Event.status.asc()),
                ),
                "date_range": _range(session, Event.start_time_utc, event_filter),
                "with_mlb_provider_key": events_with_mlb_provider,
                "with_odds_provider_key": events_with_odds_provider,
            },
            "line_history": {
                "odds_quotes": _count(session, select(func.count(OddsQuote.id))),
                "draftkings_pregame_events": _count(
                    session, select(func.count()).select_from(dk_pregame_event_ids)
                ),
                "settled_draftkings_pregame_events": settled_dk_events,
                "core_quotes_by_market": _counts_by(
                    session,
                    "market",
                    select(OddsQuote.market.label("market"), func.count(OddsQuote.id).label("count"))
                    .join(Event, Event.id == OddsQuote.event_id)
                    .where(event_filter)
                    .where(OddsQuote.book == "draftkings")
                    .group_by(OddsQuote.market)
                    .order_by(OddsQuote.market.asc()),
                ),
                "core_quote_date_range": _range(
                    session,
                    OddsQuote.collected_at_utc,
                    OddsQuote.event_id.in_(select(event_ids.c.id)),
                ),
                "event_specific_quotes": _count(session, select(func.count(EventOddsQuote.id))),
                "event_specific_pregame_events": _count(
                    session, select(func.count()).select_from(event_odds_pregame_event_ids)
                ),
                "event_specific_quotes_by_market": _counts_by(
                    session,
                    "market_key",
                    select(
                        EventOddsQuote.market_key.label("market_key"),
                        func.count(EventOddsQuote.id).label("count"),
                    )
                    .join(Event, Event.id == EventOddsQuote.event_id)
                    .where(event_filter)
                    .where(EventOddsQuote.book == "draftkings")
                    .group_by(EventOddsQuote.market_key)
                    .order_by(EventOddsQuote.market_key.asc()),
                ),
                "unlinked_event_specific_player_quotes": _count(
                    session,
                    select(func.count(EventOddsQuote.id)).where(
                        EventOddsQuote.entity_type == "player",
                        EventOddsQuote.player_id.is_(None),
                    ),
                ),
                "unlinked_event_specific_team_quotes": _count(
                    session,
                    select(func.count(EventOddsQuote.id)).where(
                        EventOddsQuote.entity_type == "team",
                        EventOddsQuote.team_id.is_(None),
                    ),
                ),
            },
            "mlb_stats": {
                "team_logs": _count(session, select(func.count(MlbTeamGameLog.id))),
                "team_log_date_range": _range(session, MlbTeamGameLog.game_date_utc),
                "player_logs": _count(session, select(func.count(MlbPlayerGameLog.id))),
                "player_log_date_range": _range(session, MlbPlayerGameLog.game_date_utc),
                "probable_starters": _count(session, select(func.count(MlbProbableStarter.id))),
                "raw_payloads": _count(session, select(func.count(MlbStatsRawPayload.id))),
                "raw_payloads_by_endpoint": _counts_by(
                    session,
                    "endpoint",
                    select(
                        MlbStatsRawPayload.endpoint.label("endpoint"),
                        func.count(MlbStatsRawPayload.id).label("count"),
                    )
                    .group_by(MlbStatsRawPayload.endpoint)
                    .order_by(MlbStatsRawPayload.endpoint.asc()),
                ),
            },
            "identity": _identity_inventory(session, has_crosswalk_table),
            "statcast": _statcast_inventory(session, has_statcast_table),
            "environment": {
                "venues": _count(session, select(func.count(MlbVenue.id))),
                "event_venues": _count(session, select(func.count(MlbEventVenue.event_id))),
                "weather_snapshots": _count(session, select(func.count(MlbEnvironmentSnapshot.id))),
                "park_factors": _count(session, select(func.count(MlbParkFactor.id))),
            },
            "missing_joins": {
                "final_events_without_team_logs": max(final_events - final_events_with_team_logs, 0),
                "final_events_without_player_logs": max(
                    final_events - final_events_with_player_logs,
                    0,
                ),
                "draftkings_events_without_mlb_provider_key": max(
                    events_with_odds_provider - events_with_mlb_provider,
                    0,
                ),
            },
        }

        json_path = None
        if out_dir is not None:
            target = Path(out_dir)
            target.mkdir(parents=True, exist_ok=True)
            json_path = str(target / "mlb_data_inventory.json")
            Path(json_path).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

        return MlbInventoryResult(summary=summary, json_path=json_path)
    finally:
        if own_session:
            session.close()
