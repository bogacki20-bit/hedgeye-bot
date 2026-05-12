"""
bridge_pg_queue.py — Postgres-backed command queue worker for command_bridge.py.

DROP-IN INSTRUCTIONS
--------------------
1. Save this file alongside command_bridge.py (i.e. C:\\Projects\\hedgeye-bot\\bridge_pg_queue.py).
2. In command_bridge.py, in the `if __name__ == "__main__":` block, add these two lines
   BEFORE the existing file-queue main loop (Telegram heartbeat etc.):

       from bridge_pg_queue import start_poller
       start_poller()                # spawns a daemon thread; non-blocking

3. Restart the bridge (next time Kristian is at the keyboard).

The poller runs in a daemon thread so it doesn't interfere with the existing
Telegram / file-queue loop. It polls every POLL_INTERVAL seconds for rows where
status='pending', claims them with UPDATE ... SET status='running' (atomic via
SELECT ... FOR UPDATE SKIP LOCKED), executes, and writes results back.

ENVIRONMENT
-----------
DATABASE_URL or DATABASE_PUBLIC_URL — the Postgres connection string. Reuses
the same env var convention as the rest of the bot.

SAFETY
------
ps_exec runs arbitrary PowerShell — explicitly authorized by the user.
Per-row timeout (default 120s, override via payload['timeout'] or column timeout_s).
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "bridge_pg_queue requires psycopg2-binary. Install with: pip install psycopg2-binary"
    ) from e


log = logging.getLogger("bridge_pg_queue")
if not log.handlers:
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s [pg_queue] %(levelname)s %(message)s"))
    log.addHandler(h)
log.setLevel(logging.INFO)


POLL_INTERVAL = float(os.environ.get("BRIDGE_PG_POLL_S", "3.0"))
DEFAULT_TIMEOUT = int(os.environ.get("BRIDGE_PG_DEFAULT_TIMEOUT_S", "120"))
REPO_ROOT = os.environ.get("HEDGEYE_BOT_ROOT", r"C:\Projects\hedgeye-bot")
HOSTNAME = socket.gethostname()


def _conn_str() -> str:
    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_PUBLIC_URL / DATABASE_URL not set")
    return url


def _connect():
    return psycopg2.connect(_conn_str())


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_ps_exec(payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
    """Run an arbitrary PowerShell command. Full access, by user request."""
    command = payload.get("command")
    if not command or not isinstance(command, str):
        raise ValueError("ps_exec requires payload.command (string)")
    cwd = payload.get("cwd") or REPO_ROOT
    log.info("ps_exec: cwd=%s cmd=%s", cwd, command[:200])
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout_s,
    )
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
        "cwd": cwd,
    }


def handle_git_op(payload: Dict[str, Any], timeout_s: int) -> Dict[str, Any]:
    """Convenience wrapper for git in the bot repo."""
    args = payload.get("args")
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("git_op requires payload.args (list[str])")
    cwd = payload.get("cwd") or REPO_ROOT
    cmd = ["git"] + args
    log.info("git_op: cwd=%s cmd=%s", cwd, " ".join(cmd))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout_s,
    )
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "returncode": proc.returncode,
        "cmd": cmd,
        "cwd": cwd,
    }


HANDLERS = {
    "ps_exec": handle_ps_exec,
    "git_op": handle_git_op,
}


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

_CLAIM_SQL = """
WITH next AS (
    SELECT id
      FROM bridge_commands
     WHERE status = 'pending'
     ORDER BY created_at
     FOR UPDATE SKIP LOCKED
     LIMIT 1
)
UPDATE bridge_commands b
   SET status     = 'running',
       started_at = now(),
       host       = %s
  FROM next
 WHERE b.id = next.id
RETURNING b.id, b.kind, b.payload, b.timeout_s;
"""


def _claim_one(conn) -> Optional[Dict[str, Any]]:
    with conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(_CLAIM_SQL, (HOSTNAME,))
            row = cur.fetchone()
            return dict(row) if row else None


def _finish(conn, cmd_id: int, *, status: str, result: Any = None, error: Optional[str] = None) -> None:
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bridge_commands
                   SET status       = %s,
                       result       = %s,
                       error        = %s,
                       completed_at = now()
                 WHERE id = %s
                """,
                (status, Json(result) if result is not None else None, error, cmd_id),
            )


def _process(conn, row: Dict[str, Any]) -> None:
    cmd_id = row["id"]
    kind = row["kind"]
    payload = row["payload"] or {}
    timeout_s = int(payload.get("timeout") or row.get("timeout_s") or DEFAULT_TIMEOUT)

    handler = HANDLERS.get(kind)
    if handler is None:
        _finish(conn, cmd_id, status="error", error=f"unknown kind: {kind}")
        return

    try:
        result = handler(payload, timeout_s)
        _finish(conn, cmd_id, status="done", result=result)
        log.info("done id=%s kind=%s rc=%s", cmd_id, kind, result.get("returncode"))
    except subprocess.TimeoutExpired as e:
        _finish(conn, cmd_id, status="timeout", error=f"timeout after {timeout_s}s: {e}")
        log.warning("timeout id=%s kind=%s", cmd_id, kind)
    except Exception as e:
        _finish(conn, cmd_id, status="error", error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
        log.exception("error id=%s kind=%s", cmd_id, kind)


def poll_once() -> int:
    """One pass. Returns number of commands processed."""
    n = 0
    try:
        conn = _connect()
    except Exception:
        log.exception("pg connect failed")
        return 0
    try:
        while True:
            row = _claim_one(conn)
            if not row:
                break
            _process(conn, row)
            n += 1
            if n >= 8:   # cap per tick so we don't starve the file-queue loop
                break
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return n


def _loop() -> None:
    log.info("pg_queue poll loop started host=%s interval=%.1fs", HOSTNAME, POLL_INTERVAL)
    while True:
        try:
            poll_once()
        except Exception:
            log.exception("poll_once crashed; sleeping and retrying")
        time.sleep(POLL_INTERVAL)


_thread: Optional[threading.Thread] = None


def start_poller() -> threading.Thread:
    """Spawn the poll loop as a daemon thread. Idempotent."""
    global _thread
    if _thread and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_loop, name="bridge-pg-queue", daemon=True)
    _thread.start()
    return _thread


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Run in the foreground if invoked directly (handy for testing).
    _loop()
