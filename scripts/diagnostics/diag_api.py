"""Quick smoke test for the API."""
import httpx
import json

base = "http://localhost:8000"

# 1. Games list
r = httpx.get(f"{base}/games", params={"date": "2026-02-14"}, timeout=30)
print(f"GET /games: {r.status_code}")
d = r.json()
print(f"  {d['count']} games on {d['date']}")
for g in d["games"][:3]:
    print(f"  {g['away_team']['name']} @ {g['home_team']['name']} ({g['status']})")

# 2. Game detail (pick first game if available)
if d["games"]:
    eid = d["games"][0]["event_id"]
    r2 = httpx.get(f"{base}/game/{eid}/summary", timeout=30)
    print(f"\nGET /game/{eid}/summary: {r2.status_code}")
    s = r2.json()
    print(f"  {s['away_team']['name']} @ {s['home_team']['name']}")
    print(f"  Snapshots keys: {list(s['snapshots'].keys())}")

    # 3. Timeseries
    r3 = httpx.get(f"{base}/game/{eid}/timeseries", timeout=30)
    print(f"\nGET /game/{eid}/timeseries: {r3.status_code}")
    ts = r3.json()
    print(f"  {len(ts['odds'])} odds points, {len(ts['splits'])} splits points")

    # 4. Features
    r4 = httpx.get(f"{base}/game/{eid}/features", timeout=30)
    print(f"\nGET /game/{eid}/features: {r4.status_code}")
    fd = r4.json()
    print(f"  {len(fd['features'])} feature rows")

print("\nAll endpoints OK!")
