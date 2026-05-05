"""Show a game that has odds data."""
import httpx

games = [
    g for g in httpx.get("http://localhost:8000/games", params={"date": "2026-02-14"}, timeout=30).json()["games"]
    if g.get("spread_home") is not None
]
g = games[0]
print(f"{g['away_team']['name']} @ {g['home_team']['name']}")
print(f"  Spread: {g['spread_home']} ({g['spread_home_price']}) / {g['spread_away']} ({g['spread_away_price']})")
print(f"  Total:  {g['total_line']} (o{g['total_over_price']}/u{g['total_under_price']})")
print(f"  ML:     {g['ml_home_price']} / {g['ml_away_price']}")
print(f"  Score:  {g['home_score']}-{g['away_score']}")
