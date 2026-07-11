"""
report.py — the REPORT command (build queue item 4, v1).

Dense structured FACTS, no narrative — output designed to be pasted into an
LLM for capital-allocation reasoning. The receiving model reasons; this bot
states. Machine-readable regime header first. Assembled entirely from
already-computed layers: quad history, vol_regime_daily, mfr_snapshots,
ss_flow_events, book_direction, v_screener divergences, alert ledgers.

Telegram:  REPORT            (sentinel; stored as kind='on-demand')
Nightly:   store_eod() after the vol-regime write (kind='eod') — the stored
           rows ARE the ML corpus format.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("report")

SENTINEL = "REPORT"

_SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
            "XLU", "XLV", "XLY"]
_MACRO = ["UUP", "TLT", "SHY", "LQD", "HYG"]


def _rp_series(cur, tickers, days=10) -> dict:
    """{ticker: [(date, rp), ...] ascending} computed in Python."""
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


def build_report(kind: str = "on-demand") -> str:
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
            rows = _book_rows()
            longs, shorts, flags = 0, 0, []
            for r in rows:
                side, td = r["side"], r.get("trend_dir") or "?"
                longs += side == "long"
                shorts += side == "short"
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
            lines.append(f"BOOK: {longs}L/{shorts}S · flagged: "
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
    body = build_report(kind="eod")
    store_report(body, "eod")
    return body


def handle_report_command(text: str):
    """Telegram hook — owns messages starting with REPORT. None to decline."""
    if not text or not text.strip().upper().startswith(SENTINEL):
        return None
    try:
        body = build_report(kind="on-demand")
        store_report(body, "on-demand")
        return body
    except Exception as e:
        log.exception("REPORT failed")
        return f"🛑 REPORT error: {e}"
