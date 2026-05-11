"""Comprehensive bot-health check: which processes are running, when was the
last email parsed, when did MFR/SG/Yahoo last refresh, and whether the
scanner Task Scheduler entry exists.
"""
import json
import subprocess
import sys
from datetime import datetime

out = {}

# All python processes (no filter — see everything)
try:
    cp = subprocess.run(
        ["tasklist", "/FO", "CSV"],
        capture_output=True, text=True, timeout=10,
        shell=False, creationflags=0x08000000,
    )
    py_lines = [
        L for L in cp.stdout.splitlines()
        if any(k in L.lower() for k in ("python", "pyw"))
    ]
    out["python_processes"] = py_lines
except Exception as e:
    out["python_processes_err"] = str(e)

# Is the HedgeyeBotProactiveScanner task registered + when's next run?
try:
    cp = subprocess.run(
        ["schtasks", "/Query", "/TN", "HedgeyeBotProactiveScanner", "/FO", "LIST", "/V"],
        capture_output=True, text=True, timeout=10,
        shell=False, creationflags=0x08000000,
    )
    out["scanner_task_query"] = cp.stdout[:2000]
except Exception as e:
    out["scanner_task_err"] = str(e)

# DB freshness
try:
    import db_pg
    with db_pg.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT MAX(received_at), COUNT(*) FROM hedgeye_emails_raw "
                       "WHERE received_at >= NOW() - INTERVAL '7 days'")
            r = cur.fetchone()
            out["latest_email_received_at"] = str(r[0])
            out["emails_last_7d"] = int(r[1])

            cur.execute("SELECT MAX(fetched_at) FROM mfr_snapshots")
            r = cur.fetchone()
            out["latest_mfr_fetched"] = str(r[0]) if r and r[0] else "none"

            cur.execute("SELECT MAX(captured_at) FROM spotgamma_snapshots")
            r = cur.fetchone()
            out["latest_spotgamma_captured"] = str(r[0]) if r and r[0] else "none"

            cur.execute("SELECT MAX(captured_at) FROM yahoo_snapshots")
            r = cur.fetchone()
            out["latest_yahoo_captured"] = str(r[0]) if r and r[0] else "none"

            cur.execute("SELECT MAX(parsed_at), COUNT(*) FROM hedgeye_emails_raw "
                       "WHERE parsed_at >= NOW() - INTERVAL '24 hours'")
            r = cur.fetchone()
            out["latest_email_parsed_at"] = str(r[0]) if r and r[0] else "none"
            out["emails_parsed_last_24h"] = int(r[1])
except Exception as e:
    out["db_err"] = str(e)

out["now"] = datetime.utcnow().isoformat() + "Z"
print(json.dumps(out, indent=2, default=str), flush=True)
sys.stdout.flush()
