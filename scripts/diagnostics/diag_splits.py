"""Quick test: run the splits scraper and print results."""
import logging
import sys
import os
from pathlib import Path

# Force unbuffered output
os.environ["PYTHONUNBUFFERED"] = "1"

# Ensure dk_ncaab is importable when running from scripts/ dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from dk_ncaab.collectors.splits_dknetwork import _scrape_splits_page

print("Scraping Action Network...", flush=True)
try:
    html, ss, rows = _scrape_splits_page()
except Exception as e:
    print(f"SCRAPE FAILED: {e}", flush=True)
    import traceback
    traceback.print_exc()
    sys.exit(1)

print(f"\n{'='*60}", flush=True)
print(f"HTML length : {len(html):,}", flush=True)
print(f"Screenshot  : {len(ss):,} bytes", flush=True)
print(f"Parsed rows : {len(rows)}", flush=True)
print(f"{'='*60}\n", flush=True)

if not rows:
    print("!! ZERO rows parsed -- selectors still broken !!", flush=True)
    sys.exit(1)

for i, r in enumerate(rows):
    print(f"  [{i:3d}] {r.team_a:22s} vs {r.team_b:22s} | {r.side:5s} | bets={r.bets_pct:5.1f}%", flush=True)

print(f"\nTotal: {len(rows)} split rows from {len(rows)//2} games", flush=True)
