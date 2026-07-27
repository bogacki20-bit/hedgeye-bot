# --- AUTO-SYNC: keep this clone on origin/master before doing any work. -------
# The runner clone only advanced on a manual `git pull`, so every deploy left the
# scheduled analytics + shadow ingest running stale code. sync_master.ps1 is
# idempotent (a no-change run is just a cheap fetch), always exits 0, and only
# bounces the command-bridge when the SHA actually moved. Run in a SEPARATE
# process so its `exit 0` and Set-Location cannot leak into this launcher.
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Projects\hedgeye-bot\sync_master.ps1"
# -----------------------------------------------------------------------------
# warm_monitored_launcher.ps1 â€” Task Scheduler entrypoint for HedgeyeBotWarmMonitored.
# Wraps tools.warm_all_monitored so stdout+stderr land in
# C:\Projects\hedgeye-bot\logs\warm_monitored_YYYY-MM-DD.log.
#
# Runs nightly to drive unified_refresh.refresh_all_for_tickers across the full
# monitored_tickers list so the morning premarket SG sweep sees warm MFR + Yahoo
# data and a populated SG refresh queue.

$ErrorActionPreference = 'Continue'

$repo = 'C:\Projects\hedgeye-bot'

# Load .env so DATABASE_PUBLIC_URL, ANTHROPIC_API_KEY, MFR_TOKEN, etc. are
# available to the child python process. Without this the scheduled task
# starts under InteractiveToken with no env and tools.list_monitored_tickers
# exits with "DATABASE_PUBLIC_URL / DATABASE_URL not set".
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
$logFile = Join-Path $logDir "warm_monitored_$date.log"

$python  = 'C:\Users\bogac\AppData\Local\Programs\Python\Python312\python.exe'

# Header so log files are easy to scan
$start = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value ""
Add-Content -Path $logFile -Value "==== warm_monitored run $start (pid=$PID) ===="

# Run as a module so tools/__init__.py is honored. cwd=$repo so relative
# imports inside the module land correctly.
Set-Location $repo
& $python -m tools.warm_all_monitored `
    --priority all `
    --batch-size 25 `
    --throttle 5 `
    --capture-type warm_sweep `
    *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8

$exit = $LASTEXITCODE
$end  = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value "==== warm_monitored exit $end rc=$exit ===="
exit $exit
