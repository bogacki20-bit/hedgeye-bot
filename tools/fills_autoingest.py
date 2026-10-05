"""fills_autoingest.py — ingest a NEW Accounts_History*.csv the moment it
lands in Downloads, without waiting for the evening _daily_upload (desk
10/5 #8: "fills log is 3 days stale again; auto-ingest Accounts_History").

Activity only — no positions file needed:
    activity ingest (--commit) -> actions_log import -> cash flows -> lot ledger

Idempotent and cheap to poll: the newest file's sha1 is remembered in
data/fills_autoingest_state.json and an unchanged file is a no-op. Every
downstream step already dedupes, so a forced re-run is safe too.

    py -m tools.fills_autoingest            # ingest if new
    py -m tools.fills_autoingest --force    # ingest even if seen
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
STATE = os.path.join(HERE, "data", "fills_autoingest_state.json")
MAX_AGE_DAYS = 3


def _newest() -> str | None:
    files = glob.glob(os.path.join(DOWNLOADS, "Accounts_History*.csv"))
    return max(files, key=os.path.getmtime) if files else None


def _sha1(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def _load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _run(label: str, args: list) -> None:
    print(f"=== {label} ===")
    rc = subprocess.run([sys.executable] + args, cwd=HERE).returncode
    if rc != 0:
        raise SystemExit(f"LOUD FAIL: {label} exited {rc}")


def ingest(force: bool = False) -> str:
    f = _newest()
    if not f:
        return "no Accounts_History*.csv in Downloads"
    age = (time.time() - os.path.getmtime(f)) / 86400
    if age > MAX_AGE_DAYS:
        return f"{os.path.basename(f)} is {age:.0f}d old — nothing new to ingest"
    h = _sha1(f)
    st = _load_state()
    if not force and st.get("sha1") == h:
        return f"{os.path.basename(f)} already ingested {st.get('at', '?')}"

    _run("activity ingest", ["ingest_fidelity.py", "--activity", f, "--commit"])
    _run("actions_log import", ["-m", "tools.import_fidelity_history", f])
    import db_pg
    db_pg._load_dotenv_fallback()
    from tools.cash_flows import ingest as cf_ingest
    print(cf_ingest(f))
    from tools.lot_ledger import integrity_check, rebuild
    n = rebuild()
    mism = integrity_check()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT max(run_date) FROM fills")
        last = cur.fetchone()[0]

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump({"sha1": h, "file": os.path.basename(f),
                   "at": time.strftime("%Y-%m-%d %H:%M")}, fh)
    return (f"ingested {os.path.basename(f)} · ledger {n} fills · last fill "
            f"{last} · integrity {'clean' if not mism else f'{len(mism)} MISMATCH(ES)'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(ingest(force=a.force))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
