# relative_strength_launcher.ps1 — Task Scheduler entrypoint for
# HedgeyeBotRelativeStrength. Wraps `python -m tools.relative_strength` so
# stdout+stderr land in <repo>\logs\relative_strength_YYYY-MM-DD.log.
#
# Runs once daily after the close (weekdays) to recompute the RS + sector-
# correlation snapshot BEFORE the EOD REPORT, so REPORT/REPORT NOW/DAYPACK
# render today's grid. Mirrors correlation_tracker_launcher.ps1, with two
# additions:
#   1. $repo derives from $PSScriptRoot (the folder this script lives in),
#      so it works whether the repo is C:\Projects\hedgeye-bot or elsewhere
#      — no hardcoded path to drift.
#   2. PYTHONUTF8 / PYTHONIOENCODING are forced so the 🟢/🔴 grid emojis
#      don't trip the Windows cp1252 console (UnicodeEncodeError). Railway's
#      Linux runtime never needs this; the local scheduled run does.

$ErrorActionPreference = 'Continue'

# Repo = this script's own directory (robust; siblings hardcode C:\Projects).
$repo   = $PSScriptRoot
$python = 'C:\Users\bogac\AppData\Local\Programs\Python\Python312\python.exe'

# Force UTF-8 so emoji output can't crash the run under cp1252.
$env:PYTHONUTF8       = '1'
$env:PYTHONIOENCODING = 'utf-8'

# Load .env so DATABASE_PUBLIC_URL (and any other keys) reach the child
# python process. Without this the task starts under InteractiveToken with
# no env and db_pg exits "DATABASE_PUBLIC_URL / DATABASE_URL not set".
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
$logFile = Join-Path $logDir "relative_strength_$date.log"

$start = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value ""
Add-Content -Path $logFile -Value "==== relative_strength run $start (pid=$PID) ===="

Set-Location $repo
& $python -m tools.relative_strength *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8
$rsExit = $LASTEXITCODE

# Volume signal (decelerating-dip trigger) — same daily run, right after RS.
& $python -m tools.volume_signal *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8
$volExit = $LASTEXITCODE

# RS pairwise matrix (sectors + QQQ + IWM) — same daily run, after volume.
& $python -m tools.rs_matrix *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8
$rsMatrixExit = $LASTEXITCODE

# Full correlation matrix + book risk-cluster read — same daily run, after rs_matrix.
& $python -m tools.correlation_matrix *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8
$corrExit = $LASTEXITCODE

# Surface any failure: exit nonzero if RS, volume, rs_matrix, or correlation failed (first nonzero wins).
if ($rsExit -ne 0) { $exit = $rsExit } elseif ($volExit -ne 0) { $exit = $volExit } elseif ($rsMatrixExit -ne 0) { $exit = $rsMatrixExit } elseif ($corrExit -ne 0) { $exit = $corrExit } else { $exit = 0 }
$end  = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value "==== relative_strength rc=$rsExit | volume_signal rc=$volExit | rs_matrix rc=$rsMatrixExit | correlation rc=$corrExit | exit $end rc=$exit ===="
exit $exit
