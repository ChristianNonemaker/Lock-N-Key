"""Dump all teams from DB."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Team

s = SessionLocal()
teams = s.query(Team).order_by(Team.normalized_name).all()
print(f"Total teams: {len(teams)}")
for t in teams:
    print(f"  {t.id:4d} | {t.normalized_name:40s} | {t.name}")
s.close()
