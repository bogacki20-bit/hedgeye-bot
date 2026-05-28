"""Install/refresh the Windows Task Scheduler entry for the event-alerts
launcher.

The original registration (2026-05-16) was missing two critical pieces
that caused the task to silently exit 1 every fire and never write its
log file:

  1. PowerShell ExecutionPolicy. The task's Arguments were just
     `-File ...launcher.ps1` — on a stock workstation the user / machine
     ExecutionPolicy is `Undefined` (i.e. Restricted), so PowerShell
     refused to load the .ps1 and exited 1 before reaching any
     `Out-File` redirection. Fix: `-NoProfile -NonInteractive
     -ExecutionPolicy Bypass -File ...`.

  2. Battery flags. `DisallowStartIfOnBatteries=true` blocked the task
     entirely on any morning the laptop was unplugged. Fix: set both
     battery flags false (matches install_scanner_task.py).

This script is a near-clone of install_scanner_task.py — same pattern,
different schedule (daily at 09:30 ET, no per-day repetition).

CLI:
    py install_event_alerts_task.py                  # install / overwrite
    py install_event_alerts_task.py --uninstall      # remove
    py install_event_alerts_task.py --xml-only       # write XML + admin .ps1 only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT      = Path(__file__).parent.resolve()
TASK_NAME      = "HedgeyeBotEventAlerts"
TASK_XML_PATH  = REPO_ROOT / ".commands" / "task_xml" / "event_alerts_task.xml"
ADMIN_PS1_PATH = REPO_ROOT / ".commands" / "task_xml" / "install_event_alerts_admin.ps1"


def build_xml() -> str:
    """Build a Task Scheduler XML that fixes the two bugs in the original
    registration. Mirrors install_scanner_task.build_xml() shape."""
    powershell = r"powershell.exe"
    launcher   = str(REPO_ROOT / "event_alerts_launcher.ps1")
    args       = (f'-NoProfile -NonInteractive '
                  f'-ExecutionPolicy Bypass -File "{launcher}"')

    # StartBoundary in the past so today is a valid fire day. Local 09:30.
    start_date = (date.today() - timedelta(days=1)).isoformat()

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hedgeye bot — daily PS / SS / ETF Pro / II delta-alert sweep, fires Telegram + alerts_fired rows. Re-registered 2026-05-28 with ExecutionPolicy Bypass + battery flags off.</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start_date}T09:30:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
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
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{powershell}</Command>
      <Arguments>{args}</Arguments>
      <WorkingDirectory>{REPO_ROOT}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install(xml: str) -> dict:
    TASK_XML_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASK_XML_PATH.write_text(xml, encoding="utf-16")

    cmd = ["schtasks", "/Create", "/TN", TASK_NAME,
           "/XML", str(TASK_XML_PATH), "/F"]
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            shell=False, creationflags=0x08000000,
        )
        if cp.returncode == 0:
            return {"ok": True, "method": "schtasks",
                    "stdout": cp.stdout.strip()}
        out_err = (cp.stdout + cp.stderr).lower()
        if any(k in out_err for k in ("access", "denied", "permission")):
            admin_ps = (
                f"schtasks /Delete /TN {TASK_NAME} /F 2>$null; "
                f"schtasks /Create /TN {TASK_NAME} /XML '{TASK_XML_PATH}' /F"
            )
            ADMIN_PS1_PATH.write_text(admin_ps, encoding="utf-8")
            return {
                "ok": False, "method": "needs_admin",
                "xml_path": str(TASK_XML_PATH),
                "admin_ps1": str(ADMIN_PS1_PATH),
                "instruction": (
                    "Open PowerShell as Administrator and run:\n  "
                    f"{admin_ps}"
                ),
            }
        return {"ok": False, "method": "schtasks",
                "stdout": cp.stdout, "stderr": cp.stderr}
    except Exception as e:
        return {"ok": False, "method": "exception", "error": str(e)}


def uninstall() -> dict:
    try:
        cp = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True, text=True, timeout=30,
            shell=False, creationflags=0x08000000,
        )
        return {"ok": cp.returncode == 0,
                "stdout": cp.stdout.strip(), "stderr": cp.stderr.strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Install HedgeyeBotEventAlerts Task Scheduler entry"
    )
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--xml-only", action="store_true",
                    help="Write XML + admin script only; do not invoke schtasks")
    a = ap.parse_args()

    if a.uninstall:
        print(json.dumps(uninstall(), indent=2))
        return 0

    xml = build_xml()
    if a.xml_only:
        TASK_XML_PATH.parent.mkdir(parents=True, exist_ok=True)
        TASK_XML_PATH.write_text(xml, encoding="utf-16")
        print(json.dumps({"ok": True, "method": "xml_only",
                          "xml_path": str(TASK_XML_PATH)}, indent=2))
        return 0

    print(json.dumps(install(xml), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
