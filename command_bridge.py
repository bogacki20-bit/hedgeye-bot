"""
Command Bridge — local daemon that lets the Cowork agent execute whitelisted
host commands on this Windows laptop.

WHY THIS EXISTS
---------------
The Cowork agent's bash sandbox runs Linux and is firewalled from most external
hosts (no GitHub, no Railway, no Telegram). The agent can read/edit files on
the host filesystem but can't run host PowerShell, git operations on the
mounted repo (Windows index.lock issues), or Railway CLI commands.

This daemon bridges that gap. The agent writes a small JSON command file to
`.commands/pending/`. The daemon polls that folder every 5 seconds, validates
the command against a strict whitelist, executes it on the host (full Windows
shell + network access), and writes a JSON result file to `.commands/results/`.

USAGE
-----
1. Install (one-time):  python -m pip install --user requests
2. Run interactively:   python command_bridge.py
3. Run as background:   pythonw command_bridge.py     (no console window)
4. Auto-start on logon: register via Windows Task Scheduler. See the
                        accompanying SETUP_COMMAND_BRIDGE.md doc.

SECURITY
--------
- Strict command whitelist in WHITELIST. Anything not in the dict is rejected.
- All commands run as the current user (not admin).
- Args are validated against per-command schemas — no shell-string concatenation.
- Every command is logged to .commands/log.txt with timestamp + outcome.
- The daemon does NOT execute arbitrary shell strings. Each whitelisted handler
  builds its own argv list explicitly.

To extend: add a new entry to WHITELIST with a handler function. Don't accept
free-form shell strings.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# ─────────────────────────── Config ───────────────────────────

REPO_ROOT      = Path(r"C:\Projects\hedgeye-bot")
COMMANDS_ROOT  = REPO_ROOT / ".commands"
PENDING_DIR    = COMMANDS_ROOT / "pending"
RESULTS_DIR    = COMMANDS_ROOT / "results"
LOG_FILE       = COMMANDS_ROOT / "log.txt"
POLL_INTERVAL  = float(os.environ.get("BRIDGE_POLL_INTERVAL", "5.0"))   # seconds
MAX_CMD_AGE_DAYS = int(os.environ.get("BRIDGE_RESULT_RETENTION_DAYS", "30"))

# Set up file + console logging
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("command_bridge")


# ─────────────────────────── Whitelist ───────────────────────────

def _run(argv: list[str], cwd: Path | None = None, timeout: int = 60) -> dict:
    """Run a subprocess with strict argv (no shell). Returns a result dict."""
    log.info(f"  exec: {' '.join(argv)} (cwd={cwd or os.getcwd()})")
    try:
        cp = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,  # never shell=True. argv only.
        )
        return {
            "ok":         cp.returncode == 0,
            "returncode": cp.returncode,
            "stdout":     cp.stdout[-8000:] if cp.stdout else "",
            "stderr":     cp.stderr[-8000:] if cp.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": f"timeout after {timeout}s"}
    except Exception as e:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": str(e)}


def handle_git_status(args: dict) -> dict:
    """git status in REPO_ROOT."""
    return _run(["git", "status"], cwd=REPO_ROOT)


def handle_git_pull(args: dict) -> dict:
    """git pull origin master."""
    return _run(["git", "pull", "origin", "master"], cwd=REPO_ROOT, timeout=120)


def handle_git_log(args: dict) -> dict:
    """git log of last N commits (default 10)."""
    n = int(args.get("n", 10))
    if not (1 <= n <= 100):
        return {"ok": False, "stderr": "n must be 1..100"}
    return _run(["git", "log", "--oneline", f"-n{n}"], cwd=REPO_ROOT)


def handle_git_diff_stat(args: dict) -> dict:
    """git diff --stat (uncommitted)."""
    return _run(["git", "diff", "--stat"], cwd=REPO_ROOT)


def handle_git_push(args: dict) -> dict:
    """git add . && git commit -m MSG && git push.

    Requires args.message (commit message). Optional args.body (commit body).
    """
    message = args.get("message")
    if not message or not isinstance(message, str) or len(message) > 500:
        return {"ok": False, "stderr": "args.message required (str, max 500 chars)"}
    body = args.get("body")
    add  = _run(["git", "add", "."], cwd=REPO_ROOT)
    if not add["ok"]:
        return add
    commit_argv = ["git", "commit", "-m", message]
    if body:
        if not isinstance(body, str) or len(body) > 4000:
            return {"ok": False, "stderr": "args.body must be str, max 4000 chars"}
        commit_argv += ["-m", body]
    commit = _run(commit_argv, cwd=REPO_ROOT)
    if not commit["ok"]:
        # No changes to commit is acceptable — proceed to push (no-op).
        if "nothing to commit" not in (commit["stdout"] + commit["stderr"]).lower():
            return commit
    push = _run(["git", "push", "origin", "master"], cwd=REPO_ROOT, timeout=120)
    return {
        "ok": push["ok"],
        "returncode": push["returncode"],
        "stdout": (add["stdout"] + "\n" + commit["stdout"] + "\n" + push["stdout"]).strip(),
        "stderr": (add["stderr"] + "\n" + commit["stderr"] + "\n" + push["stderr"]).strip(),
    }


def handle_railway_env(args: dict) -> dict:
    """Fetch Railway env vars for the current service. Requires `railway` CLI on PATH."""
    if not shutil.which("railway"):
        return {"ok": False, "stderr": "railway CLI not found on PATH. Install from https://docs.railway.app/develop/cli."}
    # `railway variables` prints env vars in current linked service.
    return _run(["railway", "variables"], cwd=REPO_ROOT, timeout=30)


def handle_railway_env_get(args: dict) -> dict:
    """Get the value of a single Railway env var by name (sanitized output).

    Returns ONLY the value, not the full env list. Helps prevent unintended
    secret exposure when only one var is needed.
    """
    if not shutil.which("railway"):
        return {"ok": False, "stderr": "railway CLI not found on PATH"}
    name = args.get("name")
    if not name or not isinstance(name, str) or not name.replace("_", "").isalnum():
        return {"ok": False, "stderr": "args.name required (alphanumeric + underscores only)"}
    # railway variables prints all; we filter for the requested var.
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


def handle_railway_logs(args: dict) -> dict:
    """Tail recent Railway service logs."""
    if not shutil.which("railway"):
        return {"ok": False, "stderr": "railway CLI not found on PATH"}
    return _run(["railway", "logs", "--tail", "200"], cwd=REPO_ROOT, timeout=30)


def handle_telegram_send(args: dict) -> dict:
    """Send a Telegram message via the bot. Requires args.text.

    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID via railway env get (so
    secrets don't have to live on this laptop). Falls back to local env vars
    on this machine if Railway CLI isn't available.
    """
    text = args.get("text")
    if not text or not isinstance(text, str) or len(text) > 4000:
        return {"ok": False, "stderr": "args.text required (str, max 4000 chars)"}

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        # Fallback: pull from Railway
        if shutil.which("railway"):
            t = handle_railway_env_get({"name": "TELEGRAM_BOT_TOKEN"})
            c = handle_railway_env_get({"name": "TELEGRAM_CHAT_ID"})
            if t["ok"] and c["ok"]:
                token = t["stdout"]
                chat_id = c["stdout"]
    if not token or not chat_id:
        return {"ok": False, "stderr": "TELEGRAM_BOT_TOKEN/CHAT_ID not available (set env or install railway CLI + link)"}

    try:
        import urllib.request
        import urllib.parse
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
        return {"ok": True, "stdout": body[-2000:], "stderr": ""}
    except Exception as e:
        return {"ok": False, "stderr": f"telegram send failed: {e}"}


def handle_python_script(args: dict) -> dict:
    """Run an explicitly-allowed Python script in the repo with optional args.

    Allowed scripts list is hardcoded — anything else is rejected.
    """
    ALLOWED_SCRIPTS = {
        "parser_risk_range.py",
        "price_monitor.py",
        "apply_schema.py",
        "db_pg.py",
        "portfolio.py",
    }
    script = args.get("script")
    if script not in ALLOWED_SCRIPTS:
        return {"ok": False, "stderr": f"script {script!r} not in allowed list ({sorted(ALLOWED_SCRIPTS)})"}
    extra_args = args.get("args", [])
    if not isinstance(extra_args, list):
        return {"ok": False, "stderr": "args.args must be a list of strings"}
    for a in extra_args:
        if not isinstance(a, str) or len(a) > 500:
            return {"ok": False, "stderr": "every arg must be a str <= 500 chars"}
    return _run([sys.executable, str(REPO_ROOT / script)] + list(extra_args),
                cwd=REPO_ROOT, timeout=300)


# Map command name → handler. Anything not here is rejected.
WHITELIST: dict[str, callable] = {
    "git_status":       handle_git_status,
    "git_pull":         handle_git_pull,
    "git_push":         handle_git_push,
    "git_log":          handle_git_log,
    "git_diff_stat":    handle_git_diff_stat,
    "railway_env":      handle_railway_env,
    "railway_env_get":  handle_railway_env_get,
    "railway_logs":     handle_railway_logs,
    "telegram_send":    handle_telegram_send,
    "python_script":    handle_python_script,
}


# ─────────────────────────── Daemon ───────────────────────────

def setup_dirs() -> None:
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)


def process_command_file(path: Path) -> None:
    """Read, validate, execute, and write result for a single command file."""
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
        log.error("  args must be a dict")
        write_result(cmd_id, {"ok": False, "stderr": "args must be a dict"})
        path.unlink(missing_ok=True)
        return
    if name not in WHITELIST:
        log.error(f"  command {name!r} not in whitelist")
        write_result(cmd_id, {
            "ok": False,
            "stderr": f"command {name!r} not in whitelist. Allowed: {sorted(WHITELIST)}",
        })
        path.unlink(missing_ok=True)
        return

    try:
        result = WHITELIST[name](args) or {"ok": False, "stderr": "handler returned None"}
    except Exception as e:
        log.error(f"  handler raised: {e}")
        result = {"ok": False, "stderr": f"handler exception: {e}"}

    log.info(f"  result: ok={result.get('ok')} rc={result.get('returncode')}")
    write_result(cmd_id, {
        "command":     name,
        "args":        args,
        "completed_at": datetime.utcnow().isoformat() + "Z",
        **result,
    })
    path.unlink(missing_ok=True)


def write_result(cmd_id: str, result: dict) -> None:
    out_path = RESULTS_DIR / f"{cmd_id}.json"
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")


def cleanup_old_results() -> None:
    """Delete result files older than MAX_CMD_AGE_DAYS to keep the folder lean."""
    now = time.time()
    cutoff = now - (MAX_CMD_AGE_DAYS * 86400)
    for p in RESULTS_DIR.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except Exception:
            pass


def run_loop() -> None:
    log.info("=" * 60)
    log.info(f"command_bridge starting — repo={REPO_ROOT}")
    log.info(f"polling: {PENDING_DIR}")
    log.info(f"results: {RESULTS_DIR}")
    log.info(f"whitelist: {sorted(WHITELIST)}")
    log.info(f"poll interval: {POLL_INTERVAL}s")
    log.info("=" * 60)

    setup_dirs()
    last_cleanup = 0

    while True:
        try:
            # Process any pending commands. Sort so older ones go first.
            pending = sorted(PENDING_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for p in pending:
                process_command_file(p)

            # Periodic cleanup of old results
            if time.time() - last_cleanup > 3600:
                cleanup_old_results()
                last_cleanup = time.time()

        except KeyboardInterrupt:
            log.info("interrupt — shutting down")
            return
        except Exception as e:
            log.error(f"loop error: {e}", exc_info=True)

        time.sleep(POLL_INTERVAL)


# ─────────────────────────── CLI helpers ───────────────────────────

def write_command(name: str, args: dict | None = None) -> str:
    """Convenience: write a pending command from this same script. Returns command id."""
    setup_dirs()
    cmd_id = uuid.uuid4().hex[:12]
    spec = {"command": name, "args": args or {}}
    path = PENDING_DIR / f"{cmd_id}.json"
    path.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(f"queued {cmd_id}: {name}")
    return cmd_id


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--queue":
        # Quick-and-dirty manual queueing: command_bridge.py --queue git_status
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
