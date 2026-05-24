"""Selenium-driven IBKR login flow.

This module owns the headless Chrome session that automates the IBKR Client
Portal login. It is intentionally isolated from app.py and session.py so the
selector list (the part that breaks every time IBKR redesigns its login
page) is small, obvious, and updatable in one place.

Why this exists at all — we used to lean on voyz/ibeam for this. As of
2026-04-21, ibeam 0.5.11 AND 0.5.12 both ship with post-login selectors
(`#twofactbase`, `#xyz-field-bronze-response`, `.ibkey-promo-skip`,
`.xyz-errormessage`) that no longer match anything on the current IBKR
login UI. Upstream fixes look stalled, so we control the selectors ourselves.

Flow:
    1. open headless chrome (chromium + matched chromedriver from apt)
    2. navigate to IBKR_LOGIN_URL
    3. fill USERNAME + PASSWORD inputs
    4. click SUBMIT
    5. wait for the 2FA page (detect via TWO_FA_INPUT presence OR url contains /MFA/)
    6. compute current TOTP from IBEAM_TOTP_SECRET using pyotp
    7. type the 6-digit code into TWO_FA_INPUT, click TWO_FA_SUBMIT
    8. dismiss the IBKey promo upsell if it appears (IBKEY_PROMO_SKIP)
    9. wait for SUCCESS_INDICATOR (url change or dashboard element)
   10. dump the session cookies for the REST proxy to reuse
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import pyotp
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

log = logging.getLogger(__name__)


# ─────────────────────────── SELECTORS ────────────────────────────────────
#
# !! TODO !! These are STUBS pending a DevTools inspection on the live IBKR
# login page (see README.md "Updating selectors"). Until the four marked
# TODOs are replaced, login WILL FAIL at the labeled step. The structure of
# the rest of the module is independent of these values — once the operator
# fills these in, no other changes here should be needed.
#
# Each entry is a `By, value` tuple compatible with `driver.find_element(*sel)`.
# If a selector is genuinely unknown, leave the `By` as `By.CSS_SELECTOR` and
# put the placeholder string after it — the validate_selectors() call at
# import time will refuse to start the service rather than silently log in
# with placeholders.
SELECTORS = {
    # Login screen — these two are very likely unchanged (ibeam's
    # `xyz-field-username` / `xyz-field-password` IDs were stable for years).
    # If they break, swap to (By.ID, "xyz-field-username") etc.
    "USERNAME":      (By.NAME, "username"),
    "PASSWORD":      (By.NAME, "password"),
    # Old ibeam value: (By.CSS_SELECTOR, ".btn.btn-lg.xyz-button-login")
    "SUBMIT":        (By.CSS_SELECTOR, "TODO_LOGIN_SUBMIT_SELECTOR"),

    # 2FA screen.
    # Old ibeam value: (By.ID, "xyz-field-bronze-response")
    "TWO_FA_INPUT":  (By.CSS_SELECTOR, "TODO_TWO_FA_INPUT_SELECTOR"),
    # Old ibeam value: a button inside #twofactbase form
    "TWO_FA_SUBMIT": (By.CSS_SELECTOR, "TODO_TWO_FA_SUBMIT_SELECTOR"),

    # Post-2FA interstitial — IBKR sometimes shows a "use IBKey instead" upsell.
    # Old ibeam value: (By.CLASS_NAME, "ibkey-promo-skip")
    # If you cannot find this on the current UI, it likely just doesn't show
    # for this account anymore — leave the TODO, the code skips it on
    # NoSuchElementException.
    "IBKEY_PROMO_SKIP": (By.CSS_SELECTOR, "TODO_IBKEY_PROMO_SKIP_SELECTOR"),

    # Confirms login succeeded. This can be either an element on the
    # post-login dashboard OR a substring of the URL we expect to land on.
    # `_wait_for_success` accepts either kind — see code below.
    # Old ibeam value: looked for url containing "/portal.proxy" or similar.
    "SUCCESS_INDICATOR": (By.CSS_SELECTOR, "TODO_SUCCESS_INDICATOR_SELECTOR"),

    # Error message displayed inline on the login form when credentials or
    # 2FA code are rejected. Used for cleaner failure logs only — login is
    # still detected as failed by the absence of SUCCESS_INDICATOR.
    # Old ibeam value: (By.CLASS_NAME, "xyz-errormessage")
    "ERROR":         (By.CSS_SELECTOR, "TODO_ERROR_SELECTOR"),
}

# URLs and substrings used for navigation + success detection.
#
# IBKR_LOGIN_URL — the entry point. The operator's working assumption is
# that we can drive the IBKR public SSO directly rather than running the
# local Client Portal Java gateway. If snapshots fail post-auth because
# the captured cookies don't have privileges against the REST endpoints
# we proxy, the fallback is to bundle clientportal.gw into this image and
# point this URL at `https://localhost:5000/sso/Login?forwardTo=22&RL=1`.
IBKR_LOGIN_URL = os.environ.get(
    "IBKR_LOGIN_URL",
    "https://www.interactivebrokers.com/sso/Login?forwardTo=22&RL=1&ip2loc=on",
)
# Base URL the REST proxy targets. Override via env when running the
# clientportal.gw fallback (then this would be https://localhost:5000).
IBKR_API_BASE = os.environ.get(
    "IBKR_API_BASE",
    "https://www.interactivebrokers.com",
)
# Substring search on driver.current_url that confirms we left the login
# flow. Used as a fallback when SUCCESS_INDICATOR can't be found as an
# element — IBKR's post-login URL has historically contained "/portal/".
SUCCESS_URL_SUBSTRINGS = ("/portal/", "/sso/", "AccountManagement")

# Timeouts (seconds).
WAIT_FOR_LOGIN_FORM = 30
WAIT_FOR_2FA_PAGE   = 30
WAIT_FOR_AUTH_DONE  = 60


@dataclass
class AuthResult:
    """What a completed (or failed) login produced."""
    ok: bool
    cookies: list = field(default_factory=list)   # list of selenium cookie dicts
    error: Optional[str] = None
    final_url: Optional[str] = None


def _placeholder_selectors() -> list[str]:
    """Return the keys in SELECTORS whose value still starts with 'TODO_'.
    Used by app.py at startup to log loudly when the operator is running
    with a stub config."""
    bad = []
    for key, (_by, value) in SELECTORS.items():
        if isinstance(value, str) and value.startswith("TODO_"):
            bad.append(key)
    return bad


def _make_driver() -> webdriver.Chrome:
    """Build a headless Chrome that mimics a real desktop browser enough
    that IBKR's anti-bot doesn't reject us. The chromium + chromedriver
    binaries are installed via apt in the Dockerfile and discovered through
    the standard $PATH; if you change the install method, update
    CHROME_BINARY / CHROMEDRIVER_PATH env vars accordingly."""
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,1024")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
    chrome_binary = os.environ.get("CHROME_BINARY")
    if chrome_binary:
        opts.binary_location = chrome_binary
    chromedriver = os.environ.get("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
    service = Service(executable_path=chromedriver)
    return webdriver.Chrome(service=service, options=opts)


def _fill(driver: webdriver.Chrome, key: str, value: str) -> None:
    el = WebDriverWait(driver, WAIT_FOR_LOGIN_FORM).until(
        EC.presence_of_element_located(SELECTORS[key])
    )
    el.clear()
    el.send_keys(value)


def _click(driver: webdriver.Chrome, key: str) -> None:
    el = WebDriverWait(driver, WAIT_FOR_LOGIN_FORM).until(
        EC.element_to_be_clickable(SELECTORS[key])
    )
    el.click()


def _detect_2fa(driver: webdriver.Chrome) -> bool:
    """Return True once we're on the 2FA page. Two heuristics:
        (a) the TWO_FA_INPUT element is present, OR
        (b) the current URL contains '/MFA/' or '2fa' (case-insensitive).
    Either is sufficient — IBKR has bounced between both patterns."""
    try:
        WebDriverWait(driver, 1).until(
            EC.presence_of_element_located(SELECTORS["TWO_FA_INPUT"])
        )
        return True
    except TimeoutException:
        pass
    url = (driver.current_url or "").lower()
    return "/mfa/" in url or "2fa" in url or "twofact" in url


def _wait_for_2fa(driver: webdriver.Chrome) -> None:
    """Block until _detect_2fa() returns True or WAIT_FOR_2FA_PAGE expires."""
    end = time.time() + WAIT_FOR_2FA_PAGE
    while time.time() < end:
        if _detect_2fa(driver):
            return
        time.sleep(0.5)
    raise TimeoutException(
        f"2FA page not detected within {WAIT_FOR_2FA_PAGE}s; "
        f"current url={driver.current_url!r}"
    )


def _try_skip_ibkey_promo(driver: webdriver.Chrome) -> None:
    """IBKR sometimes interstitials a 'switch to IBKey for faster login'
    page after 2FA. Best-effort skip — if the element isn't there, the
    promo simply wasn't shown for this account, which is fine."""
    try:
        el = driver.find_element(*SELECTORS["IBKEY_PROMO_SKIP"])
        el.click()
        log.info("dismissed IBKey promo interstitial")
    except (NoSuchElementException, WebDriverException):
        pass


