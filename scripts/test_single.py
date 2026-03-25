"""Minimal test: just one event + one market/side."""
import sys
import logging
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

from dk_ncaab.db.session import SessionLocal
from dk_ncaab.etl.features import build_features

print("Starting test...")
session = SessionLocal()
print("Session created")

try:
    # Event 112 = Ohio State vs Virginia (has odds)
    print("Building features for event 112, moneyline, home...")
    fr = build_features(session, 112, "moneyline", "home")
    print(f"Success!")
    print(f"  implied_OPEN: {fr.implied_OPEN}")
    print(f"  implied_CLOSE: {fr.implied_CLOSE}")
    print(f"  fair_implied_OPEN: {fr.fair_implied_OPEN}")
    print(f"  fair_implied_CLOSE: {fr.fair_implied_CLOSE}")
    print(f"  home_win: {fr.home_win}")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    session.close()
    print("Done")
