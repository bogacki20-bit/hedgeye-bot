"""
report.py — the REPORT command. v4 (2026-07-11 spec) + v3 as REPORT LEGACY.

Dense structured FACTS, no narrative — output is uploaded to LLM
conversations for capital-allocation reasoning. The receiving model reasons;
this bot states. Anything not printed does not exist for the LLM, so v4
widens the facts: position context on flagged names, cash, concentration,
alert contents, flow-quality marks, a rule-based CANDIDATES nomination, and
a Δ-since-last header. Python computes everything; no LLM writes anywhere.

Telegram:  REPORT           v4 (stored kind='on-demand')
           REPORT FULL      v4 with the unfiltered ⚡DIV list
           REPORT LEGACY    v3 renderer (parallel-run week; no snapshot write)
Nightly:   store_eod() after the vol-regime write (kind='eod') — stored rows
           ARE the ML corpus.

Writes: report_rows (body) + report_snapshots (Δ state) only — report
infrastructure, never signal tables. Missing data prints as n/a with a
reason (no-silent-failures doctrine).
"""
from __future__ import annotations

import json
import logging
from datetime import date

log = logging.getLogger("report")

SENTINEL = "REPORT"
VERSION = "v4"


# ═══════════════════════ shared series helpers (v3 + v4) ═══════════════════

_SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
            "XLU", "XLV", "XLY"]
_MACRO = ["UUP", "TLT", "SHY", "LQD", "HYG"]


def _rp_series(cur, tickers, days=10) -> dict:
    """{ticker: [(date, rp, lo, hi), ...] ascending} computed in Python."""
    cur.execute(
        """SELECT ticker, snapshot_date, price, range_low, range_high
           FROM mfr_snapshots
           WHERE ticker = ANY(%s) AND snapshot_date >= CURRENT_DATE - %s
           ORDER BY ticker, snapshot_date""", (tickers, days))
    out: dict = {}
    for t, d, px, lo, hi in cur.fetchall():
        if px is None or lo is None or hi is None or float(hi) <= float(lo):
            continue
        rp = (float(px) - float(lo)) / (float(hi) - float(lo))
        out.setdefault(t, []).append((d, round(rp, 3), float(lo), float(hi)))
    return out


def _now_and_3d(series):
    if not series:
        return None, None
    now_d, now_rp = series[-1][0], series[-1][1]
    past = [row[1] for row in series if (now_d - row[0]).days >= 3]
    return now_rp, (past[-1] if past else None)


def _range_shape(series) -> str | None:
    """Range structure vs ~3 sessions ago (operator: a leading indicator —
    the range walks before price confirms):
      HH/HL ascending · LH/LL descending · HH/LL widening (vol building)
      · LH/HL compressing (vol coming out) · flat"""
    if not series:
        return None
    now_d, _, lo_n, hi_n = series[-1]
    past = [(lo, hi) for d, _, lo, hi in series if (now_d - d).days >= 3]
    if not past:
        return None
    lo_p, hi_p = past[-1]
    hh, hl = hi_n > hi_p, lo_n > lo_p
    lh, ll = hi_n < hi_p, lo_n < lo_p
    if hh and hl:   return "HH/HL"      # ascending range
    if lh and ll:   return "LH/LL"      # descending range
    if hh and ll:   return "HH/LL"      # widening — vol building
    if lh and hl:   return "LH/HL"      # compressing — resolving
    return "flat"


# ═══════════════════════ v4 pure logic (fixture-tested) ═════════════════════

def flow_mark(delta, shape) -> str:
    """P5 flow-quality: ✓ when Δrp sign agrees with range structure, ✗ on
    conflict (rising rp inside a descending range is a fade, not flow — the
    XLB/XLI/XLU 7/11 catch). Widening/compressing/flat/unknown: no mark."""
    if delta is None or not shape:
        return ""
    if delta > 0 and shape == "HH/HL":
        return "✓"
    if delta < 0 and shape == "LH/LL":
        return "✓"
    if delta > 0 and shape == "LH/LL":
        return "✗"
    if delta < 0 and shape == "HH/HL":
        return "✗"
    return ""


# TRANCHE v2 (2026-07-11, same session as v1 — wrong data worse than
# missing): buy-count inference REPLACED by %-of-target fill. All fill math
# and formatting lives in tools/position_targets (fill_bucket, fmt_fill_ctx,
# compute_fills); the T1/3 notation is gone from the report.


