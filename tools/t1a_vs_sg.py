"""Tier1 Alpha vs SpotGamma — the daily flip-line comparison block.

Both vendors' morning editions are built off the SAME prior close, so the
T1A row for report_date D pairs with the SG Founder's Note dated D.

    build_block(date) -> text for the desk note / MARKET / Telegram "T1A"
"""
from __future__ import annotations

import datetime as dt


def _t1a(day: dt.date) -> dict | None:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM t1a_snapshots WHERE report_date<=%s "
                    "ORDER BY report_date DESC LIMIT 1", (day,))
        row = cur.fetchone()
        return dict(zip([c.name for c in cur.description], row)) if row else None


from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _expected_note_date(day: dt.date) -> dt.date:
    """SG notes are dated by session; on a weekend/holiday the last
    session's note is current."""
    from tools.trading_calendar import is_trading_day, previous_trading_day
    return day if is_trading_day(day) else previous_trading_day(day)


def _f(v, nd=0) -> str:
    return "—" if v is None else f"{float(v):,.{nd}f}"


def build_block(day: dt.date | None = None) -> str:
    from tools.sg_index_levels import latest
    day = day or dt.date.today()
    t, s = _t1a(day), latest("SPX", day)
    s_dropped = None
    if s and s["note_date"] < _expected_note_date(day):
        s_dropped, s = s, None
    if not t and not s and not s_dropped:
        return "🧭 T1A vs SG: no data yet."
    lines = []
    stale = []
    if t and t["report_date"] != day:
        stale.append(f"T1A {t['report_date']:%m/%d}")
    if s_dropped:
        stale.append(f"SG {s_dropped['note_date']:%m/%d} (dropped)")
    hdr = f"🧭 T1A vs SG — SPX flip lines ({day:%m/%d})"
    lines.append(hdr + (f"  ⚠ stale: {', '.join(stale)}" if stale else ""))
    spot = (t or {}).get("spx_last") or (s or {}).get("ref_price")
    t_flip = (t or {}).get("gex_flip")
    s_zg, s_vt = (s or {}).get("zero_gamma"), (s or {}).get("vol_trigger")
    lines.append(f"  spot {_f(spot)} · T1A flip {_f(t_flip)} · SG zero-γ {_f(s_zg)}"
                 f" · SG vol trigger {_f(s_vt)} · SG pivot {_f((s or {}).get('risk_pivot'))}")
    if t_flip is not None and s_zg is not None:
        gap = float(t_flip) - float(s_zg)
        lines.append(f"  flip gap T1A−SG {gap:+.0f} pts"
                     + (f" · spot {float(spot) - max(float(t_flip), float(s_zg)):+.0f} vs the higher flip"
                        if spot else ""))
    if t:
        lines.append(f"  T1A: γ {t['gamma_exposure'] or '?'} · systematic {t['systematic_flow_risk'] or '?'}"
                     f" · alloc {t['strategic_allocation'] or '?'} · throttle {_f(t['gex_throttle'], 2)}"
                     f" · PV {_f(t['lower_pv_band'])}–{_f(t['upper_pv_band'])}"
                     f" · S/F/R {_f(t['support_strike'])}/{_f(t['focal_strike'])}/{_f(t['resistance_strike'])}")
        vc = t.get("vc_flow_by_move") or {}
        if vc:
            lines.append(f"  vol-control flow $bn: ±1% {vc.get('-1')}/{vc.get('1')}"
                         f" · ±2% {vc.get('-2')}/{vc.get('2')} · ±3% {vc.get('-3')}/{vc.get('3')}"
                         + (f" · sys z {_f(t['sys_positioning_z'], 1)}" if t.get("sys_positioning_z") is not None else ""))
    # desk 10/5 #7: a packet carried the 10/02 note's block (tilt 1.07 / CW
    # 8,100, built off the 10/01 close) beside today's (1.19 / 7,800). The
    # SG line now names its note + capture time, and a prior session's note
    # is dropped rather than printed as if current.
    if s_dropped:
        lines.append(f"  SG: today's Founder's Note not captured yet — "
                     f"{s_dropped['note_date']:%m/%d} note dropped (stale)")
    elif s:
        cap = s.get("parsed_at")
        stamp = f"note {s['note_date']:%m/%d}" + (
            f", captured {cap.astimezone(_ET):%m/%d %H:%M} ET" if cap else "")
        lines.append(f"  SG ({stamp}): tilt {_f(s['gamma_tilt'], 2)} · notional ${_f(s['gamma_notional_mm'])}M"
                     f" · CW {_f(s['call_wall'])} · PW {_f(s['put_wall'])} · absγ {_f(s['abs_gamma_strike'])}")
    if t:
        lines.extend(_t1a_extras(t))
    try:                                   # our exact 1m/3m cross (tools.rv_cross)
        from tools.rv_cross import status_lines
        lines.extend(status_lines())
    except Exception as e:  # noqa: BLE001
        lines.append(f"📉 rVol cross unavailable: {e}")
    return "\n".join(lines)


