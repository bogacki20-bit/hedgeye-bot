"""Reconfigure the HedgeyeBridge / HedgeyeBridgeWatchdog Task Scheduler
entries so the bridge stack stays operational with no manual intervention.

Two issues this script fixes (both observed in production):

1. Task action references .bat wrappers — Windows Task Scheduler always
   flashes a cmd.exe console for .bat invocations even when the task is
   set to "hidden". Replaces with direct pyw.exe (windowless interpreter).

2. The HedgeyeBridgeWatchdog repetition trigger created via
   `schtasks /Create /SC MINUTE /MO 5` has an `<Interval>PT5M</Interval>`
   element but NO `<Duration>` element. Windows defaults to "repeat for
   1 hour from start" in that case — after 1 hour, repetition stops and
   the watchdog never fires again. Symptom: "Last Run Time" and
   "Next Run Time" show 18+ minute gaps, not 5 min, after the bridge has
   been alive >1h. Bridge dies, watchdog never relaunches it.
   The fix is XML import with explicit
       <Repetition>
         <Interval>PT5M</Interval>
         <Duration>P9999D</Duration>
         <StopAtDurationEnd>false</StopAtDurationEnd>
       </Repetition>
   so the trigger repeats every 5 min for 9999 days = effectively forever.

Idempotent — safe to re-run. Reports the resulting "Task To Run" and the
relevant XML triggers block for each entry on stdout.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000

REPO_ROOT = Path(r"C:\Projects\hedgeye-bot")


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


# ─────────────────────────── XML templates ───────────────────────────

# UTF-16 LE BOM is required by schtasks /Create /XML for some Windows builds.
# We always write the XML using utf-16 encoding to be safe.

WATCHDOG_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hedgeye Bridge watchdog — fires every 5 min, restarts the bridge if heartbeat is stale or PID is dead.</Description>
    <URI>\\HedgeyeBridgeWatchdog</URI>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT5M</Interval>
        <Duration>P9999D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start_boundary}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user_id}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pyw_path}</Command>
      <Arguments>{watchdog_script}</Arguments>
      <WorkingDirectory>{repo_root}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


BRIDGE_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hedgeye Bridge — file-queue daemon launched at user logon.</Description>
    <URI>\\HedgeyeBridge</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <UserId>{user_id}</UserId>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user_id}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pyw_path}</Command>
      <Arguments>{bridge_script}</Arguments>
      <WorkingDirectory>{repo_root}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _whoami() -> str:
    """Return the current user's domain\\username for the task Principal."""
    res = _run(["whoami"])
    if res["ok"]:
        return res["stdout"].strip()
    # Fallback: USERDOMAIN\USERNAME from env
    import os
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain else user


def _start_boundary_now() -> str:
    """ISO 8601 timestamp for "now" in local time, as the start boundary
    for the watchdog's TimeTrigger. Format: 2026-05-10T15:35:00 (no Z)."""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _apply_xml_task(task_name: str, xml: str) -> dict:
    """Write XML to a temp file (UTF-16 LE), then delete-then-create the task.

    `schtasks /Create /F /XML` requires admin elevation when overwriting a
    task that already exists, even if the user owns it (apparently because
    /F is treated as a privileged mass-modify). Splitting into /Delete /F
    + /Create (no /F) sidesteps that — both delete-own-task and
    create-new-task are user-level operations.

    Returns the create result dict (delete failures are swallowed because
    a missing task is still the desired starting state for /Create).
    """
    fd = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-16", suffix=".xml", delete=False
    )
    try:
        fd.write(xml)
        fd.close()
        # Best-effort delete first (ignored if task doesn't exist)
        _run(["schtasks", "/Delete", "/F", "/TN", task_name])
        return _run(["schtasks", "/Create", "/XML", fd.name, "/TN", task_name])
    finally:
        try:
            Path(fd.name).unlink()
        except Exception:
            pass


def _query_task_xml(task_name: str) -> str:
    res = _run(["schtasks", "/Query", "/TN", task_name, "/XML"])
    if not res["ok"]:
        return f"<query xml failed: {res['stderr'][:200]}>"
    # Extract just the Triggers block for compact display
    import re as _re
    m = _re.search(r"<Triggers>.*?</Triggers>", res["stdout"], _re.DOTALL)
    return m.group(0) if m else "<no Triggers>"


