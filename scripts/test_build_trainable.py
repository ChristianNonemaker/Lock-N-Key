"""Build the dataset only for events that have odds quotes AND results."""
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

from dk_ncaab.db.session import SessionLocal
from dk_ncaab.db.models import Event, EventResult, OddsQuote
from dk_ncaab.analysis.dataset_build import build_dataset, export_parquet
from sqlalchemy import func

session = SessionLocal()

# Only events with odds AND results (trainable events)
trainable_ids = [
    r[0] for r in (
        session.query(Event.id)
        .join(OddsQuote, Event.id == OddsQuote.event_id)
        .join(EventResult, Event.id == EventResult.event_id)
        .distinct()
        .all()
    )
]
log.info("Building dataset for %d trainable events (have odds + results)", len(trainable_ids))

df = build_dataset(session, event_ids=trainable_ids)
session.close()

if not df.empty:
    log.info("Dataset shape: %d rows x %d columns", len(df), len(df.columns))
    
    # Show fair_implied columns
    fair_cols = [c for c in df.columns if 'fair' in c.lower()]
    log.info("Fair probability columns: %s", fair_cols)
    
    # Check how many rows have fair_implied populated
    for col in fair_cols:
        non_null = df[col].notna().sum()
        log.info("  %s: %d/%d populated (%.0f%%)", col, non_null, len(df), 100*non_null/len(df))
    
    # Show sample
    show_cols = ['event_id', 'market', 'side', 'implied_OPEN', 'implied_CLOSE', 
                 'fair_implied_OPEN', 'fair_implied_CLOSE', 'home_win', 'spread_cover', 'total_over']
    print("\nSample rows:")
    print(df[show_cols].head(20).to_string())
    
    # Export
    path = export_parquet(df, tag="trainable")
    log.info("Exported to %s", path)
else:
    log.warning("Empty DataFrame!")