def delta_line(prev, cur) -> str:
    """P7 Δ-header vs the previous report_snapshots state. prev None on the
    first run. States: {flags:[], sector_rp:{t:rp}, ss_book_drops:[]}."""
    if prev is None:
        return "Δ since last: first snapshot (no prior state)"
    parts = []
    new_flags = sorted(set(cur.get("flags", [])) - set(prev.get("flags", [])))
    if new_flags:
        parts.append(f"{len(new_flags)} new ⚠ ({' '.join(new_flags)})")
    drops = sorted(set(cur.get("ss_book_drops", []))
                   - set(prev.get("ss_book_drops", [])))
    if drops:
        parts.append(f"{len(drops)} SS drop affects book ({' '.join(drops)})")
    prev_rp, cur_rp = prev.get("sector_rp", {}), cur.get("sector_rp", {})
    moves = [(t, cur_rp[t] - prev_rp[t]) for t in cur_rp if t in prev_rp]
    if moves:
        t, d = max(moves, key=lambda x: abs(x[1]))
        if abs(d) >= 0.10:
            parts.append(f"{t} flow {d:+.2f}")
    return "Δ since last: " + (" · ".join(parts) or "none")


def conc_clusters(positions) -> dict:
    """P3 concentration. positions: [(ticker, weight_pct, tags|None)] where
    tags = (gics_sector, rate_sensitive, duration_char, commodity_linked,
            exposure, inverse). Older 3-tuples (sector, rate, dur) are still
    accepted (padded). Clusters OVERLAP by design (a name can be
    rate_sensitive AND healthcare AND duration) — each is a fact; a single
    position never counts twice into the SAME cluster.

    Two honest residual buckets replace the old lying 'untagged':
      • 'no-tags' — the name is ABSENT from ticker_tags (genuinely unknown).
      • 'no-sector' — the name HAS a row but no grouping axis fired (it IS
        tagged — instrument etc. — just not on a sector/theme axis).
        Renamed from 'no-gics' on 2026-08-16: the sector axis is now
        ticker_tags.hedgeye_group (Hedgeye's own 15 Position Monitor sectors),
        so a bucket labelled 'no-gics' would have told the operator the wrong
        thing about which taxonomy was missing.

    SECTOR AXIS (2026-08-16). tags[0] is whatever sector the CALLER supplies;
    this function is taxonomy-agnostic. build_report_v4 now supplies
    hedgeye_group, matching SCREEN (89ce30e). It previously supplied
    gics_sector, so CONC and SCREEN grouped by two different taxonomies.

    ETFs now cluster on commodity_linked -> 'commodity', the non-GICS exposure
    axis ('-proxy' folded in, so commodity-proxy joins 'commodity'), and
    inverse -> 'inverse'. That makes a correlated commodity/thematic book
    visible to CONC (the energy-lesson failure mode).
    Returns {cluster: [n_pos, weight_pct]}."""
    out: dict = {}

    def add(key, w):
        c = out.setdefault(key, [0, 0.0])
        c[0] += 1
        c[1] += w

    for _t, w, tags in positions:
        if tags is None:
            add("no-tags", w)
            continue
        sector, rate_sens, dur, commodity, exposure, inverse = (
            list(tags) + [None] * 6)[:6]
        keys = set()
        if rate_sens:
            keys.add("rate_sensitive")
        if sector:
            keys.add(str(sector).strip().lower().replace(" ", "_"))
        if dur:
            # 'dur:' prefix — duration_char values like 'long' would otherwise
            # read as a position side in the CONC line (2026-07-11 dry-run).
            keys.add("dur:" + str(dur).strip().lower().replace(" ", "_"))
        if commodity:
            keys.add("commodity")
        if exposure:
            keys.add((str(exposure).strip().lower().replace(" ", "_")
                      .replace("-proxy", "")))   # commodity-proxy -> commodity
        if inverse:
            keys.add("inverse")
        for k in (keys or {"no-sector"}):
            add(k, w)
    return out


_CONC_TRAILING = ("no-sector", "no-tags")   # honest residuals, shown last


def conc_line(clusters, top_n=3) -> str:
    real = {k: v for k, v in clusters.items() if k not in _CONC_TRAILING}
    if not real and not any(k in clusters for k in _CONC_TRAILING):
        return "CONC: n/a (no positions)"
    top = sorted(real.items(), key=lambda kv: -kv[1][1])[:top_n]
    parts = [f"{k} {n}pos/{w:.1f}%w" for k, (n, w) in top]
    for k in _CONC_TRAILING:
        if k in clusters:
            n, w = clusters[k]
            parts.append(f"{k} {n}pos/{w:.1f}%w")
    line = "CONC: " + " · ".join(parts)
    if top:
        line += f" · top_cluster: {top[0][0]}"
    return line


def candidates_line(rows, cap=12) -> str:
    """P6 nomination, not recommendation — rule printed inline, no ranking
    beyond rp sort, no buy language. rows: [(ticker, rp, fill|None)] already
    filtered by the rule; fill is printed on held names (deploy context)."""
    rule = "[rule: TREND=BULL + rp<0.35 + fill<80%]"
    if not rows:
        return f"CANDIDATES: none {rule}"
    parts = [f"{t}({rp:.2f}{f',held {f:.0f}%fill' if f is not None else ''})"
             for t, rp, f in rows[:cap]]
    more = f" +{len(rows) - cap} more" if len(rows) > cap else ""
    return "CANDIDATES: " + " ".join(parts) + more + f"  {rule}"


