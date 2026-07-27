# scanner_launcher.ps1 â€” Task Scheduler entrypoint for HedgeyeBotProactiveScanner.
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
$mfrCli  = Join-Path $repo 'mfr_client.py'

# Cycle cadence â€” sleep N seconds between cycles. Default 900 (15 min).
$cycleSeconds = if ($env:SCAN_CYCLE_SECONDS) { [int]$env:SCAN_CYCLE_SECONDS } else { 900 }

# MFR-watchlist sync â€” once per UTC day. Picks up new tickers the operator
# added via the MFR UI (the canonical fan-out source). Guard via marker file
# so the daily refresh fires once per calendar date, idempotent across
# launcher restarts.
$mfrSyncMarker = Join-Path $logDir 'mfr_watchlist_last_sync_utc.txt'

# Header so log files are easy to scan
$bootStart = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value ""
Add-Content -Path $logFile -Value "==== scanner_launcher boot $bootStart (pid=$PID, cycle=${cycleSeconds}s) ===="

# Inner loop: scan, sleep, repeat. Task Scheduler is now supervisor only.
while ($true) {
    # --- AUTO-SYNC: first thing in the cycle, BEFORE any Python child spawns. ---
    # This launcher runs an inner loop, so a single Task Scheduler invocation drives
    # many cycles — a top-of-FILE sync would only fire once at boot and the scanner
    # would run stale code until the task restarted. Placing it here means a
    # `git reset --hard` lands strictly BETWEEN cycles, while no fan-out / shadow
    # ingest / scanner child is mid-flight; the next cycle's children then load the
    # new code. Separate process so sync_master's `exit 0` and Set-Location cannot
    # leak into this loop. A no-change cycle costs one git fetch.
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Projects\hedgeye-bot\sync_master.ps1"
    # ---------------------------------------------------------------------------

    # Refresh daily log file path each iteration so date-rollover lands cleanly.
    $date    = Get-Date -Format 'yyyy-MM-dd'
    $logFile = Join-Path $logDir "scanner_$date.log"
    $start   = (Get-Date).ToString('o')
    Add-Content -Path $logFile -Value ""
    Add-Content -Path $logFile -Value "==== scanner cycle $start (pid=$PID) ===="

    # MFR-watchlist sync â€” runs once per UTC day. The marker file holds the
    # last sync date (YYYY-MM-DD). When the day has rolled, pull the operator's
    # full MFR watchlist via /v2/asset and refresh every ticker. Idempotent.
    $todayUtc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd')
    $needSync = $true
    if (Test-Path $mfrSyncMarker) {
        $lastSync = (Get-Content $mfrSyncMarker -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
        if ($lastSync -eq $todayUtc) { $needSync = $false }
    }
    if ($needSync) {
        Add-Content -Path $logFile -Value "---- mfr_fanout: starting daily watchlist sync ($todayUtc UTC) ----"
        & $python $mfrCli --refresh-watchlist *>&1 |
            ForEach-Object { $_.ToString() } |
            Out-File -FilePath $logFile -Append -Encoding utf8
        if ($LASTEXITCODE -eq 0) {
            Set-Content -Path $mfrSyncMarker -Value $todayUtc -Encoding utf8
            Add-Content -Path $logFile -Value "---- mfr_fanout: watchlist sync complete; marker updated ----"
        } else {
            Add-Content -Path $logFile -Value "---- mfr_fanout: watchlist sync FAILED rc=$LASTEXITCODE (will retry next cycle) ----"
        }

        # Shadow range ingest - runs immediately AFTER the MFR fan-out, inside the
        # same once-per-UTC-day block, so shadow bands are computed against the MFR
        # rows just written and the validator compares like for like. Writes only
        # shadow_snapshots + shadow_validation; never touches mfr_snapshots or
        # hedgeye_risk_ranges. Non-fatal by design: a shadow failure must not stop
        # the scanner, and the MFR marker above is already set either way.
        Add-Content -Path $logFile -Value "---- shadow_ingest: starting ($todayUtc UTC) ----"
        & $python -m tools.shadow_ingest *>&1 |
            ForEach-Object { $_.ToString() } |
            Out-File -FilePath $logFile -Append -Encoding utf8
        if ($LASTEXITCODE -eq 0) {
            Add-Content -Path $logFile -Value "---- shadow_ingest: complete ----"
        } else {
            Add-Content -Path $logFile -Value "---- shadow_ingest: FAILED rc=$LASTEXITCODE (non-fatal; MFR data unaffected) ----"
        }
    }

    # Polling universe: full union of Quad slice + ETF Pro long/short + Signal
    # Strength + Portfolio Solutions + Risk Range + operator overrides.
    # Restored 2026-05-25 after the 2026-05-24 rollback inadvertently dropped
    # the ETF Pro / SS / PS coverage and left the bot polling only ~39
    # Quad-aligned tickers. 16 workers is the tested sweet spot; the polling
    # universe is typically ~120-200 tickers so a cycle takes ~1-2 min.
    & $python $script --source polling_universe --workers 16 --throttle 0 *>&1 |
        ForEach-Object { $_.ToString() } |
        Out-File -FilePath $logFile -Append -Encoding utf8

    $exit = $LASTEXITCODE
    $end  = (Get-Date).ToString('o')
    Add-Content -Path $logFile -Value "==== scanner cycle exit $end rc=$exit; sleeping ${cycleSeconds}s ===="

    Start-Sleep -Seconds $cycleSeconds
}
