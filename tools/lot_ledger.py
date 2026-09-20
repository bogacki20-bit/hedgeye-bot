"""lot_ledger.py — fill-level lot ledger + FIFO lot engine (desk spec 9/20).

Source of truth: actions_log (Fidelity Accounts_History rows). rebuild()
re-derives the whole ledger deterministically — a full walk per
(ticker, account) in date order, position seeded with the IMPLIED
pre-history position (current book qty minus the sum of all logged
deltas) so names held since before the activity window classify
correctly. Context-at-fill (rp/range/trend/tilt/walls, point-in-time,
Hedgeye-band-first) is backfilled by set-based UPDATEs and never
overwritten once set.

Derived, never stored:
  lots(ticker)        FIFO open lots -> oldest_open_lot date (THE CLOCK)
  recent_add()        add in trailing N sessions -> clock suppressed
  times_rented()      separate open->flat cycles
  closed_short_lots() the shelf's candidate set

integrity_check()     recompute rp from stored price+range vs stored rp;
                      mismatch = pipeline bug or dead price feed. Wired
                      into the doctor.
reconcile()           ledger position_after vs the positions export —
                      FLAG disagreement, never silently prefer one.

Options are excluded: spreads are structures, not lots (build list #7).
"""

from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger(__name__)

RECENT_ADD_SESSIONS = 5      # adds inside this window suppress the clock