def _wait_for_success(driver: webdriver.Chrome) -> bool:
    """Return True once we're past auth. SUCCESS_INDICATOR is checked first
    (as an element), then SUCCESS_URL_SUBSTRINGS as fallback so the code
    still works if the element selector is a TODO but the URL is reliable."""
    end = time.time() + WAIT_FOR_AUTH_DONE
    while time.time() < end:
        try:
            driver.find_element(*SELECTORS["SUCCESS_INDICATOR"])
            return True
        except (NoSuchElementException, WebDriverException):
            pass
        url = (driver.current_url or "")
        if any(s in url for s in SUCCESS_URL_SUBSTRINGS) \
                and "/sso/Login" not in url:
            return True
        time.sleep(0.5)
    return False


def _capture_error(driver: webdriver.Chrome) -> Optional[str]:
    """Best-effort scrape of the inline error message IBKR shows on auth
    failure. Used only for human-readable logs; the absence of success
    is already the source of truth."""
    try:
        el = driver.find_element(*SELECTORS["ERROR"])
        return (el.text or "").strip() or None
    except (NoSuchElementException, WebDriverException):
        return None


def login(account: str, password: str, totp_secret: Optional[str]) -> AuthResult:
    """Run the full headless login. Returns an AuthResult — never raises.

    `account` / `password` are plain strings (operator decision: no Fernet).
    `totp_secret` is the base32 TOTP shared secret; if None, the 2FA step
    is skipped (paper accounts without 2FA). For accounts using mobile-app
    push 2FA, this whole flow does not apply — that's an operator config
    error and should be caught before reaching here."""
    bad = _placeholder_selectors()
    if bad:
        msg = (f"refusing to attempt login — these selectors are still "
               f"TODO placeholders: {bad}. Update SELECTORS in auth.py.")
        log.error(msg)
        return AuthResult(ok=False, error=msg)

    driver = None
    try:
        driver = _make_driver()
        log.info("navigating to %s", IBKR_LOGIN_URL)
        driver.get(IBKR_LOGIN_URL)

        _fill(driver, "USERNAME", account)
        _fill(driver, "PASSWORD", password)
        _click(driver, "SUBMIT")
        log.info("submitted credentials; waiting for 2FA page")

        if totp_secret:
            _wait_for_2fa(driver)
            code = pyotp.TOTP(totp_secret).now()
            log.info("computed TOTP code (last 2 digits=%s)", code[-2:])
            _fill(driver, "TWO_FA_INPUT", code)
            _click(driver, "TWO_FA_SUBMIT")
        else:
            log.info("no IBEAM_TOTP_SECRET set; skipping 2FA step "
                     "(paper account or operator misconfiguration)")

        # IBKR sometimes shows a "switch to IBKey" upsell here.
        _try_skip_ibkey_promo(driver)

        if not _wait_for_success(driver):
            err = _capture_error(driver)
            return AuthResult(
                ok=False,
                error=(f"auth did not complete within {WAIT_FOR_AUTH_DONE}s; "
                       f"inline error={err!r}; final url={driver.current_url!r}"),
                final_url=driver.current_url,
            )

        cookies = driver.get_cookies()
        final_url = driver.current_url
        log.info("auth ok; captured %d cookies; final url=%s",
                 len(cookies), final_url)
        return AuthResult(ok=True, cookies=cookies, final_url=final_url)

    except Exception as e:
        return AuthResult(ok=False, error=f"login crashed: {e!r}")
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
