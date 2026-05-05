"""
Snapshot extraction: OPEN / T-60 / T-30 / CLOSE.

Each function takes (session, event_id, market, side) and returns the
DraftKings quote row at the specified anchor, or None if no qualifying row exists.

Rules:
  OPEN  = min(collected_at_utc) WHERE collected_at_utc < start
  T-60  = max(collected_at_utc) WHERE collected_at_utc < start - 60m
  T-30  = max(collected_at_utc) WHERE collected_at_utc < start - 30m
  CLOSE = max(collected_at_utc) WHERE collected_at_utc < start
  (never use exact-tip or post-tip data for earlier anchors)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from dk_ncaab.db.models import OddsQuote, Event, SplitsQuote, EventOddsQuote

AnchorName = Literal["OPEN", "T60", "T30", "CLOSE"]

# Minutes before tip for each anchor
_ANCHOR_OFFSETS: dict[AnchorName, int | None] = {
    "OPEN": None,   # special: earliest row
    "T60": 60,
    "T30": 30,
    "CLOSE": 0,
}


@dataclass(frozen=True)
class Snapshot:
    """One anchor's data for a given (event, market, side)."""
    anchor: AnchorName
    price_american: int
    implied_probability: float | None
    line: float | None
    collected_at_utc: datetime


@dataclass(frozen=True)
class SnapshotSet:
    """All four anchors for a given (event, market, side)."""
    event_id: int
    market: str
    side: str
    OPEN: Snapshot | None
    T60: Snapshot | None
    T30: Snapshot | None
    CLOSE: Snapshot | None


# ── Core query helpers ──────────────────────────────────────────

def _get_quote_at_anchor(
    session: Session,
    event_id: int,
    market: str,
    side: str,
    start_time_utc: datetime,
    anchor: AnchorName,
    participant_name: str | None = None,
    participant_entity_type: str | None = None,
    participant_team_id: int | None = None,
    participant_player_id: int | None = None,
) -> OddsQuote | EventOddsQuote | None:
    """Return the single OddsQuote or EventOddsQuote row at the specified anchor."""
    is_event_odds = market in ("team_totals", "pitcher_strikeouts", "batter_hits", "batter_total_bases")
    TableClass = EventOddsQuote if is_event_odds else OddsQuote

    if is_event_odds:
        base = and_(
            TableClass.event_id == event_id,
            TableClass.book == "draftkings",
            TableClass.market_key == market,
            TableClass.side == side,
        )
        if participant_entity_type:
            base = and_(base, TableClass.entity_type == participant_entity_type)
        if participant_player_id is not None:
            base = and_(base, TableClass.player_id == participant_player_id)
        elif participant_team_id is not None:
            base = and_(base, TableClass.team_id == participant_team_id)
        elif participant_name:
            base = and_(base, TableClass.participant_name == participant_name)
        else:
            return None
    else:
        base = and_(
            TableClass.event_id == event_id,
            TableClass.book == "draftkings",
            TableClass.market == market,
            TableClass.side == side,
        )

    if anchor == "OPEN":
        # Earliest collected row (that's still before tip)
        stmt = (
            select(TableClass)
            .where(base, TableClass.collected_at_utc < start_time_utc)
            .order_by(TableClass.collected_at_utc.asc(), TableClass.id.asc())
            .limit(1)
        )
    else:
        offset_min = _ANCHOR_OFFSETS[anchor]  # type: ignore[arg-type]
        cutoff = start_time_utc - timedelta(minutes=offset_min)
        stmt = (
            select(TableClass)
            .where(base, TableClass.collected_at_utc < cutoff)
            .order_by(TableClass.collected_at_utc.desc(), TableClass.id.desc())
            .limit(1)
        )

    return session.execute(stmt).scalar_one_or_none()


def _quote_to_snapshot(anchor: AnchorName, q: OddsQuote | EventOddsQuote | None) -> Snapshot | None:
    if q is None:
        return None
    return Snapshot(
        anchor=anchor,
        price_american=q.price_american,
        implied_probability=q.implied_probability,
        line=q.line,
        collected_at_utc=q.collected_at_utc,
    )


# ── Public API ──────────────────────────────────────────────────

def get_snapshot(
    session: Session,
    event_id: int,
    market: str,
    side: str,
    anchor: AnchorName,
    participant_name: str | None = None,
    participant_entity_type: str | None = None,
    participant_team_id: int | None = None,
    participant_player_id: int | None = None,
) -> Snapshot | None:
    """Get a single anchor snapshot for (event, market, side)."""
    event = session.get(Event, event_id)
    if not event:
        return None
    q = _get_quote_at_anchor(
        session,
        event_id,
        market,
        side,
        event.start_time_utc,
        anchor,
        participant_name,
        participant_entity_type,
        participant_team_id,
        participant_player_id,
    )
    return _quote_to_snapshot(anchor, q)


def get_snapshot_set(
    session: Session,
    event_id: int,
    market: str,
    side: str,
    participant_name: str | None = None,
    participant_entity_type: str | None = None,
    participant_team_id: int | None = None,
    participant_player_id: int | None = None,
) -> SnapshotSet:
    """Get all four anchors for (event, market, side, and optional participant)."""
    event = session.get(Event, event_id)
    start = event.start_time_utc if event else datetime.min.replace(tzinfo=timezone.utc)

    snaps = {}
    for anchor in ("OPEN", "T60", "T30", "CLOSE"):
        q = _get_quote_at_anchor(
            session,
            event_id,
            market,
            side,
            start,
            anchor,
            participant_name,
            participant_entity_type,
            participant_team_id,
            participant_player_id,
        )
        snaps[anchor] = _quote_to_snapshot(anchor, q)

    return SnapshotSet(event_id=event_id, market=market, side=side, **snaps)


# ── Splits snapshots ────────────────────────────────────────────

@dataclass(frozen=True)
class SplitsSnapshot:
    anchor: AnchorName
    bets_pct: float
    handle_pct: float
    collected_at_utc: datetime


def get_splits_snapshot(
    session: Session,
    event_id: int,
    market: str,
    side: str,
    anchor: AnchorName,
) -> SplitsSnapshot | None:
    """Nearest-prior splits row at the given anchor."""
    event = session.get(Event, event_id)
    if not event:
        return None

    start = event.start_time_utc
    base = and_(
        SplitsQuote.event_id == event_id,
        SplitsQuote.market == market,
        SplitsQuote.side == side,
    )

    if anchor == "OPEN":
        stmt = (
            select(SplitsQuote)
            .where(base, SplitsQuote.collected_at_utc < start)
            .order_by(SplitsQuote.collected_at_utc.asc())
            .limit(1)
        )
    else:
        offset = _ANCHOR_OFFSETS[anchor]  # type: ignore[arg-type]
        cutoff = start - timedelta(minutes=offset)
        stmt = (
            select(SplitsQuote)
            .where(base, SplitsQuote.collected_at_utc < cutoff)
            .order_by(SplitsQuote.collected_at_utc.desc())
            .limit(1)
        )

    row = session.execute(stmt).scalar_one_or_none()
    if not row:
        return None
    return SplitsSnapshot(
        anchor=anchor,
        bets_pct=row.bets_pct,
        handle_pct=row.handle_pct,
        collected_at_utc=row.collected_at_utc,
    )
