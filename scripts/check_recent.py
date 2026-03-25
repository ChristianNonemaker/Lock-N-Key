"""Quick check of recent events and results."""
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult, OddsQuote
from datetime import datetime, timezone, timedelta

session = SessionLocal()

now = datetime.now(timezone.utc)
print(f"Current UTC: {now}")

# Last 48 hours of events
cutoff = now - timedelta(hours=48)
recent = (
    session.query(Event)
    .filter(Event.start_time_utc >= cutoff)
    .order_by(Event.start_time_utc.desc())
    .all()
)
print(f"\nEvents in last 48h: {len(recent)}")
for e in recent[:30]:
    result = e.result
    score = f"{result.home_score}-{result.away_score}" if result else "NO RESULT"
    ct = e.start_time_utc.strftime("%m/%d %H:%M")
    home = e.home_team.name if e.home_team else "?"
    away = e.away_team.name if e.away_team else "?"
    print(f"  {ct} | {e.status:8s} | {home:25s} vs {away:25s} | {score}")

# Count results from last 24h
yesterday_cutoff = now - timedelta(hours=24)
yesterday_finals = (
    session.query(Event)
    .filter(Event.start_time_utc >= yesterday_cutoff, Event.status == "final")
    .count()
)
yesterday_results = (
    session.query(EventResult)
    .join(Event)
    .filter(Event.start_time_utc >= yesterday_cutoff)
    .count()
)
print(f"\nLast 24h: {yesterday_finals} final games, {yesterday_results} with results")

# Also check how many events have odds quotes
events_with_odds = (
    session.query(Event.id)
    .join(OddsQuote, Event.id == OddsQuote.event_id)
    .filter(Event.status == "final")
    .distinct()
    .count()
)
print(f"\nFinal events with odds quotes: {events_with_odds}")

# Check odds quotes for trainable events (have both odds + results)
trainable = (
    session.query(Event.id)
    .join(OddsQuote, Event.id == OddsQuote.event_id)
    .join(EventResult, Event.id == EventResult.event_id)
    .distinct()
    .count()
)
print(f"Trainable events (odds + results): {trainable}")

# Check most recent odds quote timestamp
latest_quote = (
    session.query(OddsQuote)
    .order_by(OddsQuote.collected_at_utc.desc())
    .first()
)
if latest_quote:
    print(f"\nMost recent odds quote: {latest_quote.collected_at_utc}")

# Check most recent result
latest_result = (
    session.query(EventResult)
    .order_by(EventResult.id.desc())
    .first()
)
if latest_result:
    ev = session.query(Event).filter(Event.id == latest_result.event_id).first()
    if ev:
        print(f"Most recent result: {ev.home_team.name} vs {ev.away_team.name} on {ev.start_time_utc.strftime('%m/%d')}")

session.close()
