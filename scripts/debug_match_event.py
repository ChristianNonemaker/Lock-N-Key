"""Debug why match_event fails even when teams resolve."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone, timedelta
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event
from dk_ncaab.etl.normalize import resolve_team, match_event

session = SessionLocal()

# Pick a specific game that should match: Auburn vs Arkansas
print("=== Debugging Auburn vs Arkansas ===")
team_a = resolve_team(session, "Auburn", "dknetwork")
team_b = resolve_team(session, "Arkansas", "dknetwork")
print(f"  Auburn resolved: id={team_a.id if team_a else 'NONE'}, name={team_a.name if team_a else ''}")
print(f"  Arkansas resolved: id={team_b.id if team_b else 'NONE'}, name={team_b.name if team_b else ''}")

# Check events with these team IDs
if team_a and team_b:
    ids = {team_a.id, team_b.id}
    events = session.query(Event).filter(
        Event.home_team_id.in_(ids),
        Event.away_team_id.in_(ids),
    ).all()
    print(f"  Events with both teams: {len(events)}")
    for e in events:
        print(f"    event_id={e.id}, {e.start_time_utc}, home={e.home_team_id} away={e.away_team_id}")

# Check what team IDs are used in today's events
now = datetime.now(timezone.utc)
today_start = now.replace(hour=0, minute=0, second=0)
today_end = today_start + timedelta(days=1)

print(f"\n=== Today's events (UTC {today_start.date()}) ===")
events = session.query(Event).filter(
    Event.start_time_utc.between(today_start, today_end)
).order_by(Event.start_time_utc).all()
print(f"  Total: {len(events)}")

# Show a few with full details
for e in events[:5]:
    print(f"  id={e.id} | {e.start_time_utc} | home_id={e.home_team_id} ({e.home_team.name}) vs away_id={e.away_team_id} ({e.away_team.name})")

# Try match_event with wide window
print(f"\n=== match_event test ===")
collected_at = now
center = collected_at.replace(hour=17, minute=0, second=0)
print(f"  Center time: {center}")
print(f"  Window: {center - timedelta(minutes=720)} to {center + timedelta(minutes=720)}")

result = match_event(session, "Auburn", "Arkansas", center, "dknetwork", tolerance_min=720)
print(f"  match_event('Auburn', 'Arkansas'): {result}")

# Manual check: search by the team IDs that Auburn->17 and Arkansas->13 map to
# versus ESPN team IDs
print(f"\n=== All Auburn teams in DB ===")
from dk_ncaab.db.models import Team
from sqlalchemy import select
auburn_teams = session.execute(select(Team).where(Team.normalized_name.like("%auburn%"))).scalars().all()
for t in auburn_teams:
    print(f"  id={t.id} | norm='{t.normalized_name}' | name='{t.name}'")

print(f"\n=== All Arkansas teams in DB ===")
ark_teams = session.execute(select(Team).where(Team.normalized_name.like("%arkansas%"))).scalars().all()
for t in ark_teams:
    if "pine" not in t.normalized_name and "state" not in t.normalized_name and "little" not in t.normalized_name:
        print(f"  id={t.id} | norm='{t.normalized_name}' | name='{t.name}'")

session.close()
