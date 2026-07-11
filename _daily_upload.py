"""
_daily_upload.py — the ONE evening command. Finds the newest Fidelity exports
in Downloads, then runs the whole chain:

    positions+activity ingest (--commit) -> actions_log import -> outcomes

Usage (after exporting Positions + Accounts History from Fidelity):
    python _daily_upload.py

Loud by design: stale export files (>3 days old), parse anomalies, or any
step failing stops the chain with a clear message. Every step is idempotent —
re-running is always safe.
"""
import glob
import os
import subprocess
import sys
import time
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")


def newest(pattern: str) -> str | None:
    files = glob.glob(os.path.join(DOWNLOADS, pattern))
    return max(files, key=os.path.getmtime) if files else None


def age_days(path: str) -> float:
    return (time.time() - os.path.getmtime(path)) / 86400.0


def run(label: str, args: list) -> None:
    print(f"\n=== {label} ===")
    rc = subprocess.run([sys.executable] + args, cwd=HERE).returncode
    if rc != 0:
        sys.exit(f"LOUD FAIL: {label} exited {rc} — chain stopped, fix and re-run.")


pos = newest("Portfolio_Positions_*.csv")
act = newest("Accounts_History*.csv")
if not pos or not act:
    sys.exit("LOUD FAIL: missing export(s) in Downloads — need "
             "Portfolio_Positions_*.csv AND Accounts_History*.csv.")

for f in (pos, act):
    print(f"using: {os.path.basename(f)}  ({age_days(f):.1f} days old)")
    if age_days(f) > 3:
        sys.exit(f"LOUD FAIL: {os.path.basename(f)} is {age_days(f):.0f} days "
                 f"old — export a fresh one (or delete stale files so the "
                 f"newest is the right one).")

today = date.today().isoformat()
run("1/3 book ingest (positions + activity)",
    ["ingest_fidelity.py", "--positions", pos, "--activity", act,
     "--snapshot-date", today, "--commit"])
run("2/3 actions_log import (ML trades)",
    ["-m", "tools.import_fidelity_history", act])
since = (date.today() - timedelta(days=21)).isoformat()
run("3/3 outcomes (realized round trips)",
    ["-m", "tools.compute_outcomes", "--since", since])

print("\nDAILY UPLOAD COMPLETE — book, trades, and outcomes are current.")
