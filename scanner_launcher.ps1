# scanner_launcher.ps1 — Task Scheduler entrypoint for HedgeyeBotProactiveScanner.
# Wraps proactive_scanner.py so stdout+stderr land in C:\Projects\hedgeye-bot\logs\scanner_YYYY-MM-DD.log.
# Without this wrapper the Task Scheduler runs pythonw.exe with stdout discarded.
#
# As of 2026-05-24 (notifier rollback): runs an INNER 15-minute loop so a
# single Task Scheduler invocation drives the bot continuously. Task
# Scheduler is now only the supervisor (start at boot / restart on failure)
# rather than the per-cycle trigger.

$ErrorActionPreference = 'Continue'

$repo    = 'C:\Projects\hedgeye-bot'

# Load .env so DATABASE_PUBLIC_URL, ANTHROPIC_API_KEY, etc. are available to
# the child python process. Without this the scheduled task starts under
# InteractiveToken with no env and the scanner sees an empty ticker list.
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
$logFile = Join-Path $logDir "scanner_$date.log"

$python  = 'C:\Users\bogac\AppData\Local\Programs\Python\Python312\python.exe'
$script  = Join-Path $repo 'proactive_scanner.py'

# Cycle cadence — sleep N seconds between cycles. Default 900 (15 min).
$cycleSeconds = if ($env:SCAN_CYCLE_SECONDS) { [int]$env:SCAN_CYCLE_SECONDS } else { 900 }

# Header so log files are easy to scan
$bootStart = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value ""
Add-Content -Path $logFile -Value "==== scanner_launcher boot $bootStart (pid=$PID, cycle=${cycleSeconds}s) ===="

# Inner loop: scan, sleep, repeat. Task Scheduler is now supervisor only.
while ($true) {
    # Refresh daily log file path each iteration so date-rollover lands cleanly.
    $date    = Get-Date -Format 'yyyy-MM-dd'
    $logFile = Join-Path $logDir "scanner_$date.log"
    $start   = (Get-Date).ToString('o')
    Add-Content -Path $logFile -Value ""
    Add-Content -Path $logFile -Value "==== scanner cycle $start (pid=$PID) ===="

    # Active universe: monthly ∩ quarterly Quad slice from config/mfr_quad_map.yaml
    # (notifier rollback, 2026-05-24). 16 workers is the tested sweet spot.
    & $python $script --source active_slice --workers 16 --throttle 0 *>&1 |
        ForEach-Object { $_.ToString() } |
        Out-File -FilePath $logFile -Append -Encoding utf8

    $exit = $LASTEXITCODE
    $end  = (Get-Date).ToString('o')
    Add-Content -Path $logFile -Value "==== scanner cycle exit $end rc=$exit; sleeping ${cycleSeconds}s ===="

    Start-Sleep -Seconds $cycleSeconds
}
