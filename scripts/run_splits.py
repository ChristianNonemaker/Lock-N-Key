"""Run collect_splits() end-to-end and report results."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from dk_ncaab.collectors.splits_dknetwork import collect_splits

n = collect_splits()
print(f"\nMATCHED: {n} split rows inserted into DB", flush=True)
