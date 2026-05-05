"""Source-backed identity reconciliation for stored event-specific odds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from dk_ncaab.collectors.odds_event_markets import (
    _normalize_person_name,
    _resolve_event_player,
    _resolve_event_team,
)
from dk_ncaab.config.props import prop_market_spec
from dk_ncaab.db.models import Event, EventOddsQuote, MlbPlayerGameLog, MlbProbableStarter, Player
from dk_ncaab.db.session import SessionLocal


@dataclass(frozen=True)
class EventOddsIdentityResolution:
    quote_id: int
    event_id: int
    market_key: str
    participant_name: str
    entity_type: str
    resolved_team_id: int | None
    resolved_player_id: int | None
    resolved_player_name: str | None
    method: str
    applied: bool


@dataclass(frozen=True)
class EventOddsIdentityReconciliationSummary:
    sport_key: str
    scanned: int
    resolvable: int
    updated: int
    unresolved: int
    resolutions: tuple[EventOddsIdentityResolution, ...]


def _exact_name(players: Iterable[Player], participant_name: str) -> list[Player]:
    normalized = _normalize_person_name(participant_name)
    return [player for player in players if _normalize_person_name(player.full_name) == normalized]


def _resolve_player_from_same_event_log(
    session: Session,
    *,
    event: Event,
    participant_name: str,
) -> Player | None:
    candidates = list(
        session.execute(
            select(Player)
            .join(MlbPlayerGameLog, MlbPlayerGameLog.player_id == Player.id)
            .where(MlbPlayerGameLog.event_id == event.id)
            .where(MlbPlayerGameLog.team_id.in_([event.home_team_id, event.away_team_id]))
            .group_by(Player.id)
        ).scalars()
    )
    exact = _exact_name(candidates, participant_name)
    return exact[0] if len(exact) == 1 else None


def _resolve_player_with_method(
    session: Session,
    *,
    event: Event,
    participant_name: str,
) -> tuple[Player | None, str]:
    probable_candidates = list(
        session.execute(
            select(Player)
            .join(MlbProbableStarter, MlbProbableStarter.player_id == Player.id)
            .where(MlbProbableStarter.event_id == event.id)
        ).scalars()
    )
    exact_probable = _exact_name(probable_candidates, participant_name)
    if len(exact_probable) == 1:
        return exact_probable[0], "probable_starter"

    same_event_player = _resolve_player_from_same_event_log(
        session,
        event=event,
        participant_name=participant_name,
    )
    if same_event_player is not None:
        return same_event_player, "same_event_boxscore"

    recent_player = _resolve_event_player(session, event, participant_name)
    if recent_player is not None:
        return recent_player, "recent_team_log"

    return None, "unresolved"


def reconcile_event_odds_identities(
    *,
    sport_key: str = "baseball_mlb",
    apply: bool = False,
    limit: int | None = None,
    session: Session | None = None,
) -> EventOddsIdentityReconciliationSummary:
    """Resolve missing EventOddsQuote participant IDs from local source-backed data."""
    own_session = session is None
    session = session or SessionLocal()
    try:
        stmt = (
            select(EventOddsQuote, Event)
            .join(Event, Event.id == EventOddsQuote.event_id)
            .where(
                (
                    (EventOddsQuote.entity_type == "team") & EventOddsQuote.team_id.is_(None)
                )
                | (
                    (EventOddsQuote.entity_type == "player") & EventOddsQuote.player_id.is_(None)
                )
            )
            .order_by(EventOddsQuote.id.asc())
        )
        if limit is not None:
            stmt = stmt.limit(max(1, limit))

        scanned = 0
        updated = 0
        resolutions: list[EventOddsIdentityResolution] = []
        for quote, event in session.execute(stmt).all():
            scanned += 1
            spec = prop_market_spec(sport_key, quote.provider_market_key or quote.market_key)
            if spec is None or spec.entity_type != quote.entity_type:
                continue

            resolved_team_id = None
            resolved_player_id = None
            resolved_player_name = None
            method = "unresolved"
            if quote.entity_type == "team":
                team = _resolve_event_team(session, event, quote.participant_name)
                if team is not None:
                    resolved_team_id = team.id
                    method = "event_team_name"
            elif quote.entity_type == "player":
                player, method = _resolve_player_with_method(
                    session,
                    event=event,
                    participant_name=quote.participant_name,
                )
                if player is not None:
                    resolved_player_id = player.id
                    resolved_player_name = player.full_name

            can_apply = resolved_team_id is not None or resolved_player_id is not None
            if can_apply and apply:
                if resolved_team_id is not None:
                    quote.team_id = resolved_team_id
                if resolved_player_id is not None:
                    quote.player_id = resolved_player_id
                updated += 1

            if can_apply:
                resolutions.append(
                    EventOddsIdentityResolution(
                        quote_id=quote.id,
                        event_id=quote.event_id,
                        market_key=quote.market_key,
                        participant_name=quote.participant_name,
                        entity_type=quote.entity_type,
                        resolved_team_id=resolved_team_id,
                        resolved_player_id=resolved_player_id,
                        resolved_player_name=resolved_player_name,
                        method=method,
                        applied=bool(apply),
                    )
                )

        if apply:
            session.commit()
        return EventOddsIdentityReconciliationSummary(
            sport_key=sport_key,
            scanned=scanned,
            resolvable=len(resolutions),
            updated=updated,
            unresolved=scanned - len(resolutions),
            resolutions=tuple(resolutions),
        )
    finally:
        if own_session:
            session.close()
