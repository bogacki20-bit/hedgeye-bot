"""One-shot YTD backfill: import Accounts_History (9)(10)(11) from Downloads
into actions_log (idempotent), then run the outcomes matcher in DRY-RUN.
Writes trades to actions_log only; outcomes_log is untouched until you run
the rebuild step separately."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.import_fidelity_trades import ingest
from tools.compute_outcomes import run as outcomes_run

DL = r"C:\Users\bogac\Downloads"
FILES = [os.path.join(DL, f"Accounts_History ({n}).csv") for n in (9, 10, 11)]

for f in FILES:
    if not os.path.exists(f):
        sys.exit(f"LOUD FAIL: missing {f}")
    print(f"\n=== importing {os.path.basename(f)} ===")
    s = ingest(f)
    print(f"  rows_seen={s['rows_seen']}  inserted={s['rows_inserted']}  "
          f"dupes={s['rows_dup_skipped']}  failed={s['rows_failed']}")
    if s["rows_failed"] or s["errors"]:
        sys.exit(f"LOUD FAIL: errors in {f}: {s['errors']}")

print("\n=== outcomes dry-run over FULL history ===")
sys.exit(outcomes_run(backfill=True, dry_run=True))