def full_detail(day: dt.date | None = None) -> list[str]:
    """Everything else in the MSR, for the trading day (desk 9/26: "we want
    the full tier one"). Too long for the 4,000-char desk note: goes in the
    7:00 T1A card (parser_t1a.py --send) and NOTE FULL."""
    day = day or dt.date.today()
    t = _t1a(day)
    if not t:
        return ["📑 T1A detail: no report stored."]
    out = [f"📑 T1A FULL — MSR {t['report_date']:%m/%d} (data {t['data_date'] or '?'} close)"
           + ("" if t["report_date"] == day else f"  ⚠ stale for {day:%m/%d}")]
    out.append(f"SPX: last {_f(t['spx_last'], 2)} · PV {_f(t['lower_pv_band'])}–{_f(t['upper_pv_band'])}"
               f" ({_f(t.get('downside_risk_pct'), 2)}% / +{_f(t.get('upside_risk_pct'), 2)}%)"
               f" · PV band R/R {t.get('pv_band_rr') or '?'} · implied move ±{_f(t.get('implied_move_pct'), 2)}%")
    out.append(f"  gamma: flip {_f(t['gex_flip'])} · throttle {_f(t['gex_throttle'], 2)}"
               f" · GEX/1% ${_f(t.get('gex_per_1pct_bn'))}bn · 10d rVol {_f(t.get('rv_10d'), 2)}")
    for ix in ("ndx", "rut"):
        out.append(f"{ix.upper()}: zero-γ {_f(t.get(f'{ix}_zero_gamma'))} · PV "
                   f"{_f(t.get(f'{ix}_lower_pv'))}–{_f(t.get(f'{ix}_upper_pv'))}")
    sip = t.get("strikes_in_play") or {}
    if sip:
        out.append("strikes in play: " + " · ".join(
            f"{k} {','.join(str(s) for s in v)}" for k, v in sip.items() if v))
    vc = t.get("vc_flow_by_move") or {}
    if vc:
        out.append("vol-control flow by SPX move ($bn): " + " ".join(
            f"{int(k):+d}%:{vc[k]}" for k in sorted(vc, key=int)))
    ts = t.get("term_structure") or []
    if ts:
        out.append("short-dated IV: " + " · ".join(f"{r['expiry'][5:]} {r['iv']:.2f}" for r in ts))
    ev = t.get("event_calendar") or []
    if ev:
        out.append("EVENTS (impact · est/prev · SPX IV · exp move · call/put IV · P/C vol/OI):")
        for e in ev:
            out.append(f"  {e['date'][5:]} {e['event']} · {e.get('impact') or '?'}"
                       f" · {_v(e.get('estimate'))}/{_v(e.get('previous'))}"
                       f" · {_v(e.get('spx_iv'))} · ±{_v(e.get('expected_move_pct'))}%"
                       f" · {_v(e.get('call_iv'))}/{_v(e.get('put_iv'))}"
                       f" · {_v(e.get('pc_vol'))}/{_v(e.get('pc_oi'))}")
    sb = t.get("sector_bands") or []
    if sb:
        out.append("SECTOR PV BANDS (last · lower–upper · up/down % · spread · 1m rVol · 2y β):")
        for r in sb:
            out.append(f"  {r['asset']:<5} {_v(r.get('last'))} · {_v(r.get('lower'))}–{_v(r.get('upper'))}"
                       f" · +{_v(r.get('upside_pct'))}/{_v(r.get('downside_pct'))}"
                       f" · {_v(r.get('spread_pct'))} · {_v(r.get('rvol_1m'))} · {_v(r.get('beta_2y'))}")
    rd = t.get("rv_dropoff") or []
    if rd:
        out.append("rVol drop-off (T+n: 1m date/Δ · 3m date/Δ): " + " · ".join(
            f"T+{r['t']} {r['d1m'][5:]}/{_v(r.get('drop1m'))} {r['d3m'][5:]}/{_v(r.get('drop3m'))}"
            for r in rd))
    if t.get("stocks_up") is not None:
        out.append(f"breadth: {t['stocks_up']} up / {t['stocks_down']} down · SPX {_v(t.get('spx_daily_pct'))}%"
                   f" · avg {_v(t.get('avg_return_pct'))}% (gainers {_v(t.get('avg_gain_pct'))}%,"
                   f" decliners {_v(t.get('avg_decline_pct'))}%) · above 20/50/200d"
                   f" ~{_f(t.get('breadth_20d_pct'))}/{_f(t.get('breadth_50d_pct'))}/{_f(t.get('breadth_200d_pct'))}%")
    il = t.get("impact_leaders") or []
    for side, label in (("pos", "lifted SPX"), ("neg", "dragged SPX")):
        rows = [r for r in il if r.get("side") == side]
        if rows:
            out.append(f"{label}: " + ", ".join(
                f"{r['ticker']} {_v(r.get('ret_pct'))}% ({_v(r.get('impact_pct'))}% of move)" for r in rows))
    ap = (t.get("raw") or {}).get("approx_fields")
    if ap:
        # no underscores: Telegram Markdown reads them as italics and 400s
        out.append(f"approx (chart reads): {', '.join(a.replace('_', ' ') for a in ap)}")
    return out


