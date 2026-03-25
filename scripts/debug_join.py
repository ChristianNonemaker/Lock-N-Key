"""Debug the join mismatch between events and odds_quotes."""
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult, OddsQuote
from sqlalchemy import func, text

session = SessionLocal()

# Check event_id ranges
e_min = session.query(func.min(Event.id)).scalar()
e_max = session.query(func.max(Event.id)).scalar()
print(f"Event.id range: {e_min} - {e_max}")

oq_ids = session.query(OddsQuote.event_id).distinct().limit(5).all()
print(f"Sample OddsQuote.event_id values: {[r[0] for r in oq_ids]}")

er_ids = session.query(EventResult.event_id).limit(5).all()
print(f"Sample EventResult.event_id values: {[r[0] for r in er_ids]}")

# Check if event_ids in OddsQuote actually exist in events
sample_oq_event_id = oq_ids[0][0] if oq_ids else None
if sample_oq_event_id is not None:
    matching = session.query(Event).filter(Event.id == sample_oq_event_id).first()
    print(f"\nOddsQuote event_id={sample_oq_event_id} -> Event match: {matching is not None}")
    if matching:
        print(f"  Event: {matching.home_team.name} vs {matching.away_team.name}, status={matching.status}")
    
    # Try external_event_key match
    matching2 = session.query(Event).filter(Event.external_event_key == str(sample_oq_event_id)).first()
    print(f"  Event match by external_event_key: {matching2 is not None}")

# Raw SQL to check column types
result = session.execute(text("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'odds_quotes' AND column_name = 'event_id'
"""))
for row in result:
    print(f"\nodds_quotes.event_id type: {row[1]}")

result = session.execute(text("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'events' AND column_name = 'id'
"""))
for row in result:
    print(f"events.id type: {row[1]}")

# Direct SQL join
result = session.execute(text("""
    SELECT COUNT(DISTINCT oq.event_id) 
    FROM odds_quotes oq
    JOIN events e ON oq.event_id = e.id
"""))
print(f"\nDirect SQL join count: {result.scalar()}")

# Are event_ids overlapping at all?
result = session.execute(text("""
    SELECT COUNT(DISTINCT oq.event_id) 
    FROM odds_quotes oq
    WHERE oq.event_id IN (SELECT id FROM events)
"""))
print(f"OddsQuote event_ids that exist in events: {result.scalar()}")

total_oq_events = session.query(OddsQuote.event_id).distinct().count()
print(f"Total distinct event_ids in OddsQuote: {total_oq_events}")

session.close()
