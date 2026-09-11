"""MARKET — one-glance market snapshot for the operator (Telegram command).

Sections: indexes, all eleven sector SPDRs, commodities/crypto, rates/credit/
dollar — each with range position (rp, 0=range low, 1=range high), trend from
the same gated signal stack the screener uses (v_screener: fresh Hedgeye >
MFR), and an IV-vs-RV vol tag. VIX and UST10Y ride along from the Hedgeye
Risk Range table (they have no MFR listing).

The point is the ZONES block at the top — Keith's discipline applied to the
whole market at once:
    ADD-LONG   trend BULLISH and rp <= 0.35  (buy low in a bullish range)
    ADD-SHORT  trend BEARISH and rp >= 0.65  (short high in a bearish range)
    STRETCHED  rp >= 0.85 or <= 0.05         (range edge — trim/cover zone)

Read-only. Python owns the math; no LLM.

Telegram:  MARKET  (or MARKET UPDATE)
"""

from __future__ import annotations

import datetime as dt

SENTINELS = ("MARKET", "MARKET UPDATE", "MKT")

INDEXES = ["SPY", "QQQ", "IWM"]
SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
           "XLU", "XLV", "XLY"]
THEMES = ["SMH", "DRAM", "XHB", "ITB"]  # semis, memory, homebuilders (operator ask 9/11)
COMMODITIES = ["GLD", "SLV", "CPER", "USO", "UNG", "CORN", "WEAT", "GDX",
               "IBIT", "BITCOIN"]
MACRO = ["TLT", "LQD", "HYG", "UUP"]
RR_EXTRAS = ["VIX", "UST10Y"]          # no MFR listing — Hedgeye RR only

ADD_LONG_RP = 0.35
ADD_SHORT_RP = 0.65
STRETCH_HI = 0.85
STRETCH_LO = 0.05

_ARROW = {"BULLISH": "▲", "BEARISH": "▼", "NEUTRAL": "◆"}


def _rows(sql, args=None):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args or ())
        return cur.fetchall()


def _fetch() -> dict:
    """{ticker: {rp, trend, iv, rv}} for the universe, v_screener first,
    Hedgeye RR fallback for the composites (rp from prev_close in band)."""
    univ = INDEXES + SECTORS + THEMES + COMMODITIES + MACRO
    out = {}
    for t, rp, trend, iv, rv in _rows(
            "SELECT ticker, range_pos, trend_dir, iv, rv FROM v_screener "
            "WHERE ticker = ANY(%s)", (univ,)):
        out[t] = {"rp": float(rp) if rp is not None else None,
                  "trend": trend, "iv": iv, "rv": rv}
    for t, trend, lo, hi, px, sd in _rows(
            "SELECT DISTINCT ON (ticker) ticker, trend, buy_trade, sell_trade, "
            "       prev_close, signal_date FROM hedgeye_risk_ranges "
            "WHERE ticker = ANY(%s) AND signal_date >= CURRENT_DATE - 7 "
            "ORDER BY ticker, signal_date DESC", (RR_EXTRAS,)):
        rp = None
        if lo is not None and hi is not None and px is not None and hi > lo:
            rp = float((px - lo) / (hi - lo))
        out[t] = {"rp": rp, "trend": trend, "iv": None, "rv": None,
                  "band": (lo, hi), "px": px}
    return out


def _vol_tag(iv, rv) -> str:
    if iv is None or rv is None or not rv:
        return ""
    prem = (float(iv) - float(rv)) / float(rv)
    if prem >= 0.25:
        return " vol$"      # options rich vs realized
    if prem <= -0.25:
        return " vol¢"      # options cheap vs realized
    return ""


def _line(t: str, d: dict) -> str:
    rp = d.get("rp")
    rp_s = f"{rp:+.2f}" if rp is not None and rp < 0 else \
           (f"{rp:.2f}" if rp is not None else "  ? ")
    tr = d.get("trend") or "?"
    return f"{_ARROW.get(tr, '·')} {t:<8}{rp_s:<6}{tr[:4]}{_vol_tag(d.get('iv'), d.get('rv'))}"


def build_market_update() -> str:
    data = _fetch()
    now = dt.datetime.now().strftime("%m/%d %I:%M %p")

    add_long, add_short, stretched = [], [], []
    for t in INDEXES + SECTORS + THEMES + COMMODITIES + MACRO:
        d = data.get(t)
        if not d or d.get("rp") is None or not d.get("trend"):
            continue
        rp, tr = d["rp"], d["trend"]
        if tr == "BULLISH" and rp <= ADD_LONG_RP:
            add_long.append(f"{t} {rp:.2f}")
        elif tr == "BEARISH" and rp >= ADD_SHORT_RP:
            add_short.append(f"{t} {rp:.2f}")
        if rp >= STRETCH_HI or rp <= STRETCH_LO:
            stretched.append(f"{t} {rp:.2f} {tr[:4]}")

    lines = [f"📸 MARKET UPDATE — {now} ET",
             "rp: 0=range low · 1=range high (gated stack: fresh Hedgeye > MFR)",
             ""]
    lines.append("🎯 ZONES")
    lines.append("  add-LONG  (bull, low in range):  "
                 + (", ".join(add_long) or "none"))
    lines.append("  add-SHORT (bear, high in range): "
                 + (", ".join(add_short) or "none"))
    if stretched:
        lines.append("  range edge (trim/cover): " + ", ".join(stretched))

    for title, group in (("INDEXES", INDEXES), ("SECTORS", SECTORS),
                         ("THEMES", THEMES),
                         ("COMMODITIES", COMMODITIES),
                         ("RATES/CREDIT/USD", MACRO)):
        lines.append("")
        lines.append(title)
        for t in group:
            if t in data:
                lines.append("  " + _line(t, data[t]))

    vix = data.get("VIX")
    t10 = data.get("UST10Y")
    lines.append("")
    lines.append("VOLATILITY / RATES")
    if vix and vix.get("band"):
        lo, hi = vix["band"]
        rp_s = f"rp {vix['rp']:.2f}" if vix.get("rp") is not None else "rp ?"
        lines.append(f"  VIX {vix.get('px')} in [{lo}-{hi}] {rp_s} "
                     f"{vix.get('trend') or ''}".rstrip())
    if t10 and t10.get("band"):
        lo, hi = t10["band"]
        rp_s = f"rp {t10['rp']:.2f}" if t10.get("rp") is not None else "rp ?"
        lines.append(f"  UST10Y {t10.get('px')} in [{lo}-{hi}] {rp_s} "
                     f"{t10.get('trend') or ''}".rstrip())
    rich = sum(1 for t in SECTORS
               if data.get(t, {}).get("iv") and data.get(t, {}).get("rv")
               and float(data[t]["iv"]) > float(data[t]["rv"]))
    lines.append(f"  sector vol: options richer than realized in {rich}/"
                 f"{len(SECTORS)} sectors (vol$ rich · vol¢ cheap)")
    return "\n".join(lines)


def handle_market_command(text: str):
    """Telegram entry — owns MARKET / MARKET UPDATE / MKT, declines the rest."""
    if not text or text.strip().upper() not in SENTINELS:
        return None
    return build_market_update()


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print(build_market_update())