def _v(v) -> str:
    return "—" if v is None else f"{float(v):g}"


def _t1a_extras(t: dict) -> list[str]:
    """Systematic/vol-control, short-dated vol and breadth from the full MSR."""
    out = []
    if t.get("vc_notional_bn") is not None or t.get("cta_position") is not None:
        out.append(f"  systematic: VC notional ~${_f(t.get('vc_notional_bn'))}bn · CTA ~{_f(t.get('cta_position'))}"
                   f" · T1A rVol 1m/3m ~{_f(t.get('rv_1m_t1a'), 1)}/{_f(t.get('rv_3m_t1a'), 1)} (chart reads)")
    ts = t.get("term_structure") or []
    if len(ts) >= 2:
        front, back = ts[0], ts[-1]
        peak = max(ts, key=lambda r: r["iv"])
        out.append(f"  short-dated IV {front['iv']:.2f} ({front['expiry'][5:]}) → {back['iv']:.2f} "
                   f"({back['expiry'][5:]}) · peak {peak['iv']:.2f} on {peak['expiry'][5:]}")
    hi = [e for e in (t.get("event_calendar") or []) if e.get("impact") == "high"]
    if hi:
        nxt = hi[:3]
        out.append("  next high-impact: " + " · ".join(
            f"{e['date'][5:]} {e['event']} ±{_f(e.get('expected_move_pct'), 2)}%" for e in nxt))
    if t.get("stocks_up") is not None:
        out.append(f"  breadth: {t['stocks_up']} up / {t['stocks_down']} down · above 20/50/200d "
                   f"~{_f(t.get('breadth_20d_pct'))}/{_f(t.get('breadth_50d_pct'))}/{_f(t.get('breadth_200d_pct'))}%")
    return out
