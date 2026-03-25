"""
Migrate odds quotes from orphaned Odds-API events to their matching ESPN events.

The Odds API collector previously created duplicate Event rows instead of
matching the existing ESPN-sourced events. This script:
  1. Finds odds events whose external_event_key is NOT prefixed with 'espn:'.
  2. Looks for an ESPN event with the same home_team + away_team within ±6h.
  3. Re-points all OddsQuote rows from the orphaned event to the ESPN event.
  4. Deletes the now-empty orphaned event.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, OddsQuote

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def migrate_orphan_odds() -> dict:
    stats = {"merged": 0, "orphan_deleted": 0, "no_match": 0, "quotes_moved": 0}

    with SessionLocal() as session:
        # Find all event_ids referenced by odds quotes
        odds_event_ids = [
            r[0] for r in session.query(OddsQuote.event_id).distinct().all()
        ]
        log.info("Found %d distinct event_ids in odds_quotes", len(odds_event_ids))

        for oid in odds_event_ids:
            event = session.query(Event).filter(Event.id == oid).first()
            if event is None:
                continue

            # Skip if already an ESPN event (correctly linked)
            if event.external_event_key.startswith("espn:"):
                continue

            # Try to find the ESPN event for the same game
            window_start = event.start_time_utc - timedelta(hours=6)
            window_end = event.start_time_utc + timedelta(hours=6)

            espn_event = (
                session.query(Event)
                .filter(
                    Event.home_team_id == event.home_team_id,
                    Event.away_team_id == event.away_team_id,
                    Event.start_time_utc >= window_start,
                    Event.start_time_utc <= window_end,
                    Event.external_event_key.like("espn:%"),
                )
                .first()
            )

            if espn_event is None:
                log.warning(
                    "No ESPN match for orphan event id=%d (%s vs %s @ %s)",
                    event.id,
                    event.home_team.name,
                    event.away_team.name,
                    event.start_time_utc,
                )
                stats["no_match"] += 1
                continue

            # Move all odds quotes to the ESPN event
            moved = (
                session.query(OddsQuote)
                .filter(OddsQuote.event_id == event.id)
                .update({"event_id": espn_event.id})
            )
            stats["quotes_moved"] += moved

            # Delete the orphaned event
            session.delete(event)
            stats["merged"] += 1
            stats["orphan_deleted"] += 1

            home = event.home_team.name
            away = event.away_team.name
            log.info(
                "  ✅ Merged %d quotes: %s vs %s (orphan id=%d → espn id=%d)",
                moved, home, away, event.id, espn_event.id,
            )

        session.commit()

    log.info(
        "Migration done: merged=%d  quotes_moved=%d  no_match=%d  orphans_deleted=%d",
        stats["merged"], stats["quotes_moved"],
        stats["no_match"], stats["orphan_deleted"],
    )
    return stats


if __name__ == "__main__":
    migrate_orphan_odds()
