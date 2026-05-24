"""In-memory IBKR session state + the threads that keep it alive.

Holds the cookie jar produced by auth.login(), exposes a `requests.Session`
configured with those cookies, and runs two daemon threads:

  - tickle every TICKLE_INTERVAL seconds to prevent idle timeout
  - re-auth every REAUTH_INTERVAL seconds to refresh before the ~24h limit

Container restart wipes everything and triggers a fresh login — that's
fine, ibeam did the same. No disk persistence.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import requests

from auth import IBKR_API_BASE, AuthResult, login

log = logging.getLogger(__name__)

# How often to hit /v1/api/tickle. IBKR's documented idle timeout is
# several minutes; 60s gives plenty of margin.
TICKLE_INTERVAL = int(os.environ.get("IBKR_TICKLE_INTERVAL", "60"))
# How often to redo the full Selenium login. IBKR sessions die in the
# daily maintenance window (varies by region, ~23:45-00:15 ET); 23h
# means we re-auth at roughly the same time each day, just before that.
REAUTH_INTERVAL = int(os.environ.get("IBKR_REAUTH_INTERVAL", str(23 * 3600)))
# How long to back off after a failed login before retrying.
LOGIN_RETRY_DELAY = int(os.environ.get("IBKR_LOGIN_RETRY_DELAY", "300"))


class IbkrSession:
    """Thread-safe holder for the cookie jar + last auth attempt status.

    Single instance is created in app.py and shared with request handlers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._http = requests.Session()
        self._http.headers.update({
            "User-Agent": "HedgeyeBot-CustomIBKRGateway/1.0",
            "Accept": "application/json",
        })
        self._authenticated = False
        self._last_auth_at: Optional[float] = None
        self._last_error: Optional[str] = None
        self._final_url: Optional[str] = None
        self._login_in_progress = False

    # ── public read-only state, surfaced via /v1/api/iserver/auth/status ──

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_auth_at(self) -> Optional[float]:
        return self._last_auth_at

    @property
    def login_in_progress(self) -> bool:
        return self._login_in_progress

    # ── auth lifecycle ─────────────────────────────────────────────────────

    def apply_auth_result(self, result: AuthResult) -> None:
        """Atomically swap in the cookie jar produced by a successful login,
        or record the failure for the status endpoint to report."""
        with self._lock:
            self._login_in_progress = False
            if result.ok:
                jar = requests.cookies.RequestsCookieJar()
                for c in result.cookies:
                    # Selenium cookie dicts use {name, value, domain, path,
                    # secure, expiry, ...}. requests' cookies.set accepts
                    # the key positional args; pass the rest as kwargs.
                    jar.set(
                        c["name"], c["value"],
                        domain=c.get("domain"),
                        path=c.get("path", "/"),
                        secure=c.get("secure", False),
                    )
                self._http.cookies = jar
                self._authenticated = True
                self._last_auth_at = time.time()
                self._last_error = None
                self._final_url = result.final_url
                log.info("session updated: %d cookies, authenticated=True",
                         len(result.cookies))
            else:
                self._authenticated = False
                self._last_error = result.error
                log.error("session NOT authenticated: %s", result.error)

    def mark_login_starting(self) -> None:
        with self._lock:
            self._login_in_progress = True

    def mark_unauthenticated(self, reason: str) -> None:
        """Flip the auth flag off — used by the tickle thread when it gets
        a 401/403 indicating the session is gone."""
        with self._lock:
            self._authenticated = False
            self._last_error = reason
            log.warning("session marked unauthenticated: %s", reason)

    # ── proxied HTTP for the REST endpoints ────────────────────────────────

    def get(self, path: str, params: Optional[dict] = None,
            timeout: int = 10) -> requests.Response:
        """GET <IBKR_API_BASE><path> with the captured cookies. Caller
        handles non-200 / json decode — we just return the Response."""
        url = IBKR_API_BASE.rstrip("/") + path
        return self._http.get(url, params=params, timeout=timeout)


# ─────────────────────────── background workers ────────────────────────────

def _login_once(session: IbkrSession, account: str, password: str,
                totp_secret: Optional[str]) -> None:
    session.mark_login_starting()
    result = login(account, password, totp_secret)
    session.apply_auth_result(result)


def initial_login_thread(session: IbkrSession, account: str,
                         password: str, totp_secret: Optional[str]) -> threading.Thread:
    """Kick off the first login in a daemon thread so Flask can start
    serving / answering healthchecks immediately. Returns the thread so
    the caller can log when it starts."""
    def run():
        log.info("initial login thread starting")
        while True:
            _login_once(session, account, password, totp_secret)
            if session.authenticated:
                return
            log.warning("initial login failed; retrying in %ds",
                        LOGIN_RETRY_DELAY)
            time.sleep(LOGIN_RETRY_DELAY)

    t = threading.Thread(target=run, name="ibkr-initial-login", daemon=True)
    t.start()
    return t


def tickle_thread(session: IbkrSession) -> threading.Thread:
    """Hit /v1/api/tickle every TICKLE_INTERVAL to keep the session alive.
    A 401 here means the cookies died — flag the session as unauthenticated
    and let the re-auth thread (or the next health probe) trigger recovery."""
    def run():
        log.info("tickle thread starting (interval=%ds)", TICKLE_INTERVAL)
        while True:
            time.sleep(TICKLE_INTERVAL)
            if not session.authenticated:
                continue
            try:
                r = session.get("/v1/api/tickle", timeout=10)
                if r.status_code in (401, 403):
                    session.mark_unauthenticated(
                        f"tickle returned {r.status_code}")
                elif r.status_code >= 500:
                    log.info("tickle got HTTP %s (transient?)", r.status_code)
            except requests.RequestException as e:
                log.info("tickle failed: %s", e)

    t = threading.Thread(target=run, name="ibkr-tickle", daemon=True)
    t.start()
    return t


def reauth_thread(session: IbkrSession, account: str,
                  password: str, totp_secret: Optional[str]) -> threading.Thread:
    """Every REAUTH_INTERVAL, force a fresh Selenium login so we never get
    caught flat-footed by the IBKR daily maintenance window."""
    def run():
        log.info("reauth thread starting (interval=%ds)", REAUTH_INTERVAL)
        while True:
            time.sleep(REAUTH_INTERVAL)
            log.info("scheduled re-auth firing")
            _login_once(session, account, password, totp_secret)

    t = threading.Thread(target=run, name="ibkr-reauth", daemon=True)
    t.start()
    return t