def fmt_fills(rows, latest, today, cap=14) -> str:
    """Pure: today's executed fills from actions_log.

    rows: [(symbol, action_raw, qty, price)] for `today` only.
    latest: max(run_date) present in actions_log (date | None) — printed when
    there are no fills today, so a book the operator forgot to sync reads as
    STALE rather than as a quiet 'nothing happened'.

    Aggregated per (symbol, side) with a size-weighted average price. Buys are
    +, sells are −; a same-day round trip prints both legs."""
    if not rows:
        if latest is None:
            return ("FILLS: none today · actions_log EMPTY (send "
                    "Accounts_History.csv — no trade history at all)")
        age = (today - latest).days
        stale = (f" · ⚠{age}d stale — send Accounts_History.csv"
                 if age >= 1 else "")
        return f"FILLS: none today · actions_log latest {latest}{stale}"
    agg: dict = {}
    for sym, action, qty, price in rows:
        a = (action or "").upper()
        side = "+" if "BOUGHT" in a else ("-" if "SOLD" in a else "?")
        q = abs(float(qty)) if qty is not None else 0.0
        c = agg.setdefault((sym or "?", side), [0.0, 0.0, 0])
        c[0] += q
        if price is not None:
            c[1] += q * float(price)
        c[2] += 1
    parts = []
    for (sym, side), (q, notional, n) in sorted(agg.items()):
        px = (notional / q) if q else None
        parts.append(f"{side}{q:g} {sym}"
                     + (f"@{px:,.2f}" if px else "")
                     + (f"×{n}" if n > 1 else ""))
    more = f" +{len(parts) - cap} more" if len(parts) > cap else ""
    return (f"FILLS today ({len(rows)}): " + " · ".join(parts[:cap]) + more)


def fills_line(cur) -> str:
    today = date.today()
    cur.execute("""SELECT COALESCE(normalized_symbol, raw_symbol), action,
                          qty, price
                   FROM actions_log WHERE run_date = %s ORDER BY id""",
                (today,))
    rows = cur.fetchall()
    cur.execute("SELECT max(run_date) FROM actions_log")
    r = cur.fetchone()
    return fmt_fills(rows, r[0] if r else None, today)


def _money(v) -> str:
    return f"${v:,.0f}"


# ═══════════════════════ v4 data assembly (guarded per section) ═════════════

def _cash_total(cur):
    cur.execute("""
        SELECT sum(market_value) FROM book_positions
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
          AND asset_class = 'cash'""")
    r = cur.fetchone()
    return float(r[0]) if r and r[0] is not None else None


def _ss_current(cur) -> set:
    cur.execute("SELECT ticker FROM ss_roster_history WHERE removed_on IS NULL")
    return {r[0] for r in cur.fetchall()}


def _alert_lines(cur, cap=8) -> list:
    """P4: alert CONTENTS, not counts. mkt·<tkr> <boundary> <zone> +
    book·<tkr> <type>. Cap then '+N more'."""
    items = []
    cur.execute("""SELECT ticker, boundary, COALESCE(range_zone, '')
                   FROM alerts_fired WHERE fired_at::date = CURRENT_DATE
                   ORDER BY fired_at""")
    for t, b, z in cur.fetchall():
        items.append(f"mkt·{t} {b}{(' ' + z) if z else ''}")
    cur.execute("""SELECT ticker, alert_type FROM book_alerts_fired
                   WHERE fired_on = CURRENT_DATE ORDER BY id""")
    for t, a in cur.fetchall():
        items.append(f"book·{t} {a}")
    if not items:
        return ["ALERTS: none today"]
    head = items[:cap]
    more = f" +{len(items) - cap} more" if len(items) > cap else ""
    return ["ALERTS: " + " · ".join(head) + more]


def _prev_snapshot(cur):
    cur.execute("SELECT state FROM report_snapshots ORDER BY created_at DESC "
                "LIMIT 1")
    r = cur.fetchone()
    if not r:
        return None
    return r[0] if isinstance(r[0], dict) else json.loads(r[0])


