"""Probe Windows Task Scheduler for the bridge watchdog entry, return its config.

Used to diagnose the "black window flashing every few minutes" issue. The
watchdog is launched on a schedule by Task Scheduler — if the task action
uses `py.exe` or `python.exe` (console subsystem), each invocation flashes
a console window. Switching to `pyw.exe` / `pythonw.exe` (windowless) makes
it silent.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

# CREATE_NO_WINDOW = 0x08000000 — suppress the *probe's* own console flashes
# from the tasklist / schtasks calls below. (We're already running inside the
# bridge subprocess which is detached, but this guards against any nesting.)
CREATE_NO_WINDOW = 0x08000000


def _run_silent(argv: list[str], timeout: int = 30) -> dict:
    try:
        cp = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            creationflags=CREATE_NO_WINDOW,
        )
        return {"ok": cp.returncode == 0, "rc": cp.returncode,
                "stdout": cp.stdout, "stderr": cp.stderr}
    except Exception as e:
        return {"ok": False, "rc": None, "stdout": "", "stderr": str(e)}


def _query_all_tasks_csv() -> list[dict]:
    """Run `schtasks /Query /FO CSV /V` and parse every row."""
    res = _run_silent(["schtasks", "/Query", "/FO", "CSV", "/V"])
    if not res["ok"]:
        return [{"_error": res.get("stderr", "")}]
    out = res["stdout"]
    # Naive CSV parser — Windows wraps fields in quotes and commas inside
    # fields are also quoted. Use the csv module for safety.
    import csv, io
    reader = csv.reader(io.StringIO(out))
    rows = list(reader)
    if not rows:
        return []
    headers = rows[0]
    parsed = []
    for r in rows[1:]:
        if len(r) != len(headers):
            continue
        parsed.append(dict(zip(headers, r)))
    return parsed


def _find_bridge_watchdog_tasks(rows: list[dict]) -> list[dict]:
    """Filter rows that look like the bridge watchdog."""
    keys_to_check = ("TaskName", "Task To Run", "Comment")
    needles = ("bridge", "watchdog", "command_bridge", "hedgeye-bot")
    matches = []
    for row in rows:
        haystack = " ".join((row.get(k) or "") for k in keys_to_check).lower()
        if any(n in haystack for n in needles):
            matches.append(row)
    return matches


def _query_task_xml(task_name: str) -> str:
    """Return the task's complete XML config — includes triggers, repetition,
    actions, etc. Useful for diagnosing why the schedule isn't firing as
    expected (e.g. a 5-minute MO that's actually running every 18 min).
    """
    res = _run_silent(["schtasks", "/Query", "/TN", task_name, "/XML"])
    if not res["ok"]:
        return f"<query xml failed: {res['stderr'][:200]}>"
    return res["stdout"]


def main() -> int:
    rows = _query_all_tasks_csv()
    if rows and rows[0].get("_error"):
        print(json.dumps({"error": rows[0]["_error"]}, indent=2))
        return 2

    matches = _find_bridge_watchdog_tasks(rows)
    if not matches:
        print(json.dumps(
            {"matches": 0, "note": "no Task Scheduler entries reference 'bridge', 'watchdog', or 'hedgeye-bot'"},
            indent=2,
        ))
        return 0

    summary = []
    for m in matches:
        name = m.get("TaskName") or ""
        xml = _query_task_xml(name) if name else ""
        # Pull just the <Triggers> section out of the XML for compact display
        import re as _re
        triggers_match = _re.search(r"<Triggers>.*?</Triggers>", xml, _re.DOTALL)
        triggers_block = triggers_match.group(0) if triggers_match else "<no Triggers section in XML>"
        summary.append({
            "TaskName":      name,
            "Status":        m.get("Status"),
            "Last Run Time": m.get("Last Run Time"),
            "Next Run Time": m.get("Next Run Time"),
            "Task To Run":   m.get("Task To Run"),
            "Schedule Type": m.get("Schedule Type") or m.get("Repeat: Every"),
            "Run As User":   m.get("Run As User"),
            "Triggers XML":  triggers_block,
        })
    print(json.dumps({"matches": len(matches), "tasks": summary}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
