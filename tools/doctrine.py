"""Hedgeye GIP-model doctrine accessor.

Reads config/hedgeye_doctrine.yaml and the live Quad state.

As of 2026-05-24, the canonical Quad inputs are the env vars
CURRENT_QUARTERLY_QUAD_OVERRIDE and CURRENT_MONTHLY_QUAD_OVERRIDE — the
operator sets them after reading the macro show. The bot_state fallback
(written historically by tools/detect_quads.py) is still consulted if env
is missing, and a final default of "Quad 1" is logged loudly so a misconfig
shows up in logs rather than silently routing trades.

    load_doctrine()                              -> dict (cached)
    current_quarterly_quad()                     -> "Quad N"
    current_monthly_quad()                       -> "Quad N"
    universe_for_quad(quad, side)                -> [tickers]
    asset_class_for(ticker)                      -> "equities" | ...
    position_size_cap(ticker, side, acct_value)  -> max dollars
    expected_return(ticker, quad)                -> float | None
    check_position_cap(ticker, side,
                        current_exposure, acct)  -> (allowed_$, reason)

CLI:  python -m tools.doctrine --quad "Quad 2" --side longs
"""

from __future__ import annotations

import os
import functools
import logging
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

_DOCTRINE_PATH = Path(__file__).resolve().parent.parent / "config" / "hedgeye_doctrine.yaml"

_VALID_QUADS = {"Quad 1", "Quad 2", "Quad 3", "Quad 4"}
_DEFAULT_QUAD = "Quad 1"


