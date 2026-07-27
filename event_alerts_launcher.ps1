# --- AUTO-SYNC: keep this clone on origin/master before doing any work. -------
# The runner clone only advanced on a manual `git pull`, so every deploy left the
# scheduled analytics + shadow ingest running stale code. sync_master.ps1 is
# idempotent (a no-change run is just a cheap fetch), always exits 0, and only
# bounces the command-bridge when the SHA actually moved. Run in a SEPARATE
# process so its `exit 0` and Set-Location cannot leak into this launcher.
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Projects\hedgeye-bot\sync_master.ps1"
# -----------------------------------------------------------------------------
# event_alerts_launcher.ps1 â€” env-loader wrapper for tools.event_driven_alerts.
# Invoked by Scheduled Task HedgeyeBotEventAlerts daily at 09:30 ET.
$ErrorActionPreference = 'Continue'
$repo   = 'C:\Projects\hedgeye-bot'
$python = 'C:\Users\bogac\AppData\Local\Programs\Python\Python312\python.exe'

$envFile = Join-Path $repo '.env'
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
            $idx = $line.IndexOf('=')
            $k = $line.Substring(0, $idx).Trim()
            $v = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
            if ($k) { [System.Environment]::SetEnvironmentVariable($k, $v, 'Process') }
        }
    }
}

$logDir  = Join-Path $repo 'logs'
$null    = New-Item -ItemType Directory -Force -Path $logDir
$date    = Get-Date -Format 'yyyy-MM-dd'
$logFile = Join-Path $logDir "event_alerts_$date.log"

Set-Location $repo
# Invoke python via Start-Process with explicit stdout+stderr files;
# merge back into the single date-stamped log for grep-compat with the
# pre-2026-05-26 shape. Start-Process -PassThru exposes the real
# ExitCode (read from GetExitCodeProcess, not perturbed by PowerShell
# error-record bookkeeping), so the launcher's final `exit` reflects
# what python actually returned rather than what the pipeline's last
# cmdlet $?-state happens to be.
#
# Known Task-Scheduler-level quirk on this machine (2026-05-26
# investigation): the task reports LastTaskResult=1 regardless of the
# launcher's actual exit code â€” verified by swapping in a trivial
# `exit 0` sentinel script that also reported 1. The underlying python
# DOES execute under the scheduler (log file is appended each fire,
# alerts_fired rows persist, Telegram messages send). The exit-1
# reporting is cosmetic. Most likely fix is at the system level:
#   Set-ExecutionPolicy RemoteSigned -Scope LocalMachine  (elevated)
# or reconfiguring the task principal off InteractiveToken. Surfaced
# in the commit message + report so the operator can apply.
$ErrorActionPreference = 'Continue'
$stdoutFile = Join-Path $logDir "event_alerts_stdout_$date.log"
$stderrFile = Join-Path $logDir "event_alerts_stderr_$date.log"
$proc = Start-Process -FilePath $python `
    -ArgumentList '-m', 'tools.event_driven_alerts' `
    -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $stdoutFile `
    -RedirectStandardError  $stderrFile
Get-Content $stdoutFile -ErrorAction SilentlyContinue |
    Out-File -FilePath $logFile -Append -Encoding utf8
Get-Content $stderrFile -ErrorAction SilentlyContinue |
    Out-File -FilePath $logFile -Append -Encoding utf8
exit $proc.ExitCode
