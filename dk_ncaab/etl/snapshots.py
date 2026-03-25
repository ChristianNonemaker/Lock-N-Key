"""
Snapshot extraction: OPEN / T-60 / T-30 / CLOSE.

Each function takes (session, event_id, market, side) and returns the
quote row at the specified anchor, or None if no qualifying row exists.

Rules (see directions.md §9):
  OPEN  = min(collected_at_utc)
  T-60  = max(collected_at_utc) WHERE collected_at_utc <= start - 60m
  T-30  = max(collected_at_utc) WHERE collected_at_utc <= start - 30m
  CLOSE = max(collected_at_utc) WHERE collected_at_utc <= start
  (never use post-tip data for earlier anchors)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from dk_ncaab.db.models import OddsQuote, Event, SplitsQuote

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
) -> OddsQuote | None:
    """Return the single OddsQuote row at the specified anchor."""
    base = and_(
        OddsQuote.event_id == event_id,
        OddsQuote.market == market,
        OddsQuote.side == side,
    )

    if anchor == "OPEN":
        # Earliest collected row (that's still before tip)
        stmt = (
            select(OddsQuote)
            .where(base, OddsQuote.collected_at_utc <= start_time_utc)
            .order_by(OddsQuote.collected_at_utc.asc(), OddsQuote.id.asc())
            .limit(1)
        )
    else:
        offset_min = _ANCHOR_OFFSETS[anchor]  # type: ignore[arg-type]
        cutoff = start_time_utc - timedelta(minutes=offset_min)
        stmt = (
            select(OddsQuote)
            .where(base, OddsQuote.collected_at_utc <= cutoff)
            .order_by(OddsQuote.collected_at_utc.desc(), OddsQuote.id.desc())
            .limit(1)
        )

    return session.execute(stmt).scalar_one_or_none()


def _quote_to_snapshot(anchor: AnchorName, q: OddsQuote | None) -> Snapshot | None:
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
) -> Snapshot | None:
    """Get a single anchor snapshot for (event, market, side)."""
    event = session.get(Event, event_id)
    if not event:
        return None
    q = _get_quote_at_anchor(session, event_id, market, side, event.start_time_utc, anchor)
    return _quote_to_snapshot(anchor, q)


def get_snapshot_set(
    session: Session,
    event_id: int,
    market: str,
    side: str,
) -> SnapshotSet:
    """Get all four anchors for (event, market, side)."""
    event = session.get(Event, event_id)
    start = event.start_time_utc if event else datetime.min.replace(tzinfo=timezone.utc)

    snaps = {}
    for anchor in ("OPEN", "T60", "T30", "CLOSE"):
        q = _get_quote_at_anchor(session, event_id, market, side, start, anchor)
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
            .where(base, SplitsQuote.collected_at_utc <= start)
            .order_by(SplitsQuote.collected_at_utc.asc())
            .limit(1)
        )
    else:
        offset = _ANCHOR_OFFSETS[anchor]  # type: ignore[arg-type]
        cutoff = start - timedelta(minutes=offset)
        stmt = (
            select(SplitsQuote)
            .where(base, SplitsQuote.collected_at_utc <= cutoff)
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
