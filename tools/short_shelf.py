"""short_shelf.py — the WATCH SHELF (operator + desk audit, 9/20).

The short doctrine: a covered short is rented again on the next
qualifying bounce. This module is the shelf that doctrine assumed:

  sweep()     find SHORT COVER rows in actions_log not yet shelved,
              write shelf rows (qty-weighted cover price, approx realized
              P/L vs the lifetime avg short-sale price). Also detects
              re-shorts: a SHORT SALE after a live row's cover date marks
              it re-entered with the price. Idempotent.
  evaluate()  each live row against the RE-ENTRY trigger — ALL of:
                - on a short roster: Position Monitor short bucket, OR
                  ETF Pro bias=short (fresh week), OR Signal Strength
                  member WITH fresh BEARISH trend (SS stores no side;
                  membership alone proves nothing). Portfolio Solutions
                  stores ranks with NO side, so it can't vouch a short —
                  its rank is shown on the row instead.
                - trend BEARISH from a fresh source (v_screener, gated)
                - rp >= 0.65
              and EXPIRES rows whose name left every short roster or
              flipped BULLISH — logged with a reason, never dropped.
  stats()     covers -> re-shorted count, and first vs second rental P/L
              (the question the shelf exists to answer).

Telegram: SHELF (card) — also RE-ENTRY lines ride the AM desk note.
"""

from __future__ import annotations

import datetime as dt
import logging

log = logging.getLogger(__name__)

SENTINEL = "SHELF"
SWEEP_SINCE = dt.date(2026, 9, 1)     # backfill horizon: the 9/14 covers
REENTRY_RP = 0.65
ETFPRO_FRESH_DAYS = 14                # weekly product
SHORT_BUCKETS = ("active_short", "short_bench", "top_idea_short")


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


# ── sweep: covers -> shelf rows, re-shorts -> re-entered ──────────────────

def sweep() -> tuple[int, int]:
    """(new shelf rows, rows marked re-entered)."""
    new = _exec("""
        INSERT INTO short_shelf (ticker, account_number, cover_date,
                                 cover_price, qty_covered, partial, realized_pl)
        SELECT c.sym, c.acct, c.d,
               c.spent / NULLIF(c.q, 0),
               c.q,
               COALESCE(p.qty, 0) < 0,                     -- still short after = partial
               (o.avg_open - c.spent / NULLIF(c.q, 0)) * c.q
        FROM (SELECT normalized_symbol sym, account_number acct, run_date d,
                     sum(abs(qty)) q, sum(abs(amount)) spent
              FROM actions_log
              WHERE action ILIKE '%%SHORT COVER%%' AND run_date >= %s
              GROUP BY 1, 2, 3) c
        LEFT JOIN (SELECT normalized_symbol sym, account_number acct,
                          sum(abs(amount)) / NULLIF(sum(abs(qty)), 0) avg_open
                   FROM actions_log
                   WHERE action ILIKE '%%SHORT SALE%%'
                   GROUP BY 1, 2) o ON o.sym = c.sym AND o.acct = c.acct
        LEFT JOIN (SELECT underlying, account_number, sum(quantity) qty
                   FROM book_positions
                   WHERE snapshot_date = (SELECT max(snapshot_date)
                                          FROM book_positions)
                   GROUP BY 1, 2) p ON p.underlying = c.sym
                                   AND p.account_number = c.acct
        ON CONFLICT (ticker, account_number, cover_date) DO NOTHING""",
        (SWEEP_SINCE,))

    # re-shorts: a SHORT SALE after a live row's cover date = re-entered
    re_entered = 0
    for rid, tkr, acct, cov_d in _rows(
            "SELECT id, ticker, account_number, cover_date FROM short_shelf "
            "WHERE status = 'live'"):
        r = _rows("""
            SELECT min(run_date),
                   sum(abs(amount)) / NULLIF(sum(abs(qty)), 0)
            FROM actions_log
            WHERE normalized_symbol = %s AND account_number = %s
              AND action ILIKE '%%SHORT SALE%%' AND run_date > %s""",
                  (tkr, acct, cov_d))
        if r and r[0][0]:
            _exec("UPDATE short_shelf SET status='re-entered', "
                  "reentered_at=%s, reentry_price=%s WHERE id=%s",
                  (r[0][0], r[0][1], rid))
            re_entered += 1
    return new, re_entered


# ── roster membership + trigger evaluation ────────────────────────────────

def _roster(tickers: list[str]) -> dict:
    """{ticker: {posmon, etfpro, ss, ps_rank}} short-roster membership."""
    if not tickers:
        return {}
    out = {t: {"posmon": False, "etfpro": False, "ss": False, "ps_rank": None}
           for t in tickers}
    for (t,) in _rows("SELECT ticker FROM ticker_tags WHERE ticker=ANY(%s) "
                      "AND hedgeye_bucket_0629 = ANY(%s)",
                      (tickers, list(SHORT_BUCKETS))):
        out[t]["posmon"] = True
    for (t,) in _rows("SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges "
                      "WHERE ticker=ANY(%s) AND bias='short' "
                      "AND week_of >= CURRENT_DATE - %s",
                      (tickers, ETFPRO_FRESH_DAYS)):
        out[t]["etfpro"] = True
    for (t,) in _rows("SELECT ticker FROM ss_roster_current WHERE ticker=ANY(%s)",
                      (tickers,)):
        out[t]["ss"] = True
    for t, rk in _rows("SELECT ticker, rank FROM hedgeye_portfolio_solutions "
                       "WHERE ticker=ANY(%s) AND snapshot_date = "
                       "(SELECT max(snapshot_date) FROM hedgeye_portfolio_solutions)",
                       (tickers,)):
        out[t]["ps_rank"] = rk
    return out


