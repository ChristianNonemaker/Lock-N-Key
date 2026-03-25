"""Check for duplicate events — same game loaded by ESPN and Odds API separately."""
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, OddsQuote
from sqlalchemy import text

session = SessionLocal()

# Get an odds event
oq_event = session.query(Event).filter(Event.id == 2881).first()
if oq_event:
    home = oq_event.home_team.name
    away = oq_event.away_team.name
    print(f"Odds event id=2881: {home} vs {away}")
    print(f"  start_time_utc: {oq_event.start_time_utc}")
    print(f"  external_event_key: {oq_event.external_event_key}")
    print(f"  status: {oq_event.status}")
    
    # Find the ESPN event for the same game
    espn_match = (
        session.query(Event)
        .filter(Event.home_team_id == oq_event.home_team_id)
        .filter(Event.away_team_id == oq_event.away_team_id)
        .filter(Event.id != oq_event.id)
        .all()
    )
    print(f"\n  Matching ESPN events: {len(espn_match)}")
    for m in espn_match:
        has_result = m.result is not None
        print(f"    id={m.id} key={m.external_event_key} status={m.status} result={has_result} start={m.start_time_utc}")

# Check a few more
print("\n---\n")
for oid in [2816, 2824, 2892]:
    ev = session.query(Event).filter(Event.id == oid).first()
    if ev:
        home = ev.home_team.name
        away = ev.away_team.name
        print(f"Odds event id={oid}: {home} vs {away} (key={ev.external_event_key})")
        dupes = (
            session.query(Event)
            .filter(Event.home_team_id == ev.home_team_id)
            .filter(Event.away_team_id == ev.away_team_id)
            .filter(Event.id != ev.id)
            .all()
        )
        for d in dupes:
            print(f"  Dupe: id={d.id} key={d.external_event_key} status={d.status}")

# Overall: how many events have the same home_team_id + away_team_id combos?
from sqlalchemy import func
dupes = (
    session.query(Event.home_team_id, Event.away_team_id, func.count())
    .group_by(Event.home_team_id, Event.away_team_id)
    .having(func.count() > 1)
    .all()
)
print(f"\nDuplicate team pairs: {len(dupes)}")

session.close()
