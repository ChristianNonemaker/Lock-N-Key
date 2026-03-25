"""Check status distribution of events that have odds quotes."""
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult, OddsQuote
from sqlalchemy import func

session = SessionLocal()

# Status breakdown of events with odds
rows = (
    session.query(Event.status, func.count(Event.id.distinct()))
    .join(OddsQuote, Event.id == OddsQuote.event_id)
    .group_by(Event.status)
    .all()
)
print("Events with odds by status:")
for status, count in rows:
    print(f"  {status}: {count}")

# How many of those have results?
with_results = (
    session.query(func.count(Event.id.distinct()))
    .join(OddsQuote, Event.id == OddsQuote.event_id)
    .join(EventResult, Event.id == EventResult.event_id)
    .scalar()
)
print(f"\nEvents with odds AND results: {with_results}")

# Check the date range of events with odds
min_dt = (
    session.query(func.min(Event.start_time_utc))
    .join(OddsQuote, Event.id == OddsQuote.event_id)
    .scalar()
)
max_dt = (
    session.query(func.max(Event.start_time_utc))
    .join(OddsQuote, Event.id == OddsQuote.event_id)
    .scalar()
)
print(f"\nOdds events date range: {min_dt} to {max_dt}")

# When did odds collection start?
min_oq = session.query(func.min(OddsQuote.collected_at_utc)).scalar()
max_oq = session.query(func.max(OddsQuote.collected_at_utc)).scalar()
print(f"Odds quote timestamps: {min_oq} to {max_oq}")

# Check odds distribution by market
mkt = (
    session.query(OddsQuote.market, func.count())
    .group_by(OddsQuote.market)
    .all()
)
print(f"\nOdds by market:")
for m, c in mkt:
    print(f"  {m}: {c}")

# How many odds quotes per event on avg?
avg_q = (
    session.query(func.count(OddsQuote.id) / func.count(OddsQuote.event_id.distinct()))
    .scalar()
)
print(f"\nAvg quotes per event: {avg_q:.1f}")

session.close()