def evaluate() -> dict:
    """{'fires': [...], 'watching': [...], 'expired': [...]} and applies
    expiries. Each entry is a display-ready string."""
    live = _rows("""
        SELECT id, ticker, cover_date, cover_price, realized_pl, partial
        FROM short_shelf WHERE status='live' ORDER BY cover_date""")
    if not live:
        return {"fires": [], "watching": [], "expired": []}
    tickers = sorted({r[1] for r in live})
    ros = _roster(tickers)
    scr = {t: (rp, tr, ts) for t, rp, tr, ts in _rows(
        "SELECT ticker, range_pos, trend_dir, trend_source FROM v_screener "
        "WHERE ticker=ANY(%s)", (tickers,))}

    fires, watching, expired = [], [], []
    for rid, tkr, cov_d, cov_px, rpl, partial in live:
        m = ros.get(tkr, {})
        rp, trend, _src = scr.get(tkr, (None, None, None))
        rp = float(rp) if rp is not None else None
        on_roster = (m.get("posmon") or m.get("etfpro")
                     or (m.get("ss") and trend == "BEARISH"))
        tags = "/".join(k for k in ("posmon", "etfpro", "ss") if m.get(k)) or "none"
        if m.get("ps_rank") is not None:
            tags += f" ps#{m['ps_rank']}"

        if trend == "BULLISH":
            _exec("UPDATE short_shelf SET status='expired', expired_at=%s, "
                  "expiry_reason='trend flipped BULLISH' WHERE id=%s",
                  (dt.date.today(), rid))
            expired.append(f"{tkr} — trend flipped BULLISH (covered {cov_d})")
            continue
        if not on_roster:
            _exec("UPDATE short_shelf SET status='expired', expired_at=%s, "
                  "expiry_reason='left every short roster' WHERE id=%s",
                  (dt.date.today(), rid))
            expired.append(f"{tkr} — left every short roster (covered {cov_d})")
            continue

        px_s = f"covered {cov_d} @ {float(cov_px):.2f}" if cov_px else f"covered {cov_d}"
        pl_s = f" (banked {float(rpl):+.0f})" if rpl is not None else ""
        line = (f"{tkr:<5} rp={rp if rp is None else format(rp, '.2f')} "
                f"{trend or '?'} [{tags}]{' partial' if partial else ''} · "
                f"{px_s}{pl_s}")
        if rp is not None and rp >= REENTRY_RP and trend == "BEARISH":
            fires.append((tkr, cov_d, line))
        else:
            watching.append(line)
    # one fire per ticker: the latest cover, tagged with the rental count
    by_tkr: dict = {}
    for tkr, cov_d, line in fires:
        cur = by_tkr.get(tkr)
        if cur is None or cov_d > cur[0]:
            by_tkr[tkr] = (cov_d, line, 1 if cur is None else cur[2] + 1)
        else:
            by_tkr[tkr] = (cur[0], cur[1], cur[2] + 1)
    fired = [f"RE-ENTRY: {line}"
             + (f" · {n} prior rental(s) on shelf" if n > 1 else "")
             for _, line, n in by_tkr.values()]
    return {"fires": fired, "watching": watching, "expired": expired}


def stats() -> str:
    r = _rows("""
        SELECT count(*),
               count(*) FILTER (WHERE status='re-entered'),
               count(*) FILTER (WHERE status='expired'),
               avg(realized_pl) FILTER (WHERE realized_pl IS NOT NULL)
        FROM short_shelf""")
    n, re_n, ex_n, avg_pl = r[0]
    return (f"shelf lifetime: {n} covers · {re_n} re-shorted · {ex_n} expired "
            f"· avg banked {float(avg_pl):+.0f}/cover" if n else
            "shelf empty — no covers swept yet")


# ── Telegram ──────────────────────────────────────────────────────────────

def handle_shelf_command(text: str):
    t = (text or "").strip().upper()
    if t not in (SENTINEL, "WATCH SHELF", "SHELF STATS"):
        return None
    try:
        new, re_n = sweep()
        ev = evaluate()
        L = [f"🗄 WATCH SHELF — {dt.date.today()} "
             f"(swept: +{new} new, {re_n} marked re-entered)"]
        if ev["fires"]:
            L += ["", "🔔 " + "\n🔔 ".join(ev["fires"])]
        if ev["watching"]:
            L += ["", "watching (need roster + BEARISH + rp≥0.65):"]
            L += ["  " + w for w in ev["watching"]]
        if ev["expired"]:
            L += ["", "expired this pass:"] + ["  ✝ " + e for e in ev["expired"]]
        if not (ev["fires"] or ev["watching"]):
            L += ["", "nothing on the shelf."]
        L += ["", stats(),
              "note: PS stores ranks with no side — shown as ps#N, never "
              "counted as a short roster. SS counts only with fresh BEARISH."]
        return "\n".join(L)
    except Exception as e:  # noqa: BLE001
        log.exception("SHELF failed")
        return f"🛑 SHELF error: {e}"
