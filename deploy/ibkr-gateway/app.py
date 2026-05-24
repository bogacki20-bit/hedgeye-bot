"""Flask app — the HTTP surface the bot's tools/price_feed_ibkr.py talks to.

Replaces voyz/ibeam's HTTP surface for the three endpoints the bot uses.
All three paths mirror IBKR's Client Portal API URLs verbatim so the bot
code is unchanged across the swap:

    GET /v1/api/iserver/auth/status
        → {"authenticated": bool, "competing": bool, "connected": bool, ...}
        Healthcheck. MUST return 200 even pre-auth (with authenticated:false)
        so the Railway healthcheck doesn't kill us during the 5-min cold boot.

    GET /v1/api/iserver/secdef/search?symbol=SPY
        → [ {conid, symbol, secType, exchange, ...}, ... ]
        Proxied as-is to IBKR_API_BASE.

    GET /v1/api/iserver/marketdata/snapshot?conids=756733&fields=31,82,...
        → [ {"31": "742.50", "82": "+1.20", ...} ]
        Proxied as-is. Tick-type number keys are what the bot parses; we
        do not transform the payload.

Auth happens in a background thread (see session.initial_login_thread). The
endpoint handlers DO NOT block on auth — they return graceful responses if
the session isn't ready yet, matching ibeam's behavior.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional

from flask import Flask, Response, jsonify, request

from auth import _placeholder_selectors
from session import (
    IbkrSession,
    initial_login_thread,
    reauth_thread,
    tickle_thread,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("ibkr-gateway")

app = Flask(__name__)
session = IbkrSession()


# ─────────────────────────── startup ──────────────────────────────────────

def _start_background_workers() -> None:
    """Read credentials from env, kick off login + tickle + reauth threads.
    Logs (loudly) if anything required is missing — but does NOT crash, so
    /v1/api/iserver/auth/status can still answer healthchecks with
    authenticated:false."""
    account      = os.environ.get("IBEAM_ACCOUNT")
    password     = os.environ.get("IBEAM_PASSWORD")
    totp_secret  = os.environ.get("IBEAM_TOTP_SECRET")

    bad_selectors = _placeholder_selectors()
    if bad_selectors:
        log.error("STARTING WITH PLACEHOLDER SELECTORS — login WILL fail. "
                  "Update SELECTORS in auth.py: %s", bad_selectors)

    if not account or not password:
        log.error("IBEAM_ACCOUNT / IBEAM_PASSWORD env vars not set — "
                  "skipping login thread. The service will run but always "
                  "report authenticated=false.")
        return

    initial_login_thread(session, account, password, totp_secret)
    tickle_thread(session)
    reauth_thread(session, account, password, totp_secret)


# Flask 3 removed before_first_request — call at import time. Under gunicorn
# this runs once per worker; we deploy with --workers 1 (see Dockerfile CMD)
# specifically so there is only one Selenium session and one cookie jar.
_start_background_workers()


# ─────────────────────────── endpoints ────────────────────────────────────

@app.get("/v1/api/iserver/auth/status")
def auth_status():
    """Mirror ibeam's response shape so tools/price_feed_ibkr.py treats us
    identically. The bot only reads `authenticated`; the rest are included
    for parity with ibeam logs and any future health introspection."""
    body = {
        "authenticated": session.authenticated,
        "competing":     False,
        "connected":     session.authenticated,
        # Extras — not part of IBKR's official schema but harmless and useful
        # when curling the gateway directly.
        "_gateway":           "hedgeye-bot-custom",
        "_login_in_progress": session.login_in_progress,
        "_last_auth_at":      session.last_auth_at,
        "_last_error":        session.last_error,
        "_placeholder_selectors": _placeholder_selectors(),
    }
    return jsonify(body), 200


def _proxy(path: str, params: dict) -> Response:
    """Forward a GET to IBKR_API_BASE + path with the captured cookies.
    Pass IBKR's response body + status through unchanged — the bot parses
    the raw IBKR payload shape and we must not reshape it.
    Pre-auth (or post-401) we return an empty list + 503 so the bot's
    None-handling fallback to yfinance kicks in cleanly."""
    if not session.authenticated:
        return Response(
            '{"error": "ibkr session not authenticated"}',
            status=503,
            mimetype="application/json",
        )
    try:
        r = session.get(path, params=params, timeout=10)
    except Exception as e:
        log.warning("proxy %s failed: %s", path, e)
        return Response(
            f'{{"error": "proxy request failed: {e!r}"}}',
            status=502,
            mimetype="application/json",
        )
    # A 401/403 from IBKR means our cookies died between checks. Mark the
    # session unauth'd so the next auth/status probe reports honestly and
    # the reauth_thread can pick up the recovery on its next tick.
    if r.status_code in (401, 403):
        session.mark_unauthenticated(f"proxy {path} got {r.status_code}")
    return Response(
        r.content,
        status=r.status_code,
        mimetype=r.headers.get("Content-Type", "application/json"),
    )


@app.get("/v1/api/iserver/secdef/search")
def secdef_search():
    return _proxy("/v1/api/iserver/secdef/search", request.args.to_dict())


@app.get("/v1/api/iserver/marketdata/snapshot")
def marketdata_snapshot():
    return _proxy("/v1/api/iserver/marketdata/snapshot", request.args.to_dict())


# Catch-all so the bot can call other IBKR Client Portal endpoints without
# us having to enumerate them. Identical proxy semantics.
@app.get("/v1/api/<path:rest>")
def passthrough(rest: str):
    return _proxy(f"/v1/api/{rest}", request.args.to_dict())


# Plain-text liveness — separate from /v1/api/iserver/auth/status so the
# operator can ping it from curl without parsing JSON.
@app.get("/_ping")
def ping():
    return "ok\n", 200, {"Content-Type": "text/plain"}


if __name__ == "__main__":
    # Local dev only. In the container we run via gunicorn (see Dockerfile).
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
