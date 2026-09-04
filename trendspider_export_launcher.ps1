# --- AUTO-SYNC: keep this clone on origin/master before doing any work. -------
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Projects\hedgeye-bot\sync_master.ps1"
# -----------------------------------------------------------------------------
# trendspider_export_launcher.ps1 - Task Scheduler entrypoint for
# HedgeyeBotTrendspiderExport. Wraps `python trendspider_export.py`
# (incremental mode) so stdout+stderr land in <repo>\logs\trendspider_export_YYYY-MM-DD.log.
#
# Runs once daily after the EOD producers (RS/volume/diversification at 16:20,
# correlation tracker 16:30, paper signals 17:30) so today's stored rows are
# in Postgres before export. SpotGamma canary run was dropped (operator
# decision 1, 2026-09-04 - SG corpus dead since 08-06).

$ErrorActionPreference = 'Continue'

$repo   = $PSScriptRoot
$python = 'C:\Users\bogac\AppData\Local\Programs\Python\Python312\python.exe'

$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'

# Load .env so DATABASE_PUBLIC_URL + TRENDSPIDER_* reach the child process.
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
$logFile = Join-Path $logDir "trendspider_export_$date.log"

$start = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value ""
Add-Content -Path $logFile -Value "==== trendspider_export run $start (pid=$PID) ===="

Set-Location $repo
& $python trendspider_export.py *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8
$exit = $LASTEXITCODE

$end = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value "==== trendspider_export exit $end rc=$exit ===="
exit $exit