def build_book_table(fills: dict) -> str:
    """FIX 3/4: the per-(ticker,account) position table — dry-run, BOOK FULL
    and upload-mode appendix. One row per account leg + TOTAL row on splits."""
    rows = fills.get("per_acct", [])
    agg = fills.get("agg", {})
    out = [f"{'tkr':<8}{'acct':<6}{'acct%':>7}{'tgt%':>7} {'src':<11}"
           f"{'fill%':>7}  {'bucket':<9}{'pl%':>8}"]

    def _n(v, fmt=".1f", suf="%"):
        return f"{v:{fmt}}{suf}" if v is not None else "?"

    split = {t for t, c in agg.items() if c.get("multi")}
    for t, acct, pct, tgt, src, fill, bucket, pl, _g in rows:
        out.append(f"{t:<8}{acct:<6}{_n(pct):>7}{_n(tgt):>7} "
                   f"{(src or 'set'):<11}{_n(fill, '.0f'):>7}  {bucket:<9}"
                   f"{_n(pl, '+.1f'):>8}")
    for t in sorted(split):
        c = agg[t]
        out.append(f"{t:<8}{'TOTAL':<6}{_n(c['acct_pct']):>7}{'—':>7} "
                   f"{(c['tgt_src'] or 'set'):<11}"
                   f"{_n(c['fill'], '.0f'):>7}  {c['bucket']:<9}"
                   f"{_n(c['pl'], '+.1f'):>8}  [{c['acct']}]")
    ce = fills.get("cash_equiv", {})
    if ce:
        out.append("cash-equiv (parked, excluded): "
                   + " ".join(f"{t} ${v:,.0f}" for t, v in sorted(ce.items())))
    return "\n".join(out)


