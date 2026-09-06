# --- AUTO-SYNC: keep this clone on origin/master before doing any work. ------
# Same pattern as every other launcher; sync_master never touches untracked
# files, which is what the paper-signal tools currently are.
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Projects\hedgeye-bot\sync_master.ps1"
# -----------------------------------------------------------------------------
# paper_signals_launcher.ps1 — env-loader wrapper for the nightly PAPER
# SIGNAL jobs (operator spec 8/29 round 2). Invoked by Scheduled Task
# HedgeyeBotPaperSignals daily at 17:30 ET (after the close; scanner has
# written the day's final MFR rows by then).
#
# Order matters: trend_daily materializes first (the detectors' trend
# joins read it downstream in scoring), then flush (priority signal),
# then composite. Each tool is idempotent and self-heals a 7-day window,
# so a missed night is recovered on the next run.
#
# LOGS ONLY: none of these alert, trade, or touch REPORT or the live
# entry/exit path. They append to signal_paper_fires / trend_daily.
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
$logFile = Join-Path $logDir "paper_signals_$date.log"

Set-Location $repo
Add-Content -Path $logFile -Value "==== paper_signals run $((Get-Date).ToString('o')) ===="
foreach ($mod in @('tools.trend_daily_sync', 'tools.flush_paper_log', 'tools.composite_paper_log')) {
    Add-Content -Path $logFile -Value "---- $mod ----"
    & $python -m $mod *>&1 |
        ForEach-Object { $_.ToString() } |
        Out-File -FilePath $logFile -Append -Encoding utf8
    Add-Content -Path $logFile -Value "---- $mod rc=$LASTEXITCODE ----"
}
