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


def _f(v, nd=0) -> str:
    return "—" if v is None else f"{float(v):,.{nd}f}"


def build_block(day: dt.date | None = None) -> str:
    from tools.sg_index_levels import latest
    day = day or dt.date.today()
    t, s = _t1a(day), latest("SPX", day)
    if not t and not s:
        return "🧭 T1A vs SG: no data yet."
    lines = []
    stale = []
    if t and t["report_date"] != day:
        stale.append(f"T1A {t['report_date']:%m/%d}")
    if s and s["note_date"] != day:
        stale.append(f"SG {s['note_date']:%m/%d}")
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
    if s:
        lines.append(f"  SG: tilt {_f(s['gamma_tilt'], 2)} · notional ${_f(s['gamma_notional_mm'])}M"
                     f" · CW {_f(s['call_wall'])} · PW {_f(s['put_wall'])} · absγ {_f(s['abs_gamma_strike'])}")
    return "\n".join(lines)