def build_report_v4(kind: str = "on-demand", full: bool = False,
                    verbose: bool = False, persist_snapshot: bool = True,
                    fills_override: dict | None = None) -> str:
    """fills_override: pre-computed (possibly SIMULATED) compute_fills()
    result — lets the apply-script dry run render the post-seed report
    without writing anything."""
    import db_pg
    from tools.ps_flow import _quad_for
    lines = []
    state = {"flags": [], "sector_rp": {}, "ss_book_drops": []}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        today = date.today()

        # ── P7 header: version · legend ──
        lines.append(f"REPORT {VERSION} {today} [{kind}] · rp=range pos 0-1 · "
                     f"⚠=trend-against 📉=dip/rip · fill=% of target "
                     f"(<40 STARTER · <80 BUILDING · ≤110 FULL · >110 OVER)")
        # BOOK AGE, stated ALWAYS. Everything below that touches positions,
        # weights, fills or CONC is computed from this snapshot — see
        # tools/book_freshness for why this is never silent.
        try:
            from tools.book_freshness import book_banner
            lines.append(book_banner(today))
        except Exception as e:
            lines.append(f"!! BOOK AGE UNKNOWN ({e}) — treat every position "
                         f"figure below as unverified.")
        delta_idx = len(lines)          # Δ line inserted here after assembly
        try:
            mq, qq = _quad_for(cur, today)
            cur.execute("SELECT max(effective_at) FROM quad_regime_history")
            conf = cur.fetchone()[0]
            lines.append(f"QUAD: monthly={mq or '?'} quarterly={qq or '?'} "
                         f"(last confirm {str(conf)[:10] if conf else 'NONE'})")
        except Exception as e:
            lines.append(f"QUAD: unavailable ({e})")
        try:
            from tools.vol_regime import regime_line
            lines.append(regime_line())
        except Exception as e:
            lines.append(f"VOL: unavailable ({e})")

        # ── sector flow + P5 quality marks · range dynamics · macro ──
        ser = {}
        try:
            ser = _rp_series(cur, _SECTORS + _MACRO)
            shapes = {t: _range_shape(ser.get(t, [])) for t in _SECTORS + _MACRO}
            flows = []
            for t in _SECTORS:
                now_rp, past_rp = _now_and_3d(ser.get(t, []))
                if now_rp is None:
                    continue
                d = (now_rp - past_rp) if past_rp is not None else None
                flows.append((t, now_rp, d, flow_mark(d, shapes.get(t))))
                state["sector_rp"][t] = now_rp
            flows.sort(key=lambda x: (x[2] is None, -(x[2] or 0)))
            fl = " ".join(f"{t}:{rp:.2f}({'+' if d and d >= 0 else ''}{d:.2f}){m}"
                          if d is not None else f"{t}:{rp:.2f}(?){m}"
                          for t, rp, d, m in flows)
            lines.append("SECTOR FLOW (rp, Δ3d, money-in first; ✓=Δ agrees w/ "
                         "range structure ✗=fade-vs-structure): "
                         + (fl or "no data"))

            sh = {t: s for t, s in shapes.items() if s}
            by_shape: dict = {}
            for t, s in sh.items():
                by_shape.setdefault(s, []).append(t)
            rd = " · ".join(f"{s}: {' '.join(sorted(ts))}"
                            for s, ts in sorted(by_shape.items(),
                                                key=lambda kv: -len(kv[1])))
            lines.append("RANGE DYNAMICS (vs 3d — HH/HL asc, LH/LL desc, "
                         "HH/LL widening, LH/HL compressing): " + (rd or "no data"))
            macro = []
            for t in _MACRO:
                now_rp, past_rp = _now_and_3d(ser.get(t, []))
                if now_rp is None:
                    continue
                d = (now_rp - past_rp) if past_rp is not None else None
                macro.append(f"{t}:{now_rp:.2f}"
                             + (f"({'+' if d >= 0 else ''}{d:.2f})"
                                if d is not None else "(?)"))
            lines.append("DOLLAR+BONDS: " + (" ".join(macro) or "no data"))
        except Exception as e:
            lines.append(f"SECTOR FLOW/RANGE/MACRO: unavailable ({e})")

        # ── RS / grid (companion to sector flow) ──
        try:
            from tools.relative_strength import render_report_block
            lines.append(render_report_block())
        except Exception as e:
            lines.append(f"RS/GRID: unavailable ({e})")

        # ── RS pairwise matrix (sectors + QQQ + IWM) ──
        try:
            from tools.rs_matrix import render_report_block as _rspair
            lines.append(_rspair())
        except Exception as e:
            lines.append(f"RS PAIRS: unavailable ({e})")

        # ── book risk clusters (full correlation matrix) ──
        try:
            from tools.correlation_matrix import render_report_block as _bookrisk
            lines.append(_bookrisk())
        except Exception as e:
            lines.append(f"BOOK RISK: unavailable ({e})")

        # ── volume signal (decelerating-dip trigger) ──
        try:
            from tools.volume_signal import render_report_block as _vol_block
            lines.append(_vol_block())
        except Exception as e:
            lines.append(f"VOLUME: unavailable ({e})")

        # ── SS flow ──
        try:
            from tools.ss_flow import churn_summary
            lines.append(churn_summary(5))
        except Exception as e:
            lines.append(f"SS FLOW: unavailable ({e})")

        # ── BOOK (exposure counts, cash-equivs excluded) + fill context ──
        held_set: set = set()
        ctx: dict = {}
        fills: dict = {}
        gross = cash = None
        try:
            from tools.book_alerts import _book_rows, BOOK_DIP_DELTA
            from tools.book_direction import book_sides
            from tools.position_targets import compute_fills, fmt_fill_ctx
            rows = _book_rows()
            _sides = book_sides()
            fills = fills_override or compute_fills(cur, _sides)
            ctx, gross = fills["agg"], fills["gross"]
            ce = set(fills["cash_equiv"])
            held_set = {t for t, v in _sides.items()
                        if v.get("side") in ("long", "short") and t not in ce}
            longs = sum(1 for t, v in _sides.items()
                        if v.get("side") == "long" and t not in ce)
            shorts = sum(1 for t, v in _sides.items()
                         if v.get("side") == "short" and t not in ce)
            cash = _cash_total(cur)
            flags = []
            for r in rows:
                t = r["ticker"]
                if t in ce:
                    continue                      # parked cash, not a position
                side, td = r["side"], r.get("trend_dir") or "?"
                against = ((side == "long" and td == "BEARISH") or
                           (side == "short" and td == "BULLISH"))
                rp = r.get("rp_now")
                in_zone = False
                if rp is not None and side == "long" and r.get("rp_5d_max") is not None:
                    in_zone = (r["rp_5d_max"] - rp) >= BOOK_DIP_DELTA and td == "BULLISH"
                if rp is not None and side == "short" and r.get("rp_5d_min") is not None:
                    in_zone = in_zone or ((rp - r["rp_5d_min"]) >= BOOK_DIP_DELTA
                                          and td == "BEARISH")
                mark = ("⚠" if against else "") + ("📉" if in_zone else "")
                if not mark:
                    continue
                if against:
                    state["flags"].append(t)
                c = ctx.get(t, {})
                rp_s = f"{rp:.2f}" if rp is not None else "?"
                flags.append(f"{mark}{t}({side[0].upper()},rp{rp_s},{td[:4]}"
                             + fmt_fill_ctx(c.get("acct_pct"), c.get("fill"),
                                            c.get("tgt"), c.get("tgt_src"),
                                            c.get("acct"), c.get("pl"),
                                            verbose=verbose) + ")")
            lines.append(f"BOOK: {longs}L/{shorts}S exposure · flagged: "
                         + (" ".join(flags) or "none")
                         + "  [⚠=trend-against 📉=dip/rip-zone]")
        except Exception as e:
            lines.append(f"BOOK: unavailable ({e})")

        # ── P2 CASH (FIX 2: settled + parked = deployable) ──
        try:
            parked = fills.get("cash_equiv", {})
            parked_sum = sum(parked.values())
            if cash is None and not parked:
                lines.append("CASH: n/a (no cash rows in ingest — export/"
                             "ingest carries none)")
            else:
                settled = cash or 0.0
                deploy = settled + parked_sum
                aum = (gross or 0) + deploy
                pct = (deploy / aum * 100.0) if aum > 0 else 0.0
                parked_s = (f"{_money(parked_sum)} parked "
                            f"({' '.join(sorted(parked))})" if parked
                            else "$0 parked")
                lines.append(f"CASH: {_money(settled)} settled · {parked_s} · "
                             f"{_money(deploy)} deployable · {pct:.1f}% of AUM"
                             f" · unsettled n/a (not in Fidelity export)")
        except Exception as e:
            lines.append(f"CASH: unavailable ({e})")

        # ── P3 CONC ──
        try:
            if ctx:
                # hedgeye_group, NOT gics_sector (2026-08-16). SCREEN moved to
                # Hedgeye's own 15 PM sectors in 89ce30e; CONC grouping by the
                # provider taxonomy meant the concentration line and a sector
                # screen answered "how much ENERGY do I have" differently.
                cur.execute("""SELECT ticker, hedgeye_group, rate_sensitive,
                                      duration_char, commodity_linked,
                                      exposure, inverse
                               FROM ticker_tags WHERE ticker = ANY(%s)""",
                            (sorted(ctx),))
                tags = {t: (s, rs, dc, cl, xp, inv)
                        for t, s, rs, dc, cl, xp, inv in cur.fetchall()}
                positions = [(t, c["weight"] or 0.0, tags.get(t))
                             for t, c in ctx.items()]
                lines.append(conc_line(conc_clusters(positions)))
            else:
                lines.append("CONC: n/a (no positions)")
        except Exception as e:
            lines.append(f"CONC: unavailable ({e})")

        # ── P8 ⚡DIV — scoped to book ∪ bench ∪ SS unless REPORT FULL ──
        div_scope_n = 0
        try:
            cur.execute("SELECT ticker, divergence FROM v_screener "
                        "WHERE divergence IS NOT NULL ORDER BY ticker")
            dv_all = cur.fetchall()
            if full or verbose:
                dv = [f"{t}({d})" for t, d in dv_all]
                lines.append("⚡DIV (full): " + (" ".join(dv[:30]) or "none")
                             + (f" +{len(dv) - 30} more" if len(dv) > 30 else ""))
            else:
                ss = _ss_current(cur)
                cur.execute("SELECT ticker FROM ticker_tags "
                            "WHERE hedgeye_bucket_0629 LIKE %s", ("%bench%",))
                bench = {r[0] for r in cur.fetchall()}
                scope = held_set | bench | ss
                dv = [f"{t}({d})" for t, d in dv_all if t in scope]
                div_scope_n = len(dv)
                outside = len(dv_all) - len(dv)
                lines.append("⚡DIV (book∪bench∪SS): " + (" ".join(dv) or "none")
                             + (f" · +{outside} outside scope (REPORT FULL)"
                                if outside else ""))
        except Exception as e:
            lines.append(f"⚡DIV: unavailable ({e})")

        # ── P6 CANDIDATES (nomination by rule, judgment stays upstream) ──
        try:
            cur.execute("""SELECT ticker, range_pos, held FROM v_screener
                           WHERE trend_dir = 'BULLISH' AND range_pos < 0.35
                           ORDER BY range_pos""")
            cands = []
            for t, rp, h in cur.fetchall():
                fill = ctx.get(t, {}).get("fill") if h else None
                if h and fill is not None and fill >= 80:
                    continue                      # FULL/OVER — rule gate
                cands.append((t, float(rp), fill))
            lines.append(candidates_line(cands))
        except Exception as e:
            lines.append(f"CANDIDATES: unavailable ({e})")

        # ── T1A removed from the report body 2026-07-29 (operator): Tier One
        #    Alpha goes straight to the LLM as its own upload. tools/t1a_parse
        #    still runs on ingest and t1a_daily keeps building — it just
        #    doesn't spend report space. Same for the DAYPACK doc bundle.

        # ── FILLS: what actually got executed today (actions_log). Added
        #    2026-07-29 — the actions CSV used to write a table no report
        #    read, so uploading it changed nothing visible. ──
        try:
            lines.append(fills_line(cur))
        except Exception as e:
            lines.append(f"FILLS: unavailable ({e})")

        # ── P4 alert contents ──
        try:
            lines.extend(_alert_lines(cur))
        except Exception as e:
            lines.append(f"ALERTS: unavailable ({e})")

        # ── P7 Δ vs previous snapshot + SS drops touching book ──
        try:
            cur.execute("""SELECT ticker FROM ss_flow_events
                           WHERE event = 'drop' AND event_date = CURRENT_DATE""")
            state["ss_book_drops"] = sorted({r[0] for r in cur.fetchall()}
                                            & held_set)
            state["flags"] = sorted(set(state["flags"]))
            prev = _prev_snapshot(cur)
            lines.insert(delta_idx, delta_line(prev, state))
            if persist_snapshot:
                cur.execute("INSERT INTO report_snapshots (kind, state) "
                            "VALUES (%s, %s)", (kind, json.dumps(state)))
                conn.commit()
        except Exception as e:
            lines.insert(delta_idx, f"Δ since last: unavailable ({e})")

        # ── upload mode (FIX 4 Mode B): full position table appended ──
        if verbose and fills:
            try:
                lines.append("")
                lines.append("POSITION TABLE (per account · TOTAL on splits)")
                lines.append(build_book_table(fills))
            except Exception as e:
                lines.append(f"POSITION TABLE: unavailable ({e})")

    body = "\n".join(lines)
    # v4.1 FIX 3: compact render targets <3500 chars. If over, the DIV list
    # collapses to a count — detail always lives in REPORT UPLOAD.
    if not verbose and not full and len(body) > 3500:
        for i, ln in enumerate(lines):
            if ln.startswith("⚡DIV"):
                lines[i] = (f"⚡DIV: {div_scope_n} in scope "
                            f"(REPORT UPLOAD for detail)")
                body = "\n".join(lines)
                break
    return body


