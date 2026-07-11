"""
book_direction.py — per-underlying POSITION SIDE from the latest book snapshot.

The SHY/TUA/HEFT/AGGH fix (build queue item 5, part 1): "screen my book shorts"
must mean POSITION SIDE, not bearish-trend holdings. This module derives that
side. Python owns ALL arithmetic; SQL only selects rows; no LLM anywhere.

Derivation per underlying (all accounts, all legs, latest snapshot_date):
  * equity leg:  sign(quantity)                      (Fidelity shorts are negative)
  * option leg:  sign(quantity) * (+1 call / -1 put) (long put = short exposure)
  * leg weight:  abs(market_value), falling back to abs(quantity) when Fidelity
                 sends no value ('--'). Weighting is what makes spreads net
                 correctly: a bear put spread's long leg carries more premium
                 than its short leg, so the net is short — equal-quantity legs
                 would cancel unweighted.
  * net = Σ sign*weight →  >0 long · <0 short · ==0 flat (reported, never guessed)

Wrapper linkage (stated-identity only, wrapper_links table): an INVERSE wrapper
held long is a SHORT expression on its underlying — SBIT long = short BTC. The
returned `side` is the EXPOSURE side (linkage-adjusted); `raw_side` is the
holding's own side in the instrument. The screener's thesis-mismatch check uses
raw_side vs the (already linkage-adjusted) trend_dir — the verdict is identical
in either frame, so flips never double-count.

Loud-failure notes: legs that can't be judged (no quantity, or an option row
with no C/P) are counted in `unknown_legs` and surfaced by the caller — never
silently dropped into a side.
"""
import logging

log = logging.getLogger("book_direction")

_SQL = """
SELECT underlying, asset_class, is_option, opt_type, quantity, market_value
FROM book_positions
WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
  AND asset_class <> 'cash'
  AND COALESCE(quantity, 0) <> 0
"""


def compute_sides(rows, links=None) -> dict:
    """Pure function — {underlying: {side, raw_side, net, legs, unknown_legs,
    via_linkage}} from book_positions-shaped dicts. `links` is the
    wrapper_links dict ({wrapper: {underlying, inverse, ...}}) or None."""
    links = links or {}
    acc: dict = {}
    for r in rows:
        t = (r.get("underlying") or "").strip().upper()
        if not t:
            continue
        a = acc.setdefault(t, {"net": 0.0, "legs": 0, "unknown_legs": 0})
        qty = r.get("quantity")
        if qty in (None, 0):
            a["unknown_legs"] += 1
            continue
        qty = float(qty)
        sign = 1.0 if qty > 0 else -1.0
        if r.get("is_option"):
            ot = (r.get("opt_type") or "").strip().upper()
            if ot == "P":
                sign = -sign
            elif ot != "C":
                a["unknown_legs"] += 1     # option with no C/P — can't judge
                continue
        mv = r.get("market_value")
        weight = abs(float(mv)) if mv not in (None, 0) else abs(qty)
        a["net"] += sign * weight
        a["legs"] += 1

    out = {}
    for t, a in acc.items():
        if a["legs"] == 0:
            raw = None                     # every leg unjudgeable — loud, no guess
        elif a["net"] > 0:
            raw = "long"
        elif a["net"] < 0:
            raw = "short"
        else:
            raw = "flat"
        lk = links.get(t)
        inverse = bool(lk and lk.get("inverse"))
        side = raw
        if inverse and raw in ("long", "short"):
            side = "short" if raw == "long" else "long"
        out[t] = {"side": side, "raw_side": raw, "net": round(a["net"], 2),
                  "legs": a["legs"], "unknown_legs": a["unknown_legs"],
                  "via_linkage": inverse}
    return out


def book_sides() -> dict:
    """DB entry point: latest snapshot rows + wrapper links -> compute_sides().
    Raises on DB failure — a held screen without sides must fail LOUD, not
    silently revert to trend semantics."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(_SQL)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    try:
        from tools.wrapper_links import get_links
        links = get_links()
    except Exception as e:
        # Linkage table unreadable -> sides still computable, exposure
        # adjustment skipped. Loud in the log; raw sides remain correct.
        log.warning("wrapper links unavailable for book sides: %s", e)
        links = {}
    return compute_sides(rows, links)