@functools.lru_cache(maxsize=1)
def load_doctrine() -> dict:
    """Parse and cache config/hedgeye_doctrine.yaml."""
    with open(_DOCTRINE_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _normalize_quad(value: Optional[str]) -> Optional[str]:
    """Accept 'Quad 2', 'quad2', '2', 'Q2' -> 'Quad 2'. None on garbage."""
    if value is None:
        return None
    s = str(value).strip().lower().replace("quad", "").replace("q", "").strip()
    if s in ("1", "2", "3", "4"):
        return f"Quad {s}"
    return None


def _quad_from_bot_state(key: str) -> Optional[str]:
    """Read a Quad value from the bot_state table; None if unavailable."""
    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
                row = cur.fetchone()
        if row and row[0]:
            return _normalize_quad(row[0])
    except Exception as e:  # table missing / DB down — fall back to default
        log.debug("bot_state lookup failed for %s (%s)", key, e)
    return None


def current_quarterly_quad() -> str:
    """Active QUARTERLY Quad (strategic).

    Canonical: CURRENT_QUARTERLY_QUAD_OVERRIDE env var (set by operator).
    Fallback: bot_state row (historical autodetect path; disabled by default).
    Final: _DEFAULT_QUAD with a loud log line so misconfig is visible.
    """
    override = _normalize_quad(os.environ.get("CURRENT_QUARTERLY_QUAD_OVERRIDE"))
    if override:
        return override
    state = _quad_from_bot_state("current_quarterly_quad")
    if state:
        log.warning("CURRENT_QUARTERLY_QUAD_OVERRIDE not set; using bot_state "
                    "fallback %s. Set the env var to make Quad input explicit.",
                    state)
        return state
    log.warning("No quarterly Quad input found (env or bot_state); defaulting "
                "to %s. Set CURRENT_QUARTERLY_QUAD_OVERRIDE.", _DEFAULT_QUAD)
    return _DEFAULT_QUAD


def current_monthly_quad() -> str:
    """Active MONTHLY Quad (tactical).

    Canonical: CURRENT_MONTHLY_QUAD_OVERRIDE env var (set by operator).
    Fallback: bot_state row, then the active quarterly Quad.
    """
    override = _normalize_quad(os.environ.get("CURRENT_MONTHLY_QUAD_OVERRIDE"))
    if override:
        return override
    state = _quad_from_bot_state("current_monthly_quad")
    if state:
        log.warning("CURRENT_MONTHLY_QUAD_OVERRIDE not set; using bot_state "
                    "fallback %s. Set the env var to make Quad input explicit.",
                    state)
        return state
    return current_quarterly_quad()


def universe_for_quad(quad: str, side: str = "longs") -> list[str]:
    """Flattened favored ticker list for `quad` on `side` ('longs'|'shorts')."""
    q = _normalize_quad(quad) or _DEFAULT_QUAD
    side = "shorts" if str(side).lower().startswith("short") else "longs"
    block = (load_doctrine().get("quad_universe", {}).get(q, {}) or {}).get(side, {})
    out: list[str] = []
    for _asset_class, tickers in (block or {}).items():
        for t in tickers or []:
            tu = str(t).upper()
            if tu not in out:
                out.append(tu)
    return out


def asset_class_for(ticker: str) -> Optional[str]:
    """Cap category for `ticker` (equities|fixed_income|commodities|
    foreign_currency|options|crypto), or None if unmapped."""
    if not ticker:
        return None
    return load_doctrine().get("ticker_to_asset_class", {}).get(ticker.upper())


def _side_norm(side: Optional[str]) -> str:
    return "short" if str(side or "long").lower().startswith("short") else "long"


def position_size_cap(ticker: str, side: str, account_value: float) -> Optional[float]:
    """Max dollars allowed in a single position for `ticker` per the
    Hedgeye Position Sizing matrix. None if the asset class is unknown
    (caller must treat None as 'no doctrine cap', not zero)."""
    try:
        acct = float(account_value or 0.0)
    except (TypeError, ValueError):
        return None
    ac = asset_class_for(ticker)
    if not ac:
        return None
    caps = load_doctrine().get("position_sizing_caps", {})
    spec = caps.get(ac, {})
    if ac == "equities":
        pct = spec.get("max_short_pct" if _side_norm(side) == "short"
                        else "max_long_pct")
    else:
        pct = spec.get("max_pct")
    if pct is None:
        return None
    return round(acct * (float(pct) / 100.0), 2)


def expected_return(ticker: str, quad: str) -> Optional[float]:
    """Historical avg quarterly % return for `ticker` when `quad` is
    active, or None if not in the (partial) slide_3 matrix."""
    q = _normalize_quad(quad)
    if not q or not ticker:
        return None
    row = load_doctrine().get("expected_returns", {}).get(ticker.upper())
    if not row:
        return None
    idx = int(q.split()[-1]) - 1
    try:
        return float(row[idx])
    except (IndexError, TypeError, ValueError):
        return None


def check_position_cap(
    ticker: str,
    side: str,
    current_exposure_dollars: float,
    account_value: float,
) -> tuple[Optional[float], Optional[str]]:
    """Given existing exposure, return (allowed_additional_dollars,
    cap_reason). allowed is None when no doctrine cap applies (caller
    leaves sizing unchanged). allowed is the remaining headroom to the
    asset-class cap otherwise; 0.0 (with a reason) when already at/over."""
    cap = position_size_cap(ticker, side, account_value)
    if cap is None:
        return None, None
    try:
        cur = max(0.0, float(current_exposure_dollars or 0.0))
    except (TypeError, ValueError):
        cur = 0.0
    ac = asset_class_for(ticker)
    pct = (cap / float(account_value) * 100.0) if account_value else 0.0
    headroom = round(cap - cur, 2)
    if headroom <= 0:
        return 0.0, (
            f"{ticker} {ac} position ${cur:,.0f} already at/over Hedgeye "
            f"{pct:.0f}% cap (${cap:,.0f})"
        )
    return headroom, (
        f"{ticker} {ac} cap ${cap:,.0f} ({pct:.0f}% of acct); "
        f"${cur:,.0f} used, ${headroom:,.0f} headroom"
    )


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.doctrine")
    p.add_argument("--quad", default=None, help='e.g. "Quad 2"')
    p.add_argument("--side", default="longs", choices=["longs", "shorts"])
    p.add_argument("--ticker", default=None, help="show class/cap/EV for ticker")
    p.add_argument("--account-value", type=float, default=90000.0)
    a = p.parse_args(argv)

    quad = _normalize_quad(a.quad) or current_quarterly_quad()
    print(f"Active quarterly Quad: {current_quarterly_quad()}")
    print(f"Active monthly  Quad: {current_monthly_quad()}")
    print(f"Universe for {quad} / {a.side}:")
    uni = universe_for_quad(quad, a.side)
    print("  " + ", ".join(uni) if uni else "  (empty)")
    if a.ticker:
        t = a.ticker.upper()
        print(f"\n{t}: class={asset_class_for(t)} "
              f"cap_long=${position_size_cap(t, 'long', a.account_value)} "
              f"cap_short=${position_size_cap(t, 'short', a.account_value)} "
              f"EV[{quad}]={expected_return(t, quad)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