XML_OUT_DIR = REPO_ROOT / ".commands" / "task_xml"


def _write_xml_files(watchdog_xml: str, bridge_xml: str) -> tuple[Path, Path, Path]:
    """Write the watchdog + bridge task XMLs to disk + an admin install script.

    Returns the three paths. Kristian runs the .ps1 once in admin PowerShell
    and the schedule is fixed forever.
    """
    XML_OUT_DIR.mkdir(parents=True, exist_ok=True)
    wd_path = XML_OUT_DIR / "HedgeyeBridgeWatchdog.xml"
    br_path = XML_OUT_DIR / "HedgeyeBridge.xml"
    ps1_path = XML_OUT_DIR / "install_admin.ps1"

    wd_path.write_text(watchdog_xml, encoding="utf-16")
    br_path.write_text(bridge_xml, encoding="utf-16")

    ps1 = f"""# Hedgeye Task Scheduler installer — must run as Administrator.
# Replaces \\HedgeyeBridgeWatchdog and \\HedgeyeBridge with corrected XML
# (proper TimeTrigger Duration so the watchdog repeats every 5 min forever).
Write-Host "Deleting existing Hedgeye scheduled tasks..."
schtasks /Delete /F /TN \\HedgeyeBridgeWatchdog 2>$null
schtasks /Delete /F /TN \\HedgeyeBridge         2>$null
Write-Host "Importing fresh XML..."
schtasks /Create /XML "{wd_path}" /TN \\HedgeyeBridgeWatchdog
schtasks /Create /XML "{br_path}" /TN \\HedgeyeBridge
Write-Host ""
Write-Host "Done. Verifying..."
schtasks /Query /TN \\HedgeyeBridgeWatchdog /XML | Select-String -Pattern '<Interval>|<Duration>|<StopAtDurationEnd>'
"""
    ps1_path.write_text(ps1, encoding="utf-8")
    return wd_path, br_path, ps1_path


def main() -> int:
    pyw = _resolve_pyw()
    user_id = _whoami()
    start_boundary = _start_boundary_now()

    watchdog_xml = WATCHDOG_TASK_XML.format(
        pyw_path=pyw,
        watchdog_script=str(REPO_ROOT / "bridge_watchdog.py"),
        repo_root=str(REPO_ROOT),
        user_id=user_id,
        start_boundary=start_boundary,
    )
    bridge_xml = BRIDGE_TASK_XML.format(
        pyw_path=pyw,
        bridge_script=str(REPO_ROOT / "command_bridge.py"),
        repo_root=str(REPO_ROOT),
        user_id=user_id,
    )

    out: dict = {"pyw_path": pyw, "user_id": user_id, "results": []}

    # First try direct apply (works without admin only if task is owned by
    # current user AND was originally created without the elevation flag —
    # rarely the case in practice).
    for name, xml in (
        ("\\HedgeyeBridgeWatchdog", watchdog_xml),
        ("\\HedgeyeBridge",         bridge_xml),
    ):
        applied = _apply_xml_task(name, xml)
        verified = _query_task_xml(name)
        out["results"].append({
            "task":            name,
            "schtasks_ok":     applied["ok"],
            "schtasks_rc":     applied["rc"],
            "schtasks_stderr": (applied["stderr"] or "")[:200],
            "triggers_after":  verified,
        })

    # If direct apply failed (Access denied / file-already-exists), write
    # XML files + an admin install script for the user to run once.
    if not all(r["schtasks_ok"] for r in out["results"]):
        wd_path, br_path, ps1_path = _write_xml_files(watchdog_xml, bridge_xml)
        out["admin_install_required"] = True
        out["xml_paths"] = {"watchdog": str(wd_path), "bridge": str(br_path)}
        out["admin_install_script"] = str(ps1_path)
        out["admin_oneliner"] = f'Start-Process powershell -Verb RunAs -ArgumentList \'-NoExit\',\'-File\',\'{ps1_path}\''

    print(json.dumps(out, indent=2))
    return 0 if all(r["schtasks_ok"] for r in out["results"]) else 1


if __name__ == "__main__":
    sys.exit(main())
