"""Command Bridge - daemon for whitelisted host commands. See SETUP_COMMAND_BRIDGE.md.

Production extras: heartbeat file, PID file, rotating bridge.log, hourly
Telegram pulse, self-mgmt commands (bridge_status / bridge_restart /
bridge_log_tail). Watchdog (bridge_watchdog.py) restarts on death.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

REPO_ROOT      = Path(r"C:\Projects\hedgeye-bot")
COMMANDS_ROOT  = REPO_ROOT / ".commands"
PENDING_DIR    = COMMANDS_ROOT / "pending"
RESULTS_DIR    = COMMANDS_ROOT / "results"
LOG_FILE       = COMMANDS_ROOT / "log.txt"
BRIDGE_LOG     = REPO_ROOT / "bridge.log"
HEARTBEAT_FILE = COMMANDS_ROOT / "heartbeat.txt"
PID_FILE       = COMMANDS_ROOT / "bridge.pid"
ENV_FILE       = REPO_ROOT / ".env"
POLL_INTERVAL  = float(os.environ.get("BRIDGE_POLL_INTERVAL", "5.0"))
MAX_CMD_AGE_DAYS = int(os.environ.get("BRIDGE_RESULT_RETENTION_DAYS", "30"))
TELEGRAM_HEARTBEAT_INTERVAL = float(os.environ.get("BRIDGE_TG_HEARTBEAT_INTERVAL", "3600"))
HEARTBEAT_WRITE_INTERVAL    = float(os.environ.get("BRIDGE_HEARTBEAT_WRITE_INTERVAL", "60"))


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


_load_dotenv(ENV_FILE)

COMMANDS_ROOT.mkdir(parents=True, exist_ok=True)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_root = logging.getLogger()
_root.setLevel(logging.INFO)
for _h in list(_root.handlers):
    _root.removeHandler(_h)
_rot = logging.handlers.RotatingFileHandler(BRIDGE_LOG, maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
_rot.setFormatter(_fmt)
_root.addHandler(_rot)
_legacy = logging.FileHandler(LOG_FILE, encoding="utf-8")
_legacy.setFormatter(_fmt)
_root.addHandler(_legacy)
_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(_fmt)
_root.addHandler(_console)
log = logging.getLogger("command_bridge")

START_TIME         = time.time()
COMMANDS_PROCESSED = 0
LAST_COMMAND       = None
LAST_TG_HEARTBEAT  = 0.0
LAST_HB_FILE_WRITE = 0.0

# Set to True by handle_bridge_restart. The main command loop checks this
# flag after each command's result is written and exits via os._exit(0).
# Replaces the prior threading.Timer(1.0, os._exit) pattern, which proved
# unreliable in production — process kept running for hours after restart
# was requested.
_SHUTDOWN_REQUESTED = False


def _format_uptime(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _write_pid_file():
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        log.warning(f"could not write PID file: {e}")


def _write_heartbeat_file():
    try:
        payload = f"{datetime.utcnow().isoformat()}Z pid={os.getpid()}"
        HEARTBEAT_FILE.write_text(payload, encoding="utf-8")
    except Exception as e:
        log.warning(f"could not write heartbeat: {e}")


def _heartbeat_daemon():
    """Background daemon thread: writes heartbeat every HEARTBEAT_WRITE_INTERVAL
    seconds, INDEPENDENT of the main command-processing loop. Without this
    thread, a long subprocess.run (e.g. a 5-minute python_script batch) blocks
    the main loop and the heartbeat file stays stale, which makes the
    watchdog falsely flag the bridge as dead. The daemon thread keeps the
    heartbeat fresh as long as the Python interpreter itself is alive.

    Exits when _SHUTDOWN_REQUESTED is set so it doesn't outlive its parent.
    """
    while not _SHUTDOWN_REQUESTED:
        try:
            _write_heartbeat_file()
        except Exception:
            pass
        # Tighter cadence than HEARTBEAT_WRITE_INTERVAL so a watchdog tick
        # never sees a stale file under normal operation.
        time.sleep(min(HEARTBEAT_WRITE_INTERVAL, 30))


def _start_heartbeat_thread():
    import threading
    t = threading.Thread(target=_heartbeat_daemon, daemon=True, name="heartbeat")
    t.start()
    log.info("heartbeat daemon thread started")


def _queue_depth():
    try:
        return sum(1 for _ in PENDING_DIR.glob("*.json"))
    except Exception:
        return -1


def _keep_system_awake():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        log.info("SetThreadExecutionState: keep system awake")
    except Exception as e:
        log.warning(f"SetThreadExecutionState failed: {e}")


# CREATE_NO_WINDOW = 0x08000000 — prevents Windows from briefly flashing a
# console window for each subprocess (python.exe, git.exe, etc). The bridge
# itself runs hidden, but child processes get their own console by default
# unless this flag is set; with it set, every python_script / git_* command
# is fully silent visually. Critical for the "operational while user is at
# work" requirement.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _run(argv, cwd=None, timeout=60):
    log.info(f"  exec: {' '.join(argv)} (cwd={cwd or os.getcwd()})")
    try:
        cp = subprocess.run(
            argv, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout, shell=False,
            creationflags=_CREATE_NO_WINDOW,
        )
        return {"ok": cp.returncode == 0, "returncode": cp.returncode,
                "stdout": cp.stdout[-8000:] if cp.stdout else "",
                "stderr": cp.stderr[-8000:] if cp.stderr else ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(e)}


def handle_git_status(args):
    return _run(["git", "status"], cwd=REPO_ROOT)


def handle_git_pull(args):
    return _run(["git", "pull", "origin", "master"], cwd=REPO_ROOT, timeout=120)


def handle_git_log(args):
    n = int(args.get("n", 10))
    if not (1 <= n <= 100):
        return {"ok": False, "stderr": "n must be 1..100"}
    return _run(["git", "log", "--oneline", f"-n{n}"], cwd=REPO_ROOT)


def handle_git_diff_stat(args):
    return _run(["git", "diff", "--stat"], cwd=REPO_ROOT)


def handle_git_push(args):
    message = args.get("message")
    if not message or not isinstance(message, str) or len(message) > 500:
        return {"ok": False, "stderr": "args.message required (str, max 500 chars)"}
    body = args.get("body")
    add = _run(["git", "add", "."], cwd=REPO_ROOT)
    if not add["ok"]:
        return add
    commit_argv = ["git", "commit", "-m", message]
    if body:
        if not isinstance(body, str) or len(body) > 4000:
            return {"ok": False, "stderr": "args.body must be str, max 4000 chars"}
        commit_argv += ["-m", body]
    commit = _run(commit_argv, cwd=REPO_ROOT)
    if not commit["ok"]:
        if "nothing to commit" not in (commit["stdout"] + commit["stderr"]).lower():
            return commit
    push = _run(["git", "push", "origin", "master"], cwd=REPO_ROOT, timeout=120)
    return {"ok": push["ok"], "returncode": push["returncode"],
            "stdout": (add["stdout"]+"\n"+commit["stdout"]+"\n"+push["stdout"]).strip(),
            "stderr": (add["stderr"]+"\n"+commit["stderr"]+"\n"+push["stderr"]).strip()}


def handle_railway_env(args):
    if not shutil.which("railway"):
        return {"ok": False, "stderr": "railway CLI not found on PATH"}
    return _run(["railway", "variables"], cwd=REPO_ROOT, timeout=30)


def handle_railway_env_get(args):
    if not shutil.which("railway"):
        return {"ok": False, "stderr": "railway CLI not found on PATH"}
    name = args.get("name")
    if not name or not isinstance(name, str) or not name.replace("_", "").isalnum():
        return {"ok": False, "stderr": "args.name required"}
    res = _run(["railway", "variables"], cwd=REPO_ROOT, timeout=30)
    if not res["ok"]:
        return res
    value = None
    for line in res["stdout"].splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == name:
                value = v.strip()
                break
    if value is None:
        return {"ok": False, "stderr": f"env var {name!r} not found"}
    return {"ok": True, "stdout": value, "stderr": ""}


def handle_railway_logs(args):
    if not shutil.which("railway"):
        return {"ok": False, "stderr": "railway CLI not found on PATH"}
    return _run(["railway", "logs", "--tail", "200"], cwd=REPO_ROOT, timeout=30)


def handle_telegram_send(args):
    text = args.get("text")
    if not text or not isinstance(text, str) or len(text) > 4000:
        return {"ok": False, "stderr": "args.text required (str, max 4000 chars)"}
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        if shutil.which("railway"):
            t = handle_railway_env_get({"name": "TELEGRAM_BOT_TOKEN"})
            c = handle_railway_env_get({"name": "TELEGRAM_CHAT_ID"})
            if t["ok"] and c["ok"]:
                token = t["stdout"]
                chat_id = c["stdout"]
    if not token or not chat_id:
        return {"ok": False, "stderr": "TELEGRAM_BOT_TOKEN/CHAT_ID not available"}
    try:
        import urllib.request, urllib.parse, ssl
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=data)
        # First attempt: default SSL context (validates against bundled CA bundle).
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode()
            return {"ok": True, "stdout": body[-2000:], "stderr": ""}
        except (ssl.SSLError, urllib.error.URLError) as ssl_e:
            # Corporate / antivirus HTTPS-scanning intercepts can replace the cert
            # chain with a self-signed cert that Python's default bundle does not
            # trust. Retry once with an unverified context. Heartbeat data is
            # non-sensitive ("bridge alive"); this is an acceptable fallback.
            if "CERTIFICATE" in str(ssl_e) or "SSL" in str(ssl_e):
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    body = resp.read().decode()
                return {"ok": True, "stdout": body[-2000:], "stderr": "[SSL: used unverified fallback]"}
            raise
    except Exception as e:
        return {"ok": False, "stderr": f"telegram send failed: {e}"}


def handle_python_script(args):
    ALLOWED = {"parser_risk_range.py", "price_monitor.py", "apply_schema.py", "db_pg.py", "portfolio.py", "ingest_hedgeye_downloads.py", "fetch_volsignals_transcripts.py", "probe_hedgeye_email_count.py", "corpus_ingest.py", "apply_migration.py", "probe_hedgeye_email_count_v2.py", "clear_git_lock.py", "ingest_volsignals_pdfs.py", "list_downloads.py", "download_volsignals_pdfs.py", "corpus_rag.py", "monitor_context.py", "spotgamma_client.py", "yfinance_client.py", "unified_refresh.py", "mfr_client.py", "probe_watchdog_task.py", "fix_watchdog_task.py", "hu_transcribe.py", "hu_download.py"}
    script = args.get("script")
    if script not in ALLOWED:
        return {"ok": False, "stderr": f"script {script!r} not allowed"}
    extra = args.get("args", [])
    if not isinstance(extra, list):
        return {"ok": False, "stderr": "args.args must be a list"}
    for a in extra:
        if not isinstance(a, str) or len(a) > 500:
            return {"ok": False, "stderr": "every arg must be a str <= 500 chars"}
    return _run([sys.executable, str(REPO_ROOT / script)] + list(extra), cwd=REPO_ROOT, timeout=300)


def handle_bridge_status(args):
    n = int(args.get("tail", 50))
    if not (1 <= n <= 500):
        return {"ok": False, "stderr": "tail must be 1..500"}
    log_tail = ""
    try:
        if BRIDGE_LOG.exists():
            with BRIDGE_LOG.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            log_tail = "".join(lines[-n:])
    except Exception as e:
        log_tail = f"(could not read bridge.log: {e})"
    summary = {
        "pid": os.getpid(),
        "started_at": datetime.utcfromtimestamp(START_TIME).isoformat() + "Z",
        "uptime": _format_uptime(time.time() - START_TIME),
        "uptime_seconds": int(time.time() - START_TIME),
        "commands_processed": COMMANDS_PROCESSED,
        "last_command": LAST_COMMAND,
        "queue_depth": _queue_depth(),
        "whitelist": sorted(WHITELIST),
        "log_tail": log_tail,
    }
    return {"ok": True, "stdout": json.dumps(summary, indent=2), "stderr": ""}


def handle_bridge_restart(args):
    """Set shutdown flag. Main loop checks after writing this command's result
    and calls os._exit(0). Deterministic: no Timer thread scheduling involved.
    """
    global _SHUTDOWN_REQUESTED
    log.info("bridge_restart requested — shutdown flag set; exit will fire after result is written")
    _SHUTDOWN_REQUESTED = True
    return {"ok": True, "stdout": "shutdown flag set; bridge will exit after this result is written", "stderr": ""}


def handle_bridge_log_tail(args):
    n = int(args.get("n", 100))
    if not (1 <= n <= 2000):
        return {"ok": False, "stderr": "n must be 1..2000"}
    if not BRIDGE_LOG.exists():
        return {"ok": True, "stdout": "(bridge.log does not exist yet)", "stderr": ""}
    try:
        with BRIDGE_LOG.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return {"ok": True, "stdout": "".join(lines[-n:])[-7500:], "stderr": ""}
    except Exception as e:
        return {"ok": False, "stderr": f"could not read bridge.log: {e}"}


WHITELIST = {
    "git_status": handle_git_status,
    "git_pull": handle_git_pull,
    "git_push": handle_git_push,
    "git_log": handle_git_log,
    "git_diff_stat": handle_git_diff_stat,
    "railway_env": handle_railway_env,
    "railway_env_get": handle_railway_env_get,
    "railway_logs": handle_railway_logs,
    "telegram_send": handle_telegram_send,
    "python_script": handle_python_script,
    "bridge_status": handle_bridge_status,
    "bridge_restart": handle_bridge_restart,
    "bridge_log_tail": handle_bridge_log_tail,
}


def _send_telegram_silent(text):
    try:
        res = handle_telegram_send({"text": text})
        if res.get("ok"):
            return True
        log.warning(f"telegram heartbeat failed: {res.get('stderr')}")
        return False
    except Exception as e:
        log.warning(f"telegram heartbeat exception: {e}")
        return False


def setup_dirs():
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def process_command_file(path):
    global COMMANDS_PROCESSED, LAST_COMMAND
    cmd_id = path.stem
    log.info(f"command file: {path.name}")
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.error(f"  unparseable JSON: {e}")
        write_result(cmd_id, {"ok": False, "stderr": f"unparseable JSON: {e}"})
        path.unlink(missing_ok=True)
        return
    name = spec.get("command")
    args = spec.get("args", {}) or {}
    if not isinstance(args, dict):
        write_result(cmd_id, {"ok": False, "stderr": "args must be a dict"})
        path.unlink(missing_ok=True)
        return
    if name not in WHITELIST:
        write_result(cmd_id, {"ok": False, "stderr": f"command {name!r} not in whitelist"})
        path.unlink(missing_ok=True)
        return
    try:
        result = WHITELIST[name](args) or {"ok": False, "stderr": "handler returned None"}
    except Exception as e:
        log.error(f"  handler raised: {e}")
        result = {"ok": False, "stderr": f"handler exception: {e}"}
    log.info(f"  result: ok={result.get('ok')} rc={result.get('returncode')}")
    COMMANDS_PROCESSED += 1
    LAST_COMMAND = name
    write_result(cmd_id, {"command": name, "args": args,
                          "completed_at": datetime.utcnow().isoformat() + "Z", **result})
    path.unlink(missing_ok=True)


def write_result(cmd_id, result):
    (RESULTS_DIR / f"{cmd_id}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


def cleanup_old_results():
    cutoff = time.time() - (MAX_CMD_AGE_DAYS * 86400)
    for p in RESULTS_DIR.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except Exception:
            pass


def run_loop():
    global LAST_TG_HEARTBEAT, LAST_HB_FILE_WRITE
    log.info("=" * 60)
    log.info(f"command_bridge starting - repo={REPO_ROOT} pid={os.getpid()}")
    log.info(f"bridge.log: {BRIDGE_LOG}")
    log.info(f"heartbeat: {HEARTBEAT_FILE}")
    log.info(f"pid file:  {PID_FILE}")
    log.info(f"whitelist: {sorted(WHITELIST)}")
    log.info(f"poll: {POLL_INTERVAL}s | tg heartbeat: {TELEGRAM_HEARTBEAT_INTERVAL}s")
    log.info("=" * 60)
    setup_dirs()
    _write_pid_file()
    _write_heartbeat_file()
    _start_heartbeat_thread()
    _keep_system_awake()
    startup_msg = (f"Bridge started - pid {os.getpid()} on "
                   f"{os.environ.get('COMPUTERNAME', 'host')}. "
                   f"Whitelist size: {len(WHITELIST)}.")
    _send_telegram_silent(startup_msg)
    LAST_TG_HEARTBEAT = time.time()
    last_cleanup = 0.0
    while True:
        try:
            pending = sorted(PENDING_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for p in pending:
                process_command_file(p)
                # After every command, check whether handle_bridge_restart
                # set the shutdown flag. We do this *after* the command's
                # result has been written by process_command_file, so the
                # caller always sees a 200 OK response before the bridge
                # actually dies.
                if _SHUTDOWN_REQUESTED:
                    log.info("shutdown flag set — flushing logs and calling os._exit(0)")
                    # Brief pause so any pending log handler buffers flush.
                    try:
                        for h in logging.getLogger().handlers:
                            try: h.flush()
                            except Exception: pass
                        time.sleep(0.5)
                    except Exception:
                        pass
                    os._exit(0)
            now = time.time()
            # Heartbeat is written by the _heartbeat_daemon thread now —
            # this main-loop write is a redundant safety net (no harm in
            # double-writing once per polling interval).
            if now - LAST_HB_FILE_WRITE >= HEARTBEAT_WRITE_INTERVAL:
                _write_heartbeat_file()
                LAST_HB_FILE_WRITE = now
            if now - LAST_TG_HEARTBEAT >= TELEGRAM_HEARTBEAT_INTERVAL:
                msg = (f"Bridge alive - uptime {_format_uptime(now - START_TIME)}, "
                       f"processed {COMMANDS_PROCESSED} cmds, "
                       f"queue depth {_queue_depth()}, "
                       f"last={LAST_COMMAND or 'none'}.")
                _send_telegram_silent(msg)
                LAST_TG_HEARTBEAT = now
            if now - last_cleanup > 3600:
                cleanup_old_results()
                last_cleanup = now
        except KeyboardInterrupt:
            log.info("interrupt - shutting down")
            try:
                PID_FILE.unlink(missing_ok=True)
            except Exception:
                pass
            return
        except Exception as e:
            log.error(f"loop error: {e}", exc_info=True)
        time.sleep(POLL_INTERVAL)


def write_command(name, args=None):
    setup_dirs()
    cmd_id = uuid.uuid4().hex[:12]
    spec = {"command": name, "args": args or {}}
    (PENDING_DIR / f"{cmd_id}.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"queued {cmd_id}: {name}")
    return cmd_id


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--queue":
        if len(sys.argv) < 3:
            print("usage: command_bridge.py --queue COMMAND [ARGS_JSON]")
            sys.exit(2)
        name = sys.argv[2]
        args_json = sys.argv[3] if len(sys.argv) > 3 else "{}"
        try:
            args = json.loads(args_json)
        except Exception as e:
            print(f"unparseable args JSON: {e}")
            sys.exit(2)
        write_command(name, args)
    else:
        run_loop()