def _rows(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def _exec(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        conn.commit()
        return cur.rowcount


# ── rebuild: actions_log -> fills ─────────────────────────────────────────

def rebuild() -> int:
    """Deterministic full re-derive of fills from actions_log. Existing
    context columns are preserved (matched on actions_log_id)."""
    # UNION of the two trade-history tables (9/20 finding: neither is a
    # superset — actions_log has the pre-May history, book_activity has
    # 636 rows actions_log lacks, including whole tickers like AAAU/IBIT).
    # Dedupe on (ticker, acct, date, qty, |amount|).
    acts = _rows("""
        SELECT id, normalized_symbol, account_number, run_date, action,
               abs(qty), price, amount
        FROM actions_log
        WHERE (action ILIKE 'YOU BOUGHT%%' OR action ILIKE 'YOU SOLD%%')
          AND qty IS NOT NULL AND qty <> 0
          AND raw_symbol NOT LIKE '-%%'            -- options excluded
          AND normalized_symbol IS NOT NULL
        ORDER BY normalized_symbol, account_number, run_date, id""")
    seen = {(t, a, d, round(float(q), 4), round(abs(float(amt or 0)), 2))
            for _i, t, a, d, _act, q, _px, amt in acts}
    extra = _rows("""
        SELECT -id, underlying, account_number, run_date, action_raw,
               abs(quantity), price, amount
        FROM book_activity
        WHERE action_type IN ('buy', 'sell') AND NOT is_option
          AND quantity IS NOT NULL AND quantity <> 0
        ORDER BY underlying, account_number, run_date, id""")
    acts = list(acts) + [
        r for r in extra
        if (r[1], r[2], r[3], round(float(r[5]), 4),
            round(abs(float(r[7] or 0)), 2)) not in seen]
    acts.sort(key=lambda r: (r[1], r[2], r[3], abs(r[0])))
    # implied pre-history position per (ticker, acct): current - sum(deltas)
    cur_pos = {(t, a): float(q) for t, a, q in _rows("""
        SELECT underlying, account_number, sum(quantity)
        FROM book_positions
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
          AND asset_class = 'equity'
        GROUP BY 1, 2""")}
    deltas: dict = {}
    parsed = []
    for aid, tkr, acct, d, act, q, px, amt in acts:
        q = float(q)
        up = act.upper()
        if "SHORT COVER" in up or (up.startswith("YOU BOUGHT")
                                   and "SHORT" not in up):
            signed = q
        else:                                   # SOLD / SHORT SALE
            signed = -q
        parsed.append((aid, tkr, acct, d, up, q, px, amt, signed))
        deltas[(tkr, acct)] = deltas.get((tkr, acct), 0.0) + signed

    fills = []
    pos: dict = {}
    for aid, tkr, acct, d, up, q, px, amt, signed in parsed:
        key = (tkr, acct)
        if key not in pos:
            pos[key] = cur_pos.get(key, 0.0) - deltas.get(key, 0.0)
        before = pos[key]
        pos[key] = after = round(before + signed, 6)
        if "SHORT COVER" in up:
            side, action = "short", ("cover_all" if after >= -1e-6
                                     else "cover_some")
        elif "SHORT SALE" in up:
            side, action = "short", ("add" if before < -1e-6 else "open")
        elif signed > 0:
            side, action = "long", ("add" if before > 1e-6 else "open")
        else:
            side, action = "long", ("close" if abs(after) <= 1e-6 else "trim")
        fills.append((aid, tkr, acct, d, side, action, q, px, amt, after))

    import db_pg
    from psycopg2.extras import execute_values
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        execute_values(cur, """
            INSERT INTO fills (actions_log_id, ticker, account_number,
                               run_date, side, action, qty, price, amount,
                               position_after)
            VALUES %s
            ON CONFLICT (actions_log_id) DO UPDATE
            SET side=EXCLUDED.side, action=EXCLUDED.action,
                position_after=EXCLUDED.position_after""",
            fills, page_size=1000)
        conn.commit()
    backfill_context()
    return len(fills)


def backfill_context() -> int:
    """Point-in-time context for fills that lack it. Hedgeye band first
    (<=7d old AT THE FILL DATE), MFR otherwise — the fixed precedence.
    rp is computed against the FILL price. Never overwrites."""
    n = _exec("""
        UPDATE fills f SET
            range_lo = x.lo, range_hi = x.hi, range_src = x.src,
            trend_at_fill = x.trend,
            rp_at_fill = CASE WHEN x.hi > x.lo AND f.price IS NOT NULL
                THEN GREATEST(0, LEAST(1, (f.price - x.lo) / (x.hi - x.lo)))
                END
        FROM (
            SELECT f2.id,
                   COALESCE(h.buy_trade,  m.range_low)  lo,
                   COALESCE(h.sell_trade, m.range_high) hi,
                   CASE WHEN h.ticker IS NOT NULL THEN 'hdg' ELSE 'mfr' END src,
                   COALESCE(h.trend, m.trend_signal) trend
            FROM fills f2
            LEFT JOIN LATERAL (
                SELECT ticker, buy_trade, sell_trade, trend
                FROM hedgeye_risk_ranges
                WHERE ticker = f2.ticker AND signal_date <= f2.run_date
                  AND signal_date >= f2.run_date - 7
                  AND buy_trade IS NOT NULL AND sell_trade > buy_trade
                ORDER BY signal_date DESC LIMIT 1) h ON TRUE
            LEFT JOIN LATERAL (
                SELECT range_low, range_high, trend_signal
                FROM mfr_snapshots
                WHERE ticker = f2.ticker AND snapshot_date <= f2.run_date
                  AND snapshot_date >= f2.run_date - 5
                  AND range_low IS NOT NULL AND range_high > range_low
                ORDER BY snapshot_date DESC LIMIT 1) m ON TRUE
            WHERE f2.range_lo IS NULL
        ) x
        WHERE x.id = f.id AND x.lo IS NOT NULL""")
    _exec("""
        UPDATE fills f SET spx_tilt = x.gamma_tilt
        FROM (SELECT f2.id, s.gamma_tilt FROM fills f2
              JOIN LATERAL (SELECT gamma_tilt FROM sg_tilt
                            WHERE sym='SPX' AND trade_date <= f2.run_date
                              AND trade_date >= f2.run_date - 5
                            ORDER BY trade_date DESC LIMIT 1) s ON TRUE
              WHERE f2.spx_tilt IS NULL) x
        WHERE x.id = f.id""")
    _exec("""
        UPDATE fills f SET call_wall = x.call_wall, hedge_wall = x.hedge_wall,
                           put_wall = x.put_wall
        FROM (SELECT f2.id, w.call_wall, w.hedge_wall, w.put_wall
              FROM fills f2
              JOIN LATERAL (SELECT call_wall, hedge_wall, put_wall
                            FROM spotgamma_snapshots
                            WHERE ticker = f2.ticker
                              AND snapshot_date <= f2.run_date
                              AND snapshot_date >= f2.run_date - 3
                            ORDER BY snapshot_date DESC LIMIT 1) w ON TRUE
              WHERE f2.call_wall IS NULL) x
        WHERE x.id = f.id""")
    return n


# ── the FIFO lot engine (pure derivation) ─────────────────────────────────

def lots_all() -> dict:
    """{ticker: lots-dict} for every ticker, ONE query (the note/book
    exports touch ~70 names; per-name queries are WAN death)."""
    fs = _rows("""
        SELECT ticker, run_date, side, action, qty, price, rp_at_fill
        FROM fills ORDER BY ticker, run_date, id""")
    out: dict = {}
    grp: dict = {}
    for t, *rest in fs:
        grp.setdefault(t, []).append(tuple(rest))
    for t, rows in grp.items():
        out[t] = _engine(rows)
    return out


def lots(ticker: str, account: str | None = None) -> dict:
    """{'open': [(date, qty, price, rp_at_fill)], 'closed': [...],
        'oldest_open': date|None, 'last_add': date|None,
        'times_rented': int, 'side': 'long'|'short'|None}
    FIFO per (ticker[, account]); closed entries are
    (open_date, close_date, qty, open_px, close_px, pl)."""
    cond, args = "ticker = %s", [ticker]
    if account:
        cond += " AND account_number = %s"
        args.append(account)
    fs = _rows(f"""
        SELECT run_date, side, action, qty, price, rp_at_fill
        FROM fills WHERE {cond}
        ORDER BY run_date, id""", args)
    return _engine(fs)


def _engine(fs) -> dict:
    # per-side queues: a name long in the IRA and short in the Individual
    # must not share a FIFO
    queues: dict = {"long": [], "short": []}   # [date, qty, px, rp]
    closed: list = []
    cycles = 0
    was_flat = {"long": True, "short": True}
    last_add = {"long": None, "short": None}
    for d, s, action, q, px, rp in fs:
        q = float(q)
        px = float(px) if px is not None else None
        queue = queues[s]
        if action in ("open", "add"):
            if was_flat[s]:
                cycles += 1
                was_flat[s] = False
            if action == "add":
                last_add[s] = d
            queue.append([d, q, px, float(rp) if rp is not None else None])
        else:                                   # trim/close/cover
            rem = q
            while rem > 1e-9 and queue:
                od, oq, opx, orp = queue[0]
                take = min(oq, rem)
                if opx is not None and px is not None:
                    pl = (px - opx) * take if s == "long" else (opx - px) * take
                else:
                    pl = None
                closed.append((od, d, take, opx, px, pl))
                if oq - take <= 1e-9:
                    queue.pop(0)
                else:
                    queue[0][1] = oq - take
                rem -= take
            if not queue:
                was_flat[s], last_add[s] = True, None
    # the live side: whichever queue holds open lots (shorts win ties —
    # the clock cares about them)
    side = ("short" if queues["short"] else
            "long" if queues["long"] else None)
    queue = queues[side] if side else []
    return {"open": [(d, q, px, rp) for d, q, px, rp in queue],
            "closed": closed,
            "oldest_open": queue[0][0] if queue else None,
            "last_add": last_add[side] if side else None,
            "times_rented": cycles,
            "side": side}


def clock(ticker: str, account: str | None = None,
          today: dt.date | None = None) -> dict:
    """The stale-short clock, run right: off the OLDEST OPEN LOT, and
    suppressed entirely when an add landed in the trailing N sessions.
    {'days': int|None, 'suppressed': bool, 'rule': str}."""
    today = today or dt.date.today()
    L = lots(ticker, account)
    if not L["open"]:
        return {"days": None, "suppressed": False, "rule": "flat"}
    days = (today - L["oldest_open"]).days
    if L["last_add"] and (today - L["last_add"]).days <= RECENT_ADD_SESSIONS:
        return {"days": days, "suppressed": True,
                "rule": (f"clock suppressed — scaled into "
                         f"{(today - L['last_add']).days}d ago (adds are "
                         f"conviction, not staleness)")}
    return {"days": days, "suppressed": False,
            "rule": f"oldest open lot {L['oldest_open']} ({days}d)"}


def closed_short_lots() -> list[tuple]:
    """(ticker, last_close_date, close_px_wavg, still_short) — the shelf's
    candidate set: every name with >=1 closed short lot, whether flat or
    still partially held (partial holders get ADD BACK, not RE-ENTER)."""
    out = []
    shorts = {t for (t,) in _rows(
        "SELECT DISTINCT ticker FROM fills WHERE side='short'")}
    all_lots = lots_all()
    for t in sorted(shorts):
        L = all_lots.get(t)
        if not L:
            continue
        sc = [c for c in L["closed"] if c[4] is not None]
        if not sc:
            continue
        last_d = max(c[1] for c in L["closed"])
        recent = [c for c in L["closed"] if c[1] == last_d and c[4] is not None]
        wq = sum(c[2] for c in recent) or 1
        wpx = sum(c[2] * c[4] for c in recent) / wq
        out.append((t, last_d, wpx, bool(L["open"] and L["side"] == "short")))
    return out


# ── integrity ─────────────────────────────────────────────────────────────

def integrity_check(tol: float = 0.02) -> list[str]:
    """Recompute rp from stored price+range vs stored rp. A mismatch is a
    pipeline bug or a dead price feed wearing a confident number."""
    bad = _rows("""
        SELECT ticker, run_date, price, range_lo, range_hi, rp_at_fill,
               GREATEST(0, LEAST(1, (price-range_lo)/(range_hi-range_lo)))
        FROM fills
        WHERE price IS NOT NULL AND range_lo IS NOT NULL
          AND range_hi > range_lo AND rp_at_fill IS NOT NULL
          AND abs(rp_at_fill -
                  GREATEST(0, LEAST(1, (price-range_lo)/(range_hi-range_lo))))
              > %s""", (tol,))
    return [f"{t} {d}: stored rp {float(rp):.2f} vs recomputed "
            f"{float(rc):.2f} (px {float(px):g}, band {float(lo):g}-{float(hi):g})"
            for t, d, px, lo, hi, rp, rc in bad]


def reconcile() -> list[str]:
    """Ledger position_after vs the latest positions export. Disagreement
    is FLAGGED, never silently resolved."""
    led = {(t, a): float(p) for t, a, p in _rows("""
        SELECT DISTINCT ON (ticker, account_number)
               ticker, account_number, position_after
        FROM fills ORDER BY ticker, account_number, run_date DESC, id DESC""")}
    book = {(t, a): float(q) for t, a, q in _rows("""
        SELECT underlying, account_number, sum(quantity) FROM book_positions
        WHERE snapshot_date=(SELECT max(snapshot_date) FROM book_positions)
          AND asset_class='equity' GROUP BY 1,2""")}
    flags = []
    for k in sorted(set(led) | set(book)):
        lv, bv = led.get(k, 0.0), book.get(k, 0.0)
        if abs(lv - bv) > 0.01:
            flags.append(f"{k[0]} ({k[1]}): ledger {lv:g} vs export {bv:g}")
    return flags


if __name__ == "__main__":
    import sys
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    import db_pg
    db_pg._load_dotenv_fallback()
    n = rebuild()
    print(f"rebuilt {n} fills")
    mism = integrity_check()
    print(f"integrity: {len(mism)} mismatch(es)")
    for m in mism[:10]:
        print("  ", m)
    rec = reconcile()
    print(f"reconcile vs export: {len(rec)} disagreement(s)")
    for r in rec[:15]:
        print("  ", r)
