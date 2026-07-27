# sync_master.ps1 — keep the runner clone (C:\Projects\hedgeye-bot) on origin/master.
#
# The scheduled analytics, the shadow ingest and the long-running command-bridge all
# execute from this clone, which previously only advanced on a manual `git pull`.
# Every deploy therefore left it stale — it sat 3 commits behind and would have
# silently never run the shadow ingest. This runs FIRST inside every launcher .ps1,
# so any scheduled cycle picks up new code automatically.
#
# Guarantees:
#   * ALWAYS exits 0. A network blip must never block the analytics — on a failed
#     fetch we log a warning and let the caller run on current code.
#   * NEVER destroys work. `git reset --hard` only runs when there are no TRACKED
#     modifications. Untracked files are ignored deliberately: reset --hard does not
#     touch them, and this clone legitimately carries ~10 untracked scratch files
#     (PROJECT_STATE.md, apply_*.py, data/portfolio_uploads/*.csv). Treating those as
#     "dirty" would block the sync forever and leave the hazard unfixed.
#   * ONLY bounces the bridge when the SHA actually moved. A no-change run is a cheap
#     fetch and nothing else.

$ErrorActionPreference = 'Continue'

$repo    = 'C:\Projects\hedgeye-bot'
$logDir  = Join-Path $repo 'logs'
$utcNow  = (Get-Date).ToUniversalTime()
$logFile = Join-Path $logDir ("sync_" + $utcNow.ToString('yyyy-MM-dd') + ".log")

$null = New-Item -ItemType Directory -Force -Path $logDir -ErrorAction SilentlyContinue

function Write-Sync([string]$msg) {
    $ts   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $line = "$ts [sync] $msg"
    try { Add-Content -Path $logFile -Value $line -Encoding utf8 } catch { }
    Write-Output $line
}

if (-not (Test-Path (Join-Path $repo '.git'))) {
    Write-Sync "ERROR $repo is not a git repo - skipping sync"
    exit 0
}
Set-Location $repo

# --- current SHA -----------------------------------------------------------
$old = (git rev-parse --short HEAD 2>$null)
if (-not $old) {
    Write-Sync "ERROR cannot read HEAD - skipping sync"
    exit 0
}

# --- fetch (never fatal) ---------------------------------------------------
git fetch origin master --quiet 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Sync "WARN  fetch failed (offline?) - running on current code SHA=$old"
    exit 0
}

# --- refuse to clobber real edits -----------------------------------------
# Tracked modifications only. Untracked files are safe from reset --hard and are
# expected in this clone, so they must not veto the sync.
$dirty = git status --porcelain --untracked-files=no
if ($dirty) {
    Write-Sync "!!!!! DIRTY WORKING TREE - REFUSING TO RESET. This clone should carry"
    Write-Sync "!!!!! no manual edits; surfacing rather than destroying them."
    foreach ($d in $dirty) { Write-Sync "!!!!!    $d" }
    Write-Sync "!!!!! Resolve by hand. SHA unchanged=$old"
    exit 0
}

# --- compare + reset -------------------------------------------------------
$remote = (git rev-parse --short origin/master 2>$null)
if (-not $remote) {
    Write-Sync "WARN  cannot resolve origin/master - running on current code SHA=$old"
    exit 0
}
if ($remote -eq $old) {
    Write-Sync "no-change SHA=$old"
    exit 0
}

git reset --hard -q origin/master
if ($LASTEXITCODE -ne 0) {
    Write-Sync "ERROR reset --hard failed rc=$LASTEXITCODE - SHA stays $old"
    exit 0
}
$new = (git rev-parse --short HEAD 2>$null)
Write-Sync "synced $old->$new"

# --- bounce the bridge, ONLY because the SHA moved -------------------------
# command_bridge.py is long-running and holds its modules in memory, so new code
# does not take effect until the process restarts. Killing it is enough:
# bridge_watchdog.py runs every ~5 min, sees a stale heartbeat (>180s) or a dead
# PID, and relaunches it detached. Expect up to ~5 min before it is back.
$bridge = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='pyw.exe'" -ErrorAction SilentlyContinue |
          Where-Object { $_.CommandLine -and $_.CommandLine -match 'command_bridge\.py' }
if ($bridge) {
    foreach ($p in $bridge) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Sync "bounced command_bridge pid=$($p.ProcessId) - watchdog relaunches within ~5 min"
        } catch {
            Write-Sync "WARN  could not stop bridge pid=$($p.ProcessId): $($_.Exception.Message)"
        }
    }
} else {
    Write-Sync "bridge not running - nothing to bounce (watchdog will start it)"
}

Write-Sync "done SHA=$new"
exit 0
