"""Install a Windows Task Scheduler entry to run proactive_scanner.py on a
market-hours schedule.

Pattern follows fix_watchdog_task.py: builds the XML, calls schtasks /Create /XML.
Falls back to writing the XML + an admin one-liner if /Create needs admin.

Schedule: Every 30 minutes, 9:30 AM - 4:00 PM ET, Monday-Friday.

CLI:
  py install_scanner_task.py                       # install
  py install_scanner_task.py --uninstall           # remove
  py install_scanner_task.py --xml-only            # just write XML + admin script
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.resolve()
TASK_NAME = "HedgeyeBotProactiveScanner"
TASK_XML_PATH = REPO_ROOT / ".commands" / "task_xml" / "scanner_task.xml"
ADMIN_PS1_PATH = REPO_ROOT / ".commands" / "task_xml" / "install_scanner_admin.ps1"


def build_xml() -> str:
    # Run scanner through scanner_launcher.ps1 so:
    #   - stdout/stderr land in C:\Projects\hedgeye-bot\logs\scanner_YYYY-MM-DD.log
    #   - .env is loaded (DATABASE_PUBLIC_URL, ANTHROPIC_API_KEY) before python
    # Without the launcher the task ran pythonw.exe with no env and no log.
    powershell = r"powershell.exe"
    launcher = str(REPO_ROOT / "scanner_launcher.ps1")
    args = f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{launcher}"'
    # Variables kept for legacy templating below.
    pyw = powershell
    script = launcher

    # StartBoundary must be in the past (or today) for the weekly trigger to
    # consider TODAY a valid fire day. Use yesterday's date so the schedule
    # is already "active" when Windows evaluates it. Time of day is 09:30 local.
    from datetime import date, timedelta
    start_date = (date.today() - timedelta(days=1)).isoformat()

    # Trigger: every weekday at 9:30am local, then repeat every 30 min for 6.5 hours
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hedgeye bot proactive scanner — runs decision_engine over the monitored ticker list every 30 min during market hours</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start_date}T09:30:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>
          <Monday/>
          <Tuesday/>
          <Wednesday/>
          <Thursday/>
          <Friday/>
        </DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
      <Repetition>
        <Interval>PT30M</Interval>
        <Duration>PT6H30M</Duration>
        <StopAtDurationEnd>true</StopAtDurationEnd>
      </Repetition>
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

    # Try schtasks /Create /XML first (no admin needed if it doesn't already exist)
    cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(TASK_XML_PATH), "/F"]
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            shell=False, creationflags=0x08000000,
        )
        if cp.returncode == 0:
            return {"ok": True, "method": "schtasks", "stdout": cp.stdout}
        out_err = (cp.stdout + cp.stderr).lower()
        if "access" in out_err or "denied" in out_err or "permission" in out_err:
            # Write the admin one-liner
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
        return {"ok": False, "method": "schtasks", "stdout": cp.stdout, "stderr": cp.stderr}
    except Exception as e:
        return {"ok": False, "method": "exception", "error": str(e)}


def uninstall() -> dict:
    try:
        cp = subprocess.run(
            ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
            capture_output=True, text=True, timeout=30,
            shell=False, creationflags=0x08000000,
        )
        return {"ok": cp.returncode == 0, "stdout": cp.stdout, "stderr": cp.stderr}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    ap = argparse.ArgumentParser(description="Install proactive_scanner Task Scheduler entry")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--xml-only", action="store_true",
                    help="Just write XML + admin script; don't try schtasks")
    args = ap.parse_args()

    if args.uninstall:
        import json
        print(json.dumps(uninstall(), indent=2)); return

    xml = build_xml()
    if args.xml_only:
        TASK_XML_PATH.parent.mkdir(parents=True, exist_ok=True)
        TASK_XML_PATH.write_text(xml, encoding="utf-16")
        import json
        print(json.dumps({
            "ok": True, "method": "xml_only",
            "xml_path": str(TASK_XML_PATH),
        }, indent=2)); return

    import json
    print(json.dumps(install(xml), indent=2))


if __name__ == "__main__":
    main()
