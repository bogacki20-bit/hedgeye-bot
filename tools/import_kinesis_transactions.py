"""Import a Kinesis Transactions CSV into actions_log (source='kinesis').

Spec-named entry point. The ingest logic lives in
`tools.import_kinesis_trades`: the paired incoming/outgoing rows that
share a Transactions_ID (and Order_ID) collapse to ONE actions_log row
— the non-C1USD side is the asset you bought/sold, the C1USD side is
the cash cost. Idempotent on the same natural UNIQUE as Fidelity.

    python -m tools.import_kinesis_transactions imports/Transactions_Statement_KM*.CSV
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

from tools.import_kinesis_trades import ingest, main  # noqa: E402

__all__ = ["ingest", "main"]

if __name__ == "__main__":
    sys.exit(main())
