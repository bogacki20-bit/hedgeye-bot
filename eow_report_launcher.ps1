# --- AUTO-SYNC: keep this clone on origin/master before doing any work. -------
# The runner clone only advanced on a manual `git pull`, so every deploy left the
# scheduled analytics + shadow ingest running stale code. sync_master.ps1 is
# idempotent (a no-change run is just a cheap fetch), always exits 0, and only
# bounces the command-bridge when the SHA actually moved. Run in a SEPARATE
# process so its `exit 0` and Set-Location cannot leak into this launcher.
& powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Projects\hedgeye-bot\sync_master.ps1"
# -----------------------------------------------------------------------------
# eow_report_launcher.ps1 â€” env-loader wrapper for tools.generate_eow_report.
# Invoked by Scheduled Task HedgeyeBotEOWReport Fridays at 17:00 ET.
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
$logFile = Join-Path $logDir "eow_report_$date.log"

Set-Location $repo
& $python -m tools.generate_eow_report --week current *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8
