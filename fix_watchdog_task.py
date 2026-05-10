"""Reconfigure the HedgeyeBridge / HedgeyeBridgeWatchdog Task Scheduler
entries to invoke pyw.exe directly (windowless Python) instead of bouncing
through .bat wrappers — eliminates the cmd.exe popup flash users see every
~5 minutes when the watchdog fires.

Idempotent — safe to re-run. Reports the resulting "Task To Run" for each
entry on stdout. Skips a task gracefully if schtasks /Change reports it
doesn't exist.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

REPO_ROOT = Path(r"C:\Projects\hedgeye-bot")

# Resolve pyw.exe absolute path so schtasks doesn't depend on PATH at runtime.
def _resolve_pyw() -> str:
    candidates = [
        shutil.which("pyw"),
        shutil.which("pyw.exe"),
        shutil.which("pythonw"),
        shutil.which("pythonw.exe"),
    ]
    for c in candidates:
        if c:
            return c
    # Fallback to bare name; schtasks /Change accepts unqualified.
    return "pyw.exe"


def _run(argv: list[str]) -> dict:
    try:
        cp = subprocess.run(
            argv, capture_output=True, text=True, timeout=30, shell=False,
            creationflags=CREATE_NO_WINDOW,
        )
        return {"ok": cp.returncode == 0, "rc": cp.returncode,
                "stdout": cp.stdout, "stderr": cp.stderr}
    except Exception as e:
        return {"ok": False, "rc": None, "stdout": "", "stderr": str(e)}


def _change_task(task_name: str, command: str) -> dict:
    res = _run(["schtasks", "/Change", "/TN", task_name, "/TR", command])
    return {"task": task_name, "command": command, **res}


def _query_task(task_name: str) -> str:
    res = _run(["schtasks", "/Query", "/TN", task_name, "/FO", "CSV", "/V"])
    if not res["ok"]:
        return f"<query failed: {res['stderr'][:80]}>"
    # The 'Task To Run' column is somewhere in the CSV — easier to grep.
    import csv, io
    rows = list(csv.reader(io.StringIO(res["stdout"])))
    if len(rows) < 2:
        return "<no rows>"
    headers = rows[0]
    if "Task To Run" not in headers:
        return "<no Task To Run column>"
    idx = headers.index("Task To Run")
    return rows[1][idx] if idx < len(rows[1]) else "<empty>"


def main() -> int:
    pyw = _resolve_pyw()
    targets = [
        ("HedgeyeBridgeWatchdog", f'{pyw} {REPO_ROOT / "bridge_watchdog.py"}'),
        ("HedgeyeBridge",         f'{pyw} {REPO_ROOT / "command_bridge.py"}'),
    ]

    out: dict = {"pyw_path": pyw, "results": []}
    for name, cmd in targets:
        res = _change_task(name, cmd)
        verified = _query_task(name)
        out["results"].append({
            "task":            name,
            "applied_command": cmd,
            "schtasks_ok":     res["ok"],
            "schtasks_rc":     res["rc"],
            "schtasks_stderr": res["stderr"][:200] if res["stderr"] else "",
            "now_runs":        verified,
        })
    print(json.dumps(out, indent=2))
    return 0 if all(r["schtasks_ok"] for r in out["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
