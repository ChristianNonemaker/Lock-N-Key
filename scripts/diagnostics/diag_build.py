"""Quick test of dataset build on a few events."""
import logging
logging.basicConfig(level=logging.WARNING)

from dk_ncaab.analysis.dataset_build import build_dataset
from dk_ncaab.db.session import SessionLocal

# Events we know have odds + results
test_ids = [112, 111, 5, 2, 1]

session = SessionLocal()
try:
    df = build_dataset(session, event_ids=test_ids)
    print(f"Shape: {df.shape}")
    if df.empty:
        print("DataFrame is empty!")
    else:
        print(f"\nAll columns: {df.columns.tolist()}")
        
        # Check for fair_implied columns
        fair_cols = [c for c in df.columns if 'fair' in c.lower()]
        print(f"\nFair probability columns: {fair_cols}")
        
        # Show a sample
        show_cols = ['event_id', 'market', 'side', 'implied_OPEN', 'implied_CLOSE']
        show_cols += [c for c in fair_cols if c in df.columns]
        print(f"\nSample data:")
        print(df[show_cols].to_string())
finally:
    session.close()
