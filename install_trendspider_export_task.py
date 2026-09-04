r"""Install a Windows Task Scheduler entry for the daily TrendSpider export.

Pattern mirrors install_relative_strength_task.py: builds the XML, calls
schtasks /Create /XML, falls back to writing XML + an admin one-liner if
/Create needs elevation.

Schedule: once daily, Monday-Friday at 17:45 local (ET) — after every EOD
producer this job reads from (RS/volume/diversification 16:20, correlation
tracker 16:30, paper signals 17:30). The SpotGamma-canary second run from the
original spec was dropped (operator decision 1, 2026-09-04): the SG corpus is
dead, so everything exported is produced by the EOD chain.

CLI:
  py install_trendspider_export_task.py               # install
  py install_trendspider_export_task.py --uninstall   # remove
  py install_trendspider_export_task.py --xml-only    # write XML + admin script only
"""
from __future__ import annotations

import argparse
import subprocess
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.resolve()
TASK_NAME = "HedgeyeBotTrendspiderExport"
TASK_TIME = "17:45:00"          # local (ET) — after the 16:20-17:30 EOD chain
TASK_XML_PATH = REPO_ROOT / ".commands" / "task_xml" / "trendspider_export_task.xml"
ADMIN_PS1_PATH = REPO_ROOT / ".commands" / "task_xml" / "install_trendspider_export_admin.ps1"


def build_xml() -> str:
    powershell = r"powershell.exe"
    launcher = str(REPO_ROOT / "trendspider_export_launcher.ps1")
    args = f'-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{launcher}"'
    start_date = (date.today() - timedelta(days=1)).isoformat()

    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hedgeye bot TrendSpider custom-symbol export — pushes the day's stored MFR/volume/RS/diversification/quad features to TrendSpider after the EOD chain (TRENDSPIDER_ML_ROUND1_SPEC_v1).</Description>
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
    ap = argparse.ArgumentParser(description="Install TrendSpider-export Task Scheduler entry")
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
