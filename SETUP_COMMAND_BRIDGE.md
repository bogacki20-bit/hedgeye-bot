# Command Bridge — setup on the new laptop

The command bridge lets the Cowork agent execute whitelisted host commands on
this Windows laptop (git operations, Railway CLI, Telegram messages, specific
Python scripts). Without it, the agent is firewalled from those operations and
needs you to manually run PowerShell every time.

This is a one-time install. After it runs, the daemon stays up and the agent
queues commands by writing JSON files to `.commands/pending/`.

## Prerequisites

- Python 3.11+ on PATH (verify with `python --version`)
- Git for Windows (already installed)
- (Optional but recommended) Railway CLI:
  ```
  iwr https://docs.railway.app/install.ps1 | iex
  railway login
  cd C:\Projects\hedgeye-bot
  railway link    (select wonderful-tranquility / hedgeye-bot)
  ```

## Test the daemon interactively (5 min)

Open PowerShell and run:

```powershell
cd C:\Projects\hedgeye-bot
python command_bridge.py
```

You should see startup logging like:
```
============================================================
command_bridge starting — repo=C:\Projects\hedgeye-bot
polling: C:\Projects\hedgeye-bot\.commands\pending
results: C:\Projects\hedgeye-bot\.commands\results
whitelist: ['git_diff_stat', 'git_log', 'git_pull', 'git_push', ...]
poll interval: 5.0s
============================================================
```

Leave that window running. It polls for commands every 5 seconds.

## Smoke test — queue a command from another shell

In a SECOND PowerShell window:

```powershell
cd C:\Projects\hedgeye-bot
python command_bridge.py --queue git_status
```

Within 5 seconds the daemon picks it up, executes `git status`, and writes
the result to `C:\Projects\hedgeye-bot\.commands\results\<id>.json`.

Inspect the result:

```powershell
Get-ChildItem .commands\results\ | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | Get-Content
```

If you see git's output in the JSON, the bridge works.

## Auto-start on login (so it's always running)

Once you've smoke-tested, register a Windows Task Scheduler entry that
launches the daemon on user login and restarts it if it crashes.

Run this in PowerShell **as Administrator**:

```powershell
$action  = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "C:\Projects\hedgeye-bot\command_bridge.py" -WorkingDirectory "C:\Projects\hedgeye-bot"
$trigger = New-ScheduledTaskTrigger -AtLogon
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERNAME" -LogonType Interactive
Register-ScheduledTask -TaskName "HedgeyeBotCommandBridge" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Hedgeye Bot — command bridge daemon for the Cowork agent."
```

After this, the daemon starts automatically every time you log in. No console
window (because we use `pythonw.exe`).

To verify it's running after a logout/login cycle:
```powershell
Get-Process pythonw -ErrorAction SilentlyContinue
Get-Content C:\Projects\hedgeye-bot\.commands\log.txt -Tail 20
```

To stop it temporarily:
```powershell
Stop-Process -Name pythonw -Force
```

To unregister (if you ever want to remove it):
```powershell
Unregister-ScheduledTask -TaskName "HedgeyeBotCommandBridge" -Confirm:$false
```

## What the bridge enables

Once running, the Cowork agent can (without you typing anything):

- **`git_status`, `git_pull`, `git_push`, `git_log`, `git_diff_stat`** — full git operations on the repo. The push case takes a `message` arg (and optional `body` for multi-line commit messages).
- **`railway_env`, `railway_env_get`, `railway_logs`** — fetch Railway env vars and tail Railway service logs (so I can pull TELEGRAM_BOT_TOKEN, DATABASE_URL, etc. without you pasting).
- **`telegram_send`** — send a message via the Hedgeye Bot's Telegram. Used by scheduled tasks to ping you on completion.
- **`python_script`** — run an explicitly-allowed Python script (parser_risk_range.py, price_monitor.py, apply_schema.py, db_pg.py, portfolio.py) with optional args. Used for one-off DB queries, fixture tests, schema applications.

Anything outside this whitelist is rejected. The daemon does NOT execute
arbitrary shell strings — every handler builds its argv list explicitly.

## Logs

Daily-rotated log: `C:\Projects\hedgeye-bot\.commands\log.txt`

Tail it to see what the daemon is doing:
```powershell
Get-Content C:\Projects\hedgeye-bot\.commands\log.txt -Tail 30 -Wait
```

## Troubleshooting

**Daemon won't start: "ModuleNotFoundError"**
You may need to install requirements. The bridge uses only stdlib, so this
shouldn't happen — but if it does:
```powershell
python -m pip install --upgrade pip
```

**Telegram sends say "TELEGRAM_BOT_TOKEN/CHAT_ID not available"**
Either:
- Install Railway CLI and link the project (so the bridge can fetch from Railway), OR
- Set the env vars locally:
  ```powershell
  [Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "your_token", "User")
  [Environment]::SetEnvironmentVariable("TELEGRAM_CHAT_ID", "your_chat_id", "User")
  ```
  Then restart the daemon.

**Commands stuck in pending/ (not executing)**
- Daemon isn't running. Check Task Scheduler or run `Get-Process pythonw`.
- Check `.commands\log.txt` for errors.
- File permissions issue: ensure your user owns the `.commands` folder.

## Adding new whitelisted commands

Edit `command_bridge.py`:
1. Write a `handle_my_new_command(args: dict) -> dict` function.
2. Add `"my_new_command": handle_my_new_command` to the `WHITELIST` dict.
3. Restart the daemon.

Keep argv lists explicit. Don't accept free-form shell strings as args.
