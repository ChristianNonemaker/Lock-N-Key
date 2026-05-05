print("Step 1: imports starting")
import sys
print("Step 2: sys imported")

try:
    from dk_ncaab.db.session import SessionLocal
    print("Step 3: SessionLocal imported")
except Exception as e:
    print(f"Step 3 FAILED: {e}")
    sys.exit(1)

try:
    from dk_ncaab.etl.features import build_features
    print("Step 4: build_features imported")
except Exception as e:
    print(f"Step 4 FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("All imports OK")
