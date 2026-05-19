"""Import a Kinesis Holdings_Statement CSV into positions_snapshot
(source='kinesis') and portfolio_positions.

Spec-named entry point. The ingest logic lives in
`tools.import_kinesis_positions`: it upserts each asset row into
portfolio_positions (account_type='Crypto') and then mirrors the
snapshot date into the unified positions_snapshot table. Idempotent.

    python -m tools.import_kinesis_holdings imports/Holdings_Statement_KM*.CSV
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

from tools.import_kinesis_positions import ingest, main  # noqa: E402

__all__ = ["ingest", "main"]

if __name__ == "__main__":
    sys.exit(main())