# ═══════════════════════ v3 (REPORT LEGACY — parallel-run week) ═════════════

def build_report_legacy(kind: str = "on-demand") -> str:
    import db_pg
    from tools.ps_flow import _quad_for
    lines = []
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        # ── header: date · quad (+confirm date) · vol line ──
        today = date.today()
        mq, qq = _quad_for(cur, today)
        cur.execute("SELECT max(effective_at) FROM quad_regime_history")
        conf = cur.fetchone()[0]
        lines.append(f"REPORT {today} [{kind}]")
        lines.append(f"QUAD: monthly={mq or '?'} quarterly={qq or '?'} "
                     f"(last confirm {str(conf)[:10] if conf else 'NONE'})")
        try:
            from tools.vol_regime import regime_line
            lines.append(regime_line())
        except Exception as e:
            lines.append(f"VOL: unavailable ({e})")

        # ── money flow: sector rp now vs 3d, ranked ──
        ser = _rp_series(cur, _SECTORS + _MACRO)
        flows = []
        for t in _SECTORS:
            now_rp, past_rp = _now_and_3d(ser.get(t, []))
            if now_rp is None:
                continue
            d = (now_rp - past_rp) if past_rp is not None else None
            flows.append((t, now_rp, d))
        flows.sort(key=lambda x: (x[2] is None, -(x[2] or 0)))
        fl = " ".join(f"{t}:{rp:.2f}({'+' if d and d>=0 else ''}{d:.2f})"
                      if d is not None else f"{t}:{rp:.2f}(?)"
                      for t, rp, d in flows)
        lines.append(f"SECTOR FLOW (rp, Δ3d, money-in first): {fl or 'no data'}")

        # ── range dynamics: where the ranges themselves are walking ──
        shapes = {t: _range_shape(ser.get(t, [])) for t in _SECTORS + _MACRO}
        shapes = {t: s for t, s in shapes.items() if s}
        by_shape: dict = {}
        for t, s in shapes.items():
            by_shape.setdefault(s, []).append(t)
        rd = " · ".join(f"{s}: {' '.join(sorted(ts))}"
                        for s, ts in sorted(by_shape.items(),
                                            key=lambda kv: -len(kv[1])))
        lines.append("RANGE DYNAMICS (vs 3d — HH/HL asc, LH/LL desc, "
                     "HH/LL widening, LH/HL compressing): " + (rd or "no data"))
        macro = []
        for t in _MACRO:
            now_rp, past_rp = _now_and_3d(ser.get(t, []))
            if now_rp is None:
                continue
            d = (now_rp - past_rp) if past_rp is not None else None
            macro.append(f"{t}:{now_rp:.2f}"
                         + (f"({'+' if d>=0 else ''}{d:.2f})" if d is not None else "(?)"))
        lines.append("DOLLAR+BONDS: " + (" ".join(macro) or "no data"))

        # ── SS flow ──
        try:
            from tools.ss_flow import churn_summary
            lines.append(churn_summary(5))
        except Exception as e:
            lines.append(f"SS FLOW: unavailable ({e})")

        # ── book state: side/rp/trend + thesis + dip-zone flags ──
        try:
            from tools.book_alerts import _book_rows, BOOK_DIP_DELTA
            from tools.book_direction import book_sides
            rows = _book_rows()
            # COUNT FRAME (2026-07-11 audit): counts are EXPOSURE side over ALL
            # sided holdings (book_sides) — the same frame as SCREEN "book
            # shorts", so the two never disagree. The ⚠ verdict below stays in
            # the RAW frame (raw side vs linkage-adjusted trend, frame-
            # invariant per the SBIT double-flip fix).
            _sides = book_sides()
            longs = sum(1 for v in _sides.values() if v.get("side") == "long")
            shorts = sum(1 for v in _sides.values() if v.get("side") == "short")
            flags = []
            for r in rows:
                side, td = r["side"], r.get("trend_dir") or "?"
                against = ((side == "long" and td == "BEARISH") or
                           (side == "short" and td == "BULLISH"))
                rp = r.get("rp_now")
                in_zone = False
                if rp is not None and side == "long" and r.get("rp_5d_max") is not None:
                    in_zone = (r["rp_5d_max"] - rp) >= BOOK_DIP_DELTA and td == "BULLISH"
                if rp is not None and side == "short" and r.get("rp_5d_min") is not None:
                    in_zone = in_zone or ((rp - r["rp_5d_min"]) >= BOOK_DIP_DELTA
                                          and td == "BEARISH")
                mark = ("⚠" if against else "") + ("📉" if in_zone else "")
                if mark:
                    rp_s = f"{rp:.2f}" if rp is not None else "?"
                    flags.append(f"{mark}{r['ticker']}({side[0].upper()},rp{rp_s},{td[:4]})")
            lines.append(f"BOOK: {longs}L/{shorts}S exposure · flagged: "
                         + (" ".join(flags) or "none")
                         + "  [⚠=trend-against 📉=dip/rip-zone]")
        except Exception as e:
            lines.append(f"BOOK: unavailable ({e})")

        # ── divergences (tomorrow's fade list) ──
        try:
            cur.execute("SELECT ticker, divergence FROM v_screener "
                        "WHERE divergence IS NOT NULL ORDER BY ticker")
            dv = [f"{t}({d})" for t, d in cur.fetchall()]
            lines.append("⚡DIV: " + (" ".join(dv[:20]) or "none")
                         + (f" +{len(dv)-20} more" if len(dv) > 20 else ""))
        except Exception as e:
            lines.append(f"⚡DIV: unavailable ({e})")

        # ── today's alert counts ──
        try:
            cur.execute("SELECT count(*) FROM alerts_fired "
                        "WHERE fired_at::date = CURRENT_DATE")
            a = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM book_alerts_fired "
                        "WHERE fired_on = CURRENT_DATE")
            b = cur.fetchone()[0]
            lines.append(f"ALERTS today: {a} market · {b} book")
        except Exception:
            pass

    return "\n".join(lines)


