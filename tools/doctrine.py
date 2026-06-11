"""Hedgeye GIP-model doctrine accessor.

Reads config/hedgeye_doctrine.yaml and the live Quad state.

Quad input resolution (2026-06-10 fix — silent Quad-1 default removed):

    1. bot_state.monthly_quad / .quarterly_quad   ← canonical operator seed
    2. CURRENT_MONTHLY_QUAD_OVERRIDE / _QUARTERLY env vars  ← shell escape
    3. raise QuadUnsetError                         ← halts the cycle

No default Quad anywhere — the pre-fix behaviour silently routed every
universe lookup against Quad 1, so a bot that started before the operator
had set the Quad would issue Quad-1 doctrine alerts indistinguishable
from real ones. Callers (price_monitor, proactive_scanner) catch
QuadUnsetError, fire ONE Telegram "QUAD UNSET — halted" message and skip
the cycle.

    load_doctrine()                              -> dict (cached)
    current_quarterly_quad()                     -> "Quad N"  (raises QuadUnsetError)
    current_monthly_quad()                       -> "Quad N"  (raises QuadUnsetError)
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


class QuadUnsetError(RuntimeError):
    """Raised when neither bot_state nor env supplies a Quad value.

    Callers (price_monitor, proactive_scanner) catch this, emit ONE
    Telegram halt notice and skip the cycle rather than silently routing
    against a default Quad. See module docstring."""


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


def _quad_from_bot_state(*keys: str) -> Optional[str]:
    """Read a Quad value from bot_state, trying each key in order; None if
    unavailable. Multi-key form supports the post-2026-06-10 short keys
    (`monthly_quad`, `quarterly_quad`) with fallback to the legacy
    `current_monthly_quad` / `current_quarterly_quad` rows tools/detect_quads.py
    used to write."""
    try:
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                for k in keys:
                    cur.execute("SELECT value FROM bot_state WHERE key = %s", (k,))
                    row = cur.fetchone()
                    if row and row[0]:
                        q = _normalize_quad(row[0])
                        if q:
                            return q
    except Exception as e:  # table missing / DB down
        log.debug("bot_state lookup failed for %s (%s)", keys, e)
    return None


def current_quarterly_quad() -> str:
    """Active QUARTERLY Quad (strategic).

    Resolution: bot_state.quarterly_quad → CURRENT_QUARTERLY_QUAD_OVERRIDE
    env → raise QuadUnsetError. No default — silent Quad-1 routing was
    causing the bot to issue Quad-1 doctrine alerts whenever the operator
    hadn't seeded the Quad yet (2026-06-10 architectural fix).
    """
    state = _quad_from_bot_state("quarterly_quad", "current_quarterly_quad")
    if state:
        return state
    override = _normalize_quad(os.environ.get("CURRENT_QUARTERLY_QUAD_OVERRIDE"))
    if override:
        return override
    raise QuadUnsetError(
        "quarterly Quad unset — seed bot_state.quarterly_quad (or set "
        "CURRENT_QUARTERLY_QUAD_OVERRIDE) before running doctrine lookups"
    )


def current_monthly_quad() -> str:
    """Active MONTHLY Quad (tactical).

    Resolution: bot_state.monthly_quad → CURRENT_MONTHLY_QUAD_OVERRIDE
    env → raise QuadUnsetError. No silent fall-through to the quarterly
    Quad — the operator's monthly read can disagree with the quarterly
    and routing tactical alerts off the wrong frame is exactly the
    failure mode this fix exists to prevent.
    """
    state = _quad_from_bot_state("monthly_quad", "current_monthly_quad")
    if state:
        return state
    override = _normalize_quad(os.environ.get("CURRENT_MONTHLY_QUAD_OVERRIDE"))
    if override:
        return override
    raise QuadUnsetError(
        "monthly Quad unset — seed bot_state.monthly_quad (or set "
        "CURRENT_MONTHLY_QUAD_OVERRIDE) before running doctrine lookups"
    )


def universe_for_quad(quad: str, side: str = "longs") -> list[str]:
    """Flattened favored ticker list for `quad` on `side` ('longs'|'shorts').

    Raises QuadUnsetError when `quad` can't be normalized (was returning
    Quad 1's universe silently on garbage input)."""
    q = _normalize_quad(quad)
    if not q:
        raise QuadUnsetError(f"universe_for_quad: unrecognized quad {quad!r}")
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
