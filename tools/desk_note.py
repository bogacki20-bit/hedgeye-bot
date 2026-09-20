"""NOTE — the institutional desk note card (Telegram command).

Anatomy, in the order a PM reads it (operator ask 9/19: 'build the whole
thing'):
  STANCE      one-line posture: net/gross, regime, cash
  EXPOSURE    long/short/gross/net, sleeve weights, options risk
  ATTRIBUTION what made/lost money since the prior snapshot (positions held
              with unchanged quantity both days — traded names are marked,
              not guessed)
  DELTAS      what CHANGED: tilt flip, dealer walls that moved >=1% on held
              names, holdings that crossed zone lines, SS roster changes
  CATALYSTS   earnings inside 14 days on held names (EquityHub dates) +
              option expiries in the book
  FLAGS       thesis-check cards for positions needing eyes (trend-against,
              pressed at walls, range edges)

NOTE       -> chat card (flagged positions only)
NOTE FULL  -> .txt document with a thesis card for EVERY position
Point-in-time, provenance-tagged, same order every day. Python owns the
math; no LLM.
"""

from __future__ import annotations

import datetime as dt

SENTINELS = ("NOTE", "DESK NOTE", "NOTE FULL")

CASH_LIKE = {"CLOX", "BUXX", "VTIP", "DBMF", "FDRXX", "SPAXX", "CORE"}
INVERSE = {"TBT": "TLT"}


