"""
tools/bridge_call.py — synchronous client for the Postgres bridge command queue.

Used by any Python code (Dispatch tasks, CLI scripts, the bot itself) to drive
PowerShell or git on Kristian's Windows machine without needing keyboard time.

USAGE
-----

    from tools.bridge_call import ps_exec, git_op

    res = ps_exec("Remove-Item .git\\index.lock -Force")
    print(res["returncode"], res["stdout"], res["stderr"])

    res = git_op(["status", "--short"])
    print(res["stdout"])

CLI
---
    python -m tools.bridge_call ps "Get-Date"
    python -m tools.bridge_call git status --short

ENVIRONMENT
-----------
DATABASE_PUBLIC_URL or DATABASE_URL — same connection used by the bridge worker.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import Json, RealDictCursor


DEFAULT_WAIT_S = float(os.environ.get("BRIDGE_CALL_WAIT_S", "120"))
POLL_S = float(os.environ.get("BRIDGE_CALL_POLL_S", "0.5"))


class BridgeError(RuntimeError):
    pass


class BridgeTimeout(BridgeError):
    pass


def _conn_str() -> str:
    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise BridgeError("DATABASE_PUBLIC_URL / DATABASE_URL not set")
    return url


def _connect():
    return psycopg2.connect(_conn_str())


def _enqueue(kind: str, payload: Dict[str, Any], timeout_s: int, requester: str) -> int:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bridge_commands (kind, payload, timeout_s, requester)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (kind, Json(payload), timeout_s, requester),
            )
            return int(cur.fetchone()[0])


def _wait(cmd_id: int, wait_s: float) -> Dict[str, Any]:
    deadline = time.time() + wait_s
    last_status = None
    while time.time() < deadline:
        with _connect() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, status, result, error FROM bridge_commands WHERE id = %s",
                    (cmd_id,),
                )
                row = cur.fetchone()
        if row is None:
            raise BridgeError(f"command {cmd_id} disappeared")
        last_status = row["status"]
        if last_status in ("done", "error", "timeout", "cancelled"):
            return dict(row)
        time.sleep(POLL_S)
    raise BridgeTimeout(f"command {cmd_id} still {last_status!r} after {wait_s}s")


def call(
    kind: str,
    payload: Dict[str, Any],
    *,
    timeout: int = 120,
    wait: Optional[float] = None,
    requester: str = "dispatch",
) -> Dict[str, Any]:
    """Enqueue a command and block until it finishes (or wait elapses)."""
    wait = wait if wait is not None else max(DEFAULT_WAIT_S, timeout + 10)
    cmd_id = _enqueue(kind, payload, timeout, requester)
    row = _wait(cmd_id, wait)
    if row["status"] != "done":
        raise BridgeError(
            f"bridge command {cmd_id} ({kind}) status={row['status']} error={row['error']!r}"
        )
    return row["result"] or {}


def ps_exec(command: str, *, cwd: Optional[str] = None, timeout: int = 120,
            wait: Optional[float] = None, requester: str = "dispatch") -> Dict[str, Any]:
    """Run an arbitrary PowerShell command. Returns {stdout, stderr, returncode, cwd}."""
    payload: Dict[str, Any] = {"command": command, "timeout": timeout}
    if cwd:
        payload["cwd"] = cwd
    return call("ps_exec", payload, timeout=timeout, wait=wait, requester=requester)


def git_op(args: List[str], *, cwd: Optional[str] = None, timeout: int = 120,
           wait: Optional[float] = None, requester: str = "dispatch") -> Dict[str, Any]:
    """Run git in C:\\Projects\\hedgeye-bot (or cwd). Returns {stdout, stderr, returncode, cmd, cwd}."""
    payload: Dict[str, Any] = {"args": list(args), "timeout": timeout}
    if cwd:
        payload["cwd"] = cwd
    return call("git_op", payload, timeout=timeout, wait=wait, requester=requester)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: List[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    sub = argv[1]
    if sub == "ps":
        if len(argv) < 3:
            print("usage: bridge_call.py ps '<powershell command>'", file=sys.stderr)
            return 2
        res = ps_exec(" ".join(argv[2:]), requester="cli")
    elif sub == "git":
        if len(argv) < 3:
            print("usage: bridge_call.py git <args...>", file=sys.stderr)
            return 2
        res = git_op(argv[2:], requester="cli")
    else:
        print(f"unknown subcommand: {sub}", file=sys.stderr)
        return 2
    json.dump(res, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return int(res.get("returncode") or 0)


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
