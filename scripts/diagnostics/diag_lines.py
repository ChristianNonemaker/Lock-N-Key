"""Quick test: verify games API returns line data and check ET conversion."""
import httpx
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

r = httpx.get("http://localhost:8000/games", params={"date": "2026-02-14"}, timeout=30)
games = r.json()["games"]

# Show first 3 games with line data
for g in games[:3]:
    dt = datetime.fromisoformat(g["start_time_utc"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et = dt.astimezone(ET)

    print(f"{g['away_team']['name']} @ {g['home_team']['name']}")
    print(f"  UTC: {g['start_time_utc']}  →  ET: {et.strftime('%I:%M %p')}")
    print(f"  Score: {g.get('home_score')}-{g.get('away_score')}")
    print(f"  Spread: {g.get('spread_home')} ({g.get('spread_home_price')}) / {g.get('spread_away')} ({g.get('spread_away_price')})")
    print(f"  Total: {g.get('total_line')} (o{g.get('total_over_price')}/u{g.get('total_under_price')})")
    print(f"  ML: {g.get('ml_home_price')} / {g.get('ml_away_price')}")
    print()

# Count how many have line data
with_spread = sum(1 for g in games if g.get("spread_home") is not None)
print(f"{with_spread}/{len(games)} games have spread data")
