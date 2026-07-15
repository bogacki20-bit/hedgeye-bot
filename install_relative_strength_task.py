r"""Install a Windows Task Scheduler entry to run the daily RS + sector-
correlation snapshot after the close.

Pattern mirrors install_scanner_task.py: builds the XML, calls
schtasks /Create /XML, falls back to writing XML + an admin one-liner if
/Create needs elevation.

Schedule: once daily, Monday-Friday at 16:20 local (ET). This is a few
minutes after the 16:00 close (so yfinance has the final daily bar) and is
meant to run BEFORE the EOD REPORT job so REPORT/REPORT NOW/DAYPACK render
today's grid. If your EOD REPORT task fires earlier than 16:20, move
TASK_TIME earlier to stay ahead of it.

Runs tools.relative_strength through relative_strength_launcher.ps1 so:
  - stdout/stderr land in <repo>\logs\relative_strength_YYYY-MM-DD.log
  - .env is loaded (DATABASE_PUBLIC_URL) before python
  - PYTHONUTF8 is set (emoji-safe)

CLI:
  py install_relative_strength_task.py               # install
  py install_relative_strength_task.py --uninstall   # remove
  py install_relative_strength_task.py --xml-only     # write XML + admin script only
"""
from __future__ import annotations

import argparse
import subprocess
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.resolve()
TASK_NAME = "HedgeyeBotRelativeStrength"
TASK_TIME = "16:20:00"          # local (ET) — keep ahead of the EOD REPORT job
TASK_XML_PATH = REPO_ROOT / ".commands" / "task_xml" / "relative_strength_task.xml"
ADMIN_PS1_PATH = REPO_ROOT / ".commands" / "task_xml" / "install_relative_strength_admin.ps1"


def build_xml() -> str:
    powershell = r"powershell.exe"
    launcher = str(REPO_ROOT / "relative_strength_launcher.ps1")
    args = f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{launcher}"'

    # StartBoundary must be today-or-past for the weekly trigger to treat
    # today as a valid fire day. Use yesterday so the schedule is already
    # active when Windows evaluates it.
    start_date = (date.today() - timedelta(days=1)).isoformat()

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hedgeye bot relative-strength + sector-correlation daily snapshot — recomputes rs_snapshots / diversification_snapshots after the close so REPORT/REPORT NOW/DAYPACK render today's grid.</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start_date}T{TASK_TIME}</StartBoundary>
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
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
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
                    "Open PowerShell as Administrator and run:\n  " + admin_ps
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
    ap = argparse.ArgumentParser(description="Install RS daily-snapshot Task Scheduler entry")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--xml-only", action="store_true",
                    help="Just write XML + admin script; don't try schtasks")
    args = ap.parse_args()

    import json
    if args.uninstall:
        print(json.dumps(uninstall(), indent=2)); return

    xml = build_xml()
    if args.xml_only:
        TASK_XML_PATH.parent.mkdir(parents=True, exist_ok=True)
        TASK_XML_PATH.write_text(xml, encoding="utf-16")
        print(json.dumps({"ok": True, "method": "xml_only",
                          "xml_path": str(TASK_XML_PATH)}, indent=2)); return

    print(json.dumps(install(xml), indent=2))


if __name__ == "__main__":
    main()
