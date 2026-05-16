# scanner_launcher.ps1 — Task Scheduler entrypoint for HedgeyeBotProactiveScanner.
# Wraps proactive_scanner.py so stdout+stderr land in C:\Projects\hedgeye-bot\logs\scanner_YYYY-MM-DD.log.
# Without this wrapper the Task Scheduler runs pythonw.exe with stdout discarded.

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

# Header so log files are easy to scan
$start = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value ""
Add-Content -Path $logFile -Value "==== scanner run $start (pid=$PID) ===="

# Run scanner, capturing both streams; PowerShell's 2>&1 merges stderr into stdout pipeline.
# Append both into the daily log file.
& $python $script --source hedgeye_active --lookback-days 7 --workers 16 --throttle 0 *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8

$exit = $LASTEXITCODE
$end  = (Get-Date).ToString('o')
Add-Content -Path $logFile -Value "==== scanner exit $end rc=$exit ===="
exit $exit