# build_report = the current default renderer (v4). _apply_060 / main.py
# callers keep working unchanged.
def build_report(kind: str = "on-demand") -> str:
    return build_report_v4(kind=kind)


# ═══════════════════════ storage + Telegram hook ═══════════════════════════

def store_report(body: str, kind: str) -> None:
    import db_pg
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("INSERT INTO report_rows (kind, body) VALUES (%s,%s)",
                        (kind, body))
            c.commit()
    except Exception as e:
        log.warning("report store failed: %s", e)


def store_eod() -> str:
    body = build_report_v4(kind="eod")
    store_report(body, "eod")
    return body


def handle_report_command(text: str):
    """Telegram hook — owns REPORT* and BOOK FULL. None to decline.
    REPORT          v4 compact (Telegram mode — tgt/src only when explicit
                    or OVER)
    REPORT FULL     compact + unfiltered ⚡DIV
    REPORT UPLOAD   verbose mode as a .txt document (full tgt/src, full DIV,
                    position table appended) — for pasting into an LLM
    REPORT LEGACY   v3 renderer (parallel-run week; no snapshot write)
    BOOK FULL       the per-account position table as a .txt document
    Document replies are dicts {document_name, document_text, caption} —
    telegram_handler sends them via sendDocument."""
    if not text:
        return None
    up = text.strip().upper()
    try:
        if up == "BOOK FULL":
            import db_pg
            from tools.book_direction import book_sides
            from tools.position_targets import compute_fills
            with db_pg.get_conn() as conn, conn.cursor() as cur:
                fills = compute_fills(cur, book_sides())
            return {"document_name": f"book_{date.today()}.txt",
                    "document_text": build_book_table(fills),
                    "caption": "📗 full position table (per account)"}
        if not up.startswith(SENTINEL):
            return None
        arg = up[len(SENTINEL):].strip()
        if arg == "NOW":
            from tools.report_now import handle_report_now
            return handle_report_now()
        if arg == "LEGACY":
            body = build_report_legacy(kind="on-demand")
            store_report(body, "on-demand-legacy")
            return body
        if arg == "UPLOAD":
            body = build_report_v4(kind="upload", verbose=True,
                                   persist_snapshot=False)
            store_report(body, "upload")
            return {"document_name": f"report_{date.today()}.txt",
                    "document_text": body,
                    "caption": "REPORT v4 upload mode — full facts"}
        if arg in ("", "FULL"):
            body = build_report_v4(kind="on-demand", full=(arg == "FULL"))
            store_report(body, "on-demand")
            return body
        return (f"🛑 REPORT: unknown variant {arg!r} — use REPORT · REPORT "
                f"NOW · REPORT FULL · REPORT UPLOAD · REPORT LEGACY · "
                f"BOOK FULL")
    except Exception as e:
        log.exception("REPORT failed")
        return f"🛑 REPORT error: {e}"