def _rows(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def _money(v):
    return f"${abs(v):,.0f}" if v >= 0 else f"-${abs(v):,.0f}"


def _book(snapshot=None):
    """[(symbol, qty, mv, gl_pct, is_option, opt_expiry, sector)] latest or given."""
    cond = "= %s" if snapshot else "= (SELECT max(snapshot_date) FROM book_positions)"
    args = (snapshot,) if snapshot else ()
    return _rows(f"""
        SELECT b.symbol, sum(b.quantity), sum(b.market_value),
               max(b.total_gl_pct), bool_or(b.is_option), max(b.opt_expiry),
               max(t.gics_sector), max(t.hedgeye_bucket_0629),
               sum(b.cost_basis)
        FROM book_positions b LEFT JOIN ticker_tags t ON t.ticker = b.symbol
        WHERE b.snapshot_date {cond} AND b.asset_class <> 'cash'
        GROUP BY b.symbol""", args)


def _snap_dates():
    r = _rows("SELECT DISTINCT snapshot_date FROM book_positions "
              "ORDER BY snapshot_date DESC LIMIT 2")
    return [x[0] for x in r]


def _cash_total():
    r = _rows("SELECT sum(market_value) FROM book_positions "
              "WHERE snapshot_date=(SELECT max(snapshot_date) FROM book_positions) "
              "AND asset_class='cash'")
    return float(r[0][0] or 0) if r else 0.0


def build_note(full: bool = False):
    dates = _snap_dates()
    today = dates[0] if dates else dt.date.today()
    prev = dates[1] if len(dates) > 1 else None
    book = _book()
    prev_book = {r[0]: r for r in _book(prev)} if prev else {}

    longs = shorts = 0.0
    sleeves: dict = {}
    for s, q, mv, gl, is_opt, exp, sector, bucket, cb in book:
        if s in CASH_LIKE or mv is None:
            continue
        mv = float(mv)
        eff = -abs(mv) if (s in INVERSE and mv > 0) else mv
        if eff >= 0:
            longs += eff
        else:
            shorts += eff
        sleeves[sector or ("options" if is_opt else "other")] = \
            sleeves.get(sector or ("options" if is_opt else "other"), 0) + abs(mv)
    gross = longs - shorts
    cash = _cash_total()

    # tilt + regime
    tilt_rows = _rows("SELECT DISTINCT ON (sym) sym, trade_date, gamma_tilt "
                      "FROM sg_tilt ORDER BY sym, trade_date DESC")
    spx_tilt = next((float(g) for s, d, g in tilt_rows if s == "SPX"), None)
    # three-state rule (9/20): hard 1.00 split flipped regime 4x in the
    # 9/14 week on a 0.21 tilt range — inside 0.90-1.10 the dial is MIXED.
    regime = ("?" if spx_tilt is None else
              "fade (pinned)" if spx_tilt > 1.10 else
              "follow (short-gamma)" if spx_tilt < 0.90 else
              "MIXED (0.90-1.10 — unresolved, size between)")

    # ── attribution vs prior snapshot ──
    attr: dict = {}
    traded = []
    if prev:
        for s, q, mv, gl, is_opt, exp, sector, bucket, cb in book:
            if s in CASH_LIKE or mv is None:
                continue
            pv = prev_book.get(s)
            if not pv or pv[1] is None or q is None:
                continue
            if abs(float(pv[1]) - float(q)) > 1e-6:
                traded.append(s)
                continue
            d = float(mv) - float(pv[2] or 0)
            key = sector or ("options" if is_opt else "other")
            attr[key] = attr.get(key, 0) + d
    total_attr = sum(attr.values())

    # ── deltas: walls moved on held names ──
    held = [r[0] for r in book if r[0] not in CASH_LIKE]
    wall_moves = []
    for t, d1, cw1, pw1, d0, cw0, pw0 in _rows("""
        WITH w AS (
          SELECT ticker, snapshot_date, call_wall, put_wall,
                 lag(snapshot_date) OVER (PARTITION BY ticker ORDER BY snapshot_date) pd,
                 lag(call_wall) OVER (PARTITION BY ticker ORDER BY snapshot_date) pcw,
                 lag(put_wall) OVER (PARTITION BY ticker ORDER BY snapshot_date) ppw
          FROM spotgamma_snapshots WHERE ticker = ANY(%s)
            AND snapshot_date >= CURRENT_DATE - 7)
        SELECT ticker, snapshot_date, call_wall, put_wall, pd, pcw, ppw FROM w
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM spotgamma_snapshots)
          AND pcw IS NOT NULL""", (held,)):
        try:
            if cw1 and cw0 and abs(float(cw1) - float(cw0)) / float(cw0) >= 0.01:
                wall_moves.append(f"{t} cw {float(cw0):g}→{float(cw1):g}")
            elif pw1 and pw0 and abs(float(pw1) - float(pw0)) / float(pw0) >= 0.01:
                wall_moves.append(f"{t} pw {float(pw0):g}→{float(pw1):g}")
        except (TypeError, ZeroDivisionError):
            continue

    # roster changes (last 2 days)
    roster = _rows("SELECT ticker, added_on, NULL FROM ss_roster_current "
                   "WHERE added_on >= CURRENT_DATE - 2 "
                   "UNION ALL SELECT ticker, NULL, removed_on FROM ss_roster_history "
                   "WHERE removed_on >= CURRENT_DATE - 2")
    r_add = [t for t, a, _ in roster if a]
    r_del = [t for t, _, d in roster if d]

    # ── catalysts ──
    earns = _rows("""
        SELECT DISTINCT ON (ticker) ticker, earnings_date FROM spotgamma_snapshots
        WHERE ticker = ANY(%s) AND earnings_date IS NOT NULL
          AND earnings_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 14
        ORDER BY ticker, snapshot_date DESC""", (held,))
    expiries = sorted({(str(e), s) for s, q, mv, gl, o, e, *_ in book if o and e})

    # ── thesis cards ──
    scr = {r[0]: r for r in _rows(
        "SELECT ticker, range_pos, trend_dir, price, range_low, range_high "
        "FROM v_screener WHERE ticker = ANY(%s)", (held,))}
    sg = {r[0]: r for r in _rows(
        "SELECT DISTINCT ON (ticker) ticker, call_wall, put_wall FROM "
        "spotgamma_snapshots WHERE ticker = ANY(%s) AND snapshot_date >= "
        "CURRENT_DATE - 3 ORDER BY ticker, snapshot_date DESC", (held,))}
    entries = {r[0]: r[1] for r in _rows(
        "SELECT normalized_symbol, min(run_date) FROM actions_log "
        "WHERE normalized_symbol = ANY(%s) AND run_date >= CURRENT_DATE - 120 "
        "AND action ILIKE 'YOU BOUGHT%%' OR action ILIKE '%%SHORT SALE%%' "
        "GROUP BY normalized_symbol", (held,))}

    cards, flags = [], []
    for s, q, mv, gl, is_opt, exp, sector, bucket, cb in sorted(
            book, key=lambda r: -abs(float(r[2] or 0))):
        if s in CASH_LIKE or is_opt or mv is None:
            continue
        side = "SHORT" if float(q or 0) < 0 else "long"
        v = scr.get(s)
        rp = f"{float(v[1]):.2f}" if v and v[1] is not None else "?"
        tr = (v[2] or "?") if v else "?"
        w = sg.get(s)
        wall = ""
        flag = []
        if v and w and v[3] and w[1]:
            px, cw, pw = float(v[3]), float(w[1]), float(w[2] or 0)
            if side == "long" and cw and (cw - px) / px < 0.02:
                flag.append("at-call-wall")
            if side == "SHORT" and pw and (px - pw) / px < 0.02:
                flag.append("on-put-wall")
            wall = f" ⋄{cw:g}/{pw:g}"
        against = (side == "long" and tr == "BEARISH") or \
                  (side == "SHORT" and tr == "BULLISH")
        if against:
            flag.append("TREND-AGAINST")
        if v and v[1] is not None:
            if side == "long" and float(v[1]) > 0.85:
                flag.append("range-top")
            if side == "SHORT" and float(v[1]) < 0.15:
                flag.append("range-bottom")
        ent = entries.get(s)
        card = (f"{s:<7}{side:<6}{_money(float(mv)):>9}  {tr[:4]}·rp{rp}{wall}  "
                f"gl={float(gl):+.1f}%" if gl is not None else
                f"{s:<7}{side:<6}{_money(float(mv)):>9}  {tr[:4]}·rp{rp}{wall}")
        card += f"  in:{ent}" if ent else ""
        card += ("  🚩" + ",".join(flag)) if flag else ""
        cards.append(card)
        if flag:
            flags.append(card)

    # ── assemble ──
    L = [f"🗂 DESK NOTE — {today} (prev {prev or '—'})",
         f"STANCE: net {_money(longs + shorts)} ({longs / gross * 100:.0f}/"
         f"{-shorts / gross * 100:.0f} L/S) · gross {_money(gross)} · "
         f"cash {_money(cash)} · SPX regime: {regime}"
         + (f" (tilt {spx_tilt:.2f})" if spx_tilt else ""),
         "",
         "EXPOSURE by sleeve (gross):"]
    for k, v in sorted(sleeves.items(), key=lambda kv: -kv[1])[:8]:
        L.append(f"  {k or 'untagged':<24}{_money(v):>10}  {v / gross * 100:.0f}%")
    L.append("")
    if prev:
        L.append(f"ATTRIBUTION {prev}→{today} (held-unchanged only): "
                 f"{_money(total_attr)}")
        for k, v in sorted(attr.items(), key=lambda kv: -abs(kv[1]))[:6]:
            L.append(f"  {k or 'untagged':<24}{_money(v):>10}")
        if traded:
            L.append(f"  (traded, excluded: {', '.join(sorted(traded)[:10])}"
                     + ("…" if len(traded) > 10 else "") + ")")
    L.append("")
    L.append("DELTAS:")
    if wall_moves:
        L.append("  walls moved: " + "; ".join(wall_moves[:8]))
    if r_add or r_del:
        L.append(f"  SS roster: +{','.join(r_add) or '—'} −{','.join(r_del) or '—'}")
    if not wall_moves and not (r_add or r_del):
        L.append("  none material")
    L.append("")
    L.append("CATALYSTS (14d):")
    for t, e in earns[:8]:
        L.append(f"  {t} earnings {e}")
    for e, s in expiries:
        L.append(f"  {s} option expiry {e}")
    if not earns and not expiries:
        L.append("  none on held names")

    # ── WATCH SHELF: covered shorts eligible to re-rent (9/20) — the
    #    recycle half of the short doctrine, off the operator's memory ──
    try:
        from tools.short_shelf import evaluate, sweep
        sweep()
        ev = evaluate()
        if ev["fires"]:
            L.append("")
            L.append(f"🗄 SHELF — {len(ev['fires'])} re-entry trigger(s) live "
                     f"(roster + BEARISH + rp≥0.65):")
            L.extend("  🔔 " + f for f in ev["fires"])
        elif ev["watching"]:
            L.append("")
            L.append(f"🗄 SHELF: {len(ev['watching'])} covered short(s) "
                     f"watching, none at re-entry (text SHELF for the list)")
    except Exception:  # noqa: BLE001
        pass

    # ── standing programs: BUXX accumulation ($36K/yr, buy the post-
    #    distribution dip; watcher nudges the window — this line keeps the
    #    program visible on every card) ──
    try:
        # Individual account only (9/20): the program is the margined
        # borrowing base in X96383748; the Roth's BUXX doesn't count.
        prog = _rows("""
            SELECT COALESCE(sum(abs(amount)),0) FROM actions_log
            WHERE normalized_symbol='BUXX' AND run_date >= '2026-09-01'
              AND account_number = 'X96383748'
              AND action ILIKE 'YOU BOUGHT%%'""")
        bought = float(prog[0][0])
        months = max((today.year - 2026) * 12 + today.month - 9 + 1, 1)
        pace = months * 3000.0
        # distributions land ~27th-30th monthly; estimate the next one
        nxt = today.replace(day=27) if today.day < 27 else \
            (today.replace(day=1) + dt.timedelta(days=32)).replace(day=27)
        L.append("")
        L.append(f"PROGRAM — BUXX $36K/yr: ${bought:,.0f} vs ${pace:,.0f} pace "
                 f"({'ON PACE' if bought >= pace else f'${pace - bought:,.0f} behind'}) "
                 f"· next distribution ≈{nxt} (buy the post-ex-div dip)")
    except Exception:  # noqa: BLE001
        pass
    L.append("")
    if full:
        L.append(f"THESIS CARDS — all {len(cards)} positions "
                 f"(in: = first entry ≤120d; 🚩 = needs eyes):")
        L.extend("  " + c for c in cards)
    else:
        L.append(f"FLAGS ({len(flags)} of {len(cards)} positions need eyes; "
                 f"NOTE FULL for every card):")
        L.extend("  " + c for c in flags[:15])
    L.append("")
    L.append("provenance: book=Fidelity daily · ranges=Hedgeye/MFR gated · "
             "walls=EquityHub (noise on sparse-OI) · tilt=SG indices · "
             "point-in-time throughout")
    body = "\n".join(L)
    if full:
        return {"document_name": f"desk_note_{today}.txt",
                "document_text": body,
                "caption": f"🗂 Desk note {today} — full thesis cards"}
    return body[:4000]


def handle_note_command(text: str):
    if not text:
        return None
    up = text.strip().upper()
    if up in ("NOTE", "DESK NOTE"):
        return build_note(False)
    if up == "NOTE FULL":
        return build_note(True)
    return None
