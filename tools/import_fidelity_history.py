"""Import a Fidelity Accounts_History CSV into actions_log (source='fidelity').

Spec-named entry point. The ingest logic lives in
`tools.import_fidelity_trades` (one actions_log row per YOU BOUGHT /
YOU SOLD fill; idempotent on the natural UNIQUE
run_date+account_number+raw_symbol+qty+price+amount). This wrapper
exists so the importer set has a stable, descriptive name.

    python -m tools.import_fidelity_history imports/Accounts_History_3.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):
    sys.path.insert(0, str(REPO_ROOT))

from tools.import_fidelity_trades import ingest, main  # noqa: E402

__all__ = ["ingest", "main"]

if __name__ == "__main__":
    sys.exit(main())
