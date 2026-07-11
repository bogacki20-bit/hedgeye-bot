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
import time

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


# ───────────── alert-decoration cache (book stamp on every alert) ─────────────
# 5-min TTL, same pattern as active_slice: one DB read per cache window, not
# one per alerted ticker. Failure is LOUD — the stamp says the check failed
# rather than silently rendering the alert as if the name weren't held.

_STAMP_TTL = 300.0
_stamp_cache: dict = {"exp": 0.0, "sides": None, "links": None, "failed": False}


def _stamp_data() -> dict:
    now = time.time()
    if _stamp_cache["exp"] > now:
        return _stamp_cache
    try:
        _stamp_cache["sides"] = book_sides()
        try:
            from tools.wrapper_links import get_links
            _stamp_cache["links"] = get_links()
        except Exception as e:
            log.warning("book stamp: wrapper links unavailable: %s", e)
            _stamp_cache["links"] = {}
        _stamp_cache["failed"] = False
    except Exception as e:
        log.warning("book stamp: sides refresh failed: %s", e)
        _stamp_cache["failed"] = True
    _stamp_cache["exp"] = now + _STAMP_TTL
    return _stamp_cache


def side_stamp(ticker: str) -> str:
    """One-line book stamp for alert bodies. '' when the name isn't held and
    no held wrapper expresses it. Examples:
        📗 YOU HOLD XLV: LONG
        📗 YOU HOLD SBIT (long shares) = SHORT exposure
        📗 EXPOSURE via METD (short META ↯inv)
    On lookup failure returns a loud failure marker, never silence."""
    t = (ticker or "").strip().upper()
    if not t:
        return ""
    c = _stamp_data()
    if c["failed"] or c["sides"] is None:
        return "📗 book-check FAILED — position match unavailable"
    s = c["sides"].get(t)
    if s and s["side"] in ("long", "short"):
        if s["via_linkage"]:
            return (f"📗 YOU HOLD {t} ({s['raw_side']} shares) = "
                    f"{s['side'].upper()} exposure")
        return f"📗 YOU HOLD {t}: {s['side'].upper()}"
    hits = []
    for w, lk in (c["links"] or {}).items():
        if (lk.get("underlying") or "").strip().upper() == t:
            ws = c["sides"].get(w)
            if ws and ws["side"] in ("long", "short"):
                inv = " ↯inv" if lk.get("inverse") else ""
                hits.append(f"{w} ({ws['side']} {t}{inv})")
    if hits:
        return "📗 EXPOSURE via " + ", ".join(sorted(hits))
    return ""
