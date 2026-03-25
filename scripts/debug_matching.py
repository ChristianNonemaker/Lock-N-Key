"""Check today's events and test team resolution for splits names."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone, timedelta
from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event
from dk_ncaab.etl.normalize import resolve_team, normalize_team_name

session = SessionLocal()

# Today's events
now = datetime.now(timezone.utc)
today_start = now.replace(hour=0, minute=0, second=0)
today_end = today_start + timedelta(days=1)
events = session.query(Event).filter(
    Event.start_time_utc.between(today_start, today_end)
).order_by(Event.start_time_utc).all()

print(f"Today's events in DB: {len(events)}")
for e in events[:15]:
    print(f"  {e.start_time_utc.strftime('%H:%M')} | {e.away_team.name:22s} @ {e.home_team.name}")

# Test some Action Network names
print(f"\n--- Team resolution test (source='dknetwork') ---")
test_names = [
    "Sac State", "UCSB", "G Tech", "Cal Poly", "SC State",
    "Auburn", "Duke", "Gonzaga", "Kentucky", "Kansas",
    "S. Carolina", "E Tennessee St", "LA Tech", "NDSU",
]
for raw in test_names:
    norm = normalize_team_name(raw)
    team = resolve_team(session, raw, "dknetwork")
    status = f"-> {team.name} (id={team.id})" if team else "!! NOT FOUND"
    print(f"  '{raw}' => '{norm}' {status}")

session.close()
