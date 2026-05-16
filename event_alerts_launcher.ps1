# event_alerts_launcher.ps1 — env-loader wrapper for tools.event_driven_alerts.
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
& $python -m tools.event_driven_alerts *>&1 |
    ForEach-Object { $_.ToString() } |
    Out-File -FilePath $logFile -Append -Encoding utf8
