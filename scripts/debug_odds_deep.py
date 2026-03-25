"""Deep dive into the odds-events mismatch."""
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult, OddsQuote
from sqlalchemy import text

session = SessionLocal()

# Get all distinct event_ids from odds_quotes
oq_event_ids = [r[0] for r in session.query(OddsQuote.event_id).distinct().all()]
print(f"All {len(oq_event_ids)} OddsQuote event_ids:")

for eid in oq_event_ids[:10]:
    ev = session.query(Event).filter(Event.id == eid).first()
    if ev:
        has_result = ev.result is not None
        home = ev.home_team.name if ev.home_team else "?"
        away = ev.away_team.name if ev.away_team else "?"
        print(f"  id={eid} status={ev.status:8s} result={has_result} {home:25s} vs {away} ({ev.start_time_utc})")
    else:
        print(f"  id={eid} -> NO EVENT FOUND")

# Check: events updated to 'final' from the update-results run
from datetime import datetime, timezone
recently_finalized = (
    session.query(Event)
    .filter(
        Event.status == "final",
        Event.start_time_utc >= datetime(2026, 2, 14, 20, 0, tzinfo=timezone.utc),
        Event.start_time_utc <= datetime(2026, 2, 15, 10, 0, tzinfo=timezone.utc),
    )
    .all()
)
print(f"\nRecently finalized events (Feb 14 evening): {len(recently_finalized)}")
for e in recently_finalized[:5]:
    has_odds = session.query(OddsQuote).filter(OddsQuote.event_id == e.id).first() is not None
    home = e.home_team.name if e.home_team else "?"
    away = e.away_team.name if e.away_team else "?"
    print(f"  id={e.id} has_odds={has_odds} {home} vs {away}")

# Check if any of the odds event_ids have been finalized
finalized_with_odds = [eid for eid in oq_event_ids
                       if session.query(Event).filter(Event.id == eid, Event.status == "final").first()]
print(f"\nOdds event_ids that are now 'final': {len(finalized_with_odds)}")
for eid in finalized_with_odds[:5]:
    ev = session.query(Event).filter(Event.id == eid).first()
    has_result = ev.result is not None
    print(f"  id={eid} has_result={has_result}")

session.close()
