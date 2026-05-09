"""Remove a stale .git/index.lock left behind by the bash sandbox.

Whitelisted in the bridge so we can recover from "Another git process seems
to be running" errors without needing PowerShell. Only removes the lock if
it is older than 60 seconds (to avoid stomping on a real concurrent git op).
"""
import os
import sys
import time
from pathlib import Path

LOCK = Path(r"C:\Projects\hedgeye-bot\.git\index.lock")

def main():
    if not LOCK.exists():
        print("no lock file present")
        return 0
    age = time.time() - LOCK.stat().st_mtime
    if age < 60:
        print(f"lock is fresh ({age:.0f}s) — refusing to remove (a real git op might be in progress)")
        return 1
    try:
        os.remove(str(LOCK))
        print(f"removed stale lock (was {age:.0f}s old)")
        return 0
    except Exception as e:
        print(f"failed to remove: {e}")
        return 2

if __name__ == "__main__":
    sys.exit(main())
