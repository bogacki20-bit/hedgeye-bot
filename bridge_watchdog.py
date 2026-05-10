"""Bridge watchdog - one-shot. Runs every 5 min via Task Scheduler.

Checks .commands/heartbeat.txt + .commands/bridge.pid. If the bridge looks
dead (heartbeat older than HEARTBEAT_STALE_SECONDS, or PID missing/dead, or
neither file exists), kills any zombie and relaunches command_bridge.py
detached. Sends a Telegram alert on relaunch. Exits silently when healthy.

Designed to be cheap: no daemon process, no state, just files. Belt-and-
braces for the Task-Scheduler-at-logon entry; if logon trigger ever misfires
this watchdog notices within 5 min.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT      = Path(r"C:\Projects\hedgeye-bot")
COMMANDS_ROOT  = REPO_ROOT / ".commands"
HEARTBEAT_FILE = COMMANDS_ROOT / "heartbeat.txt"
PID_FILE       = COMMANDS_ROOT / "bridge.pid"
BRIDGE_LOG     = REPO_ROOT / "bridge.log"
WATCHDOG_LOG   = REPO_ROOT / "bridge.watchdog.log"
ENV_FILE       = REPO_ROOT / ".env"
BRIDGE_SCRIPT  = REPO_ROOT / "command_bridge.py"

HEARTBEAT_STALE_SECONDS = int(os.environ.get("BRIDGE_STALE_SECONDS", "180"))

# Suppress console-window flashes for every subprocess call. The watchdog runs
# every ~5 min via Task Scheduler; without this flag the tasklist / taskkill
# / railway / urlopen subprocesses each pop a black window briefly. Combined
# with the Task Scheduler entry using pyw.exe (windowless Python), this gives
# a fully silent watchdog cycle on healthy runs.
CREATE_NO_WINDOW = 0x08000000


def _log(msg):
    line = f"{datetime.utcnow().isoformat()}Z [watchdog] {msg}\n"
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line.rstrip())


def _load_dotenv(path):
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


def _telegram_send(text):
    """Best-effort. Pulls token/chat_id from env or via railway CLI."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if (not token or not chat_id):
        # Try railway CLI as a fallback (same logic as the bridge).
        try:
            import shutil
            if shutil.which("railway"):
                for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
                    if not os.environ.get(var):
                        cp = subprocess.run(
                            ["railway", "variables"], cwd=str(REPO_ROOT),
                            capture_output=True, text=True, timeout=20, shell=False,
                            creationflags=CREATE_NO_WINDOW,
                        )
                        if cp.returncode == 0:
                            for line in cp.stdout.splitlines():
                                if "=" in line:
                                    k, _, v = line.partition("=")
                                    if k.strip() == var:
                                        os.environ[var] = v.strip()
                                        break
                token = os.environ.get("TELEGRAM_BOT_TOKEN")
                chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        except Exception as e:
            _log(f"railway env fetch failed: {e}")
    if not token or not chat_id:
        _log("telegram creds not available; skipping alert")
        return False
    try:
        import urllib.request, urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15):
            return True
    except Exception as e:
        _log(f"telegram send failed: {e}")
        return False


def _pid_alive(pid):
    """Best-effort PID-alive check on Windows via tasklist."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        cp = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=15, shell=False,
            creationflags=CREATE_NO_WINDOW,
        )
        return cp.returncode == 0 and (str(pid) in cp.stdout)
    except Exception:
        return False


def _kill_pid(pid):
    if not isinstance(pid, int) or pid <= 0:
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, text=True, timeout=15, shell=False,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception:
        pass


def _read_pid():
    try:
        if PID_FILE.exists():
            return int(PID_FILE.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return 0
    return 0


def _heartbeat_age_seconds():
    """Return None if file missing/unparseable, else age in seconds."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        text = HEARTBEAT_FILE.read_text(encoding="utf-8").strip()
        ts_str = text.split()[0].rstrip("Z")
        ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age
    except Exception as e:
        _log(f"heartbeat parse failed: {e}")
        return None


def _bridge_is_healthy():
    age = _heartbeat_age_seconds()
    pid = _read_pid()
    if age is None:
        return False, "heartbeat file missing/unparseable"
    if age > HEARTBEAT_STALE_SECONDS:
        return False, f"heartbeat stale ({int(age)}s old, threshold {HEARTBEAT_STALE_SECONDS}s)"
    if pid and not _pid_alive(pid):
        return False, f"PID {pid} not running"
    return True, f"healthy (heartbeat {int(age)}s old, pid {pid})"


STARTUP_LOG    = REPO_ROOT / "bridge.startup.log"


def _relaunch_bridge():
    """Detach a new python command_bridge.py process.

    stdout/stderr go to bridge.startup.log (NOT bridge.log) -- Windows refuses
    a concurrent open of bridge.log when the new bridge uses RotatingFileHandler.
    Once the new bridge starts up cleanly, all real logs flow through Python's
    logger to bridge.log; bridge.startup.log only captures interpreter-level
    failures (missing python, syntax errors, import errors).
    """
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    # CREATE_NO_WINDOW guarantees no console window for python.exe relaunch.
    # DETACHED_PROCESS alone *can* leave a brief flash on some Windows builds.
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    log_handle = open(STARTUP_LOG, "a", encoding="utf-8")
    # Prefer pythonw.exe (windowless) over python.exe to ensure the relaunched
    # bridge has no console at all. Fall back to sys.executable if pyw isn't
    # alongside python.exe.
    py = sys.executable
    if py.lower().endswith("python.exe"):
        candidate = py[:-len("python.exe")] + "pythonw.exe"
        from pathlib import Path as _P
        if _P(candidate).exists():
            py = candidate
    elif py.lower().endswith("py.exe"):
        candidate = py[:-len("py.exe")] + "pyw.exe"
        from pathlib import Path as _P
        if _P(candidate).exists():
            py = candidate
    proc = subprocess.Popen(
        [py, str(BRIDGE_SCRIPT)],
        cwd=str(REPO_ROOT),
        stdout=log_handle,
        stderr=log_handle,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )
    return proc.pid


def main():
    _load_dotenv(ENV_FILE)
    healthy, reason = _bridge_is_healthy()
    if healthy:
        # Silent on healthy runs.
        return 0

    _log(f"bridge unhealthy: {reason} -- relaunching")
    last_pid = _read_pid()
    if last_pid and _pid_alive(last_pid):
        _log(f"killing stale pid {last_pid}")
        _kill_pid(last_pid)
        time.sleep(1.0)
    try:
        new_pid = _relaunch_bridge()
        _log(f"relaunched bridge (new pid {new_pid})")
        last_seen = "unknown"
        try:
            if HEARTBEAT_FILE.exists():
                last_seen = HEARTBEAT_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        host = os.environ.get("COMPUTERNAME", "host")
        _telegram_send(
            f"Watchdog restarted bridge on {host} - reason: {reason}. "
            f"Last heartbeat: {last_seen}. New pid: {new_pid}."
        )
        return 0
    except Exception as e:
        _log(f"relaunch failed: {e}")
        try:
            _telegram_send(f"Watchdog FAILED to relaunch bridge: {e} -- manual intervention needed.")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
