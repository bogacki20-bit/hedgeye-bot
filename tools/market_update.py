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

# PM mirror: sector ETF header over each grouping, Hedgeye's active/top-idea
# longs and shorts listed under it (operator ask 9/11 — 'mirror the position
# monitor'). ● marks a top idea. Bench names are left out to keep it readable.
SECTOR_ETF = [("XLB", "Materials"), ("XLC", "Communication Services"),
              ("XLE", "Energy"), ("XLF", "Financials"),
              ("XLI", "Industrials"), ("XLK", "Technology"),
              ("XLP", "Consumer Staples"), ("XLRE", "Real Estate"),
              ("XLU", "Utilities"), ("XLV", "Health Care"),
              ("XLY", "Consumer Discretionary"), ("IBIT", "Digital Assets")]

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


def _live_prices(tickers: list[str], budget_s: float = 20.0) -> dict:
    """{ticker: live_price} via the bot's price-feed dispatcher, fetched in
    parallel under a hard time budget. Anything slow/failed is simply absent —
    the caller keeps the last-sync price. Never raises."""
    out: dict = {}
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        import yfinance_client

        def one(t):
            try:
                d = yfinance_client.fetch_raw(t)
                return t, (d or {}).get("price")
            except Exception:
                return t, None

        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = [ex.submit(one, t) for t in tickers]
            for f in as_completed(futs, timeout=budget_s):
                t, px = f.result()
                if px:
                    out[t] = px
    except Exception:
        pass  # budget exhausted or feed down — snapshot prices still stand
    return out


def _fetch(live: bool = True) -> dict:
    """{ticker: {rp, trend, iv, rv, px, band, live}} for the universe,
    v_screener first, Hedgeye RR fallback for the composites. MFR prices are
    from the DAILY sync — a 4pm MARKET was showing 10am prices (9/12), so
    px is refreshed from the live feed and rp recomputed against the band."""
    univ = INDEXES + SECTORS + THEMES + COMMODITIES + MACRO
    out = {}
    for t, rp, trend, iv, rv, px, lo, hi in _rows(
            "SELECT ticker, range_pos, trend_dir, iv, rv, price, "
            "       range_low, range_high FROM v_screener "
            "WHERE ticker = ANY(%s)", (univ,)):
        out[t] = {"rp": float(rp) if rp is not None else None,
                  "trend": trend, "iv": iv, "rv": rv, "px": px,
                  "band": (lo, hi) if lo is not None and hi is not None else None}
    if live:
        fresh = _live_prices([t for t in out])
        for t, px in fresh.items():
            d = out[t]
            d["px"], d["live"] = px, True
            if d["band"]:
                lo, hi = float(d["band"][0]), float(d["band"][1])
                if hi > lo:
                    d["rp"] = (float(px) - lo) / (hi - lo)
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


def _pm_buckets() -> dict:
    """{gics_sector: {'long': [t…], 'short': [t…]}} from the PM buckets —
    active + top-idea only, ● prefix on top ideas, top ideas listed first."""
    rows = _rows(
        "SELECT ticker, COALESCE(gics_sector,'(untagged)'), hedgeye_bucket_0629 "
        "FROM ticker_tags WHERE hedgeye_bucket_0629 IN "
        "('active_long','active_short','top_idea_long','top_idea_short') "
        "ORDER BY ticker")
    out: dict = {}
    for t, sec, b in rows:
        side = "long" if b.endswith("_long") else "short"
        top = b.startswith("top_idea")
        d = out.setdefault(sec, {"long": [], "short": []})
        d[side].append(("●" + t) if top else t)
    for d in out.values():
        for side in ("long", "short"):
            d[side].sort(key=lambda s: (not s.startswith("●"), s.lstrip("●")))
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


def _px(v) -> str:
    """Compact price: 7,646 · 174.12 · 4.95 — enough precision, no noise."""
    v = float(v)
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:.1f}"
    return f"{v:.2f}"


def _line(t: str, d: dict) -> str:
    rp = d.get("rp")
    rp_s = f"{rp:+.2f}" if rp is not None and rp < 0 else \
           (f"{rp:.2f}" if rp is not None else "  ? ")
    tr = d.get("trend") or "?"
    band = d.get("band")
    px = d.get("px")
    rng = ""
    if px is not None and band:
        rng = f"  {_px(px)} [{_px(band[0])}-{_px(band[1])}]"
    elif px is not None:
        rng = f"  {_px(px)}"
    return (f"{_ARROW.get(tr, '·')} {t:<8}{rp_s:<6}{tr[:4]}"
            f"{rng}{_vol_tag(d.get('iv'), d.get('rv'))}")


def _pm_name_rows(names: list[str]) -> dict:
    """v_screener rows for PM names (MARKET FULL / sector drill-down)."""
    out = {}
    if not names:
        return out
    for t, rp, trend, iv, rv, px, lo, hi in _rows(
            "SELECT ticker, range_pos, trend_dir, iv, rv, price, "
            "       range_low, range_high FROM v_screener "
            "WHERE ticker = ANY(%s)", (names,)):
        out[t] = {"rp": float(rp) if rp is not None else None,
                  "trend": trend, "iv": iv, "rv": rv, "px": px,
                  "band": (lo, hi) if lo is not None and hi is not None else None}
    return out


def build_market_update(full: bool = False) -> str:
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

    lines.append("")
    lines.append("INDEXES")
    for t in INDEXES:
        if t in data:
            lines.append("  " + _line(t, data[t]))

    # ── PM mirror: sector ETF header, Hedgeye longs/shorts under it.
    #    Compact mode lists tickers; FULL mode gives every name its own
    #    ranged line (arrives as several chunked Telegram messages). ──
    buckets = _pm_buckets()
    name_rows = {}
    if full:
        all_names = [t.lstrip("●") for b in buckets.values()
                     for t in b["long"] + b["short"]]
        name_rows = _pm_name_rows(all_names)
    lines.append("")
    lines.append("SECTORS — Hedgeye PM (● top idea)")
    for etf, sector in SECTOR_ETF:
        b = buckets.get(sector)
        if not b and etf not in data:
            continue
        lines.append("")
        lines.append(_line(etf, data[etf]) if etf in data else f"· {etf}")
        if not b:
            continue
        if not full:
            if b["long"]:
                lines.append("  L: " + " ".join(b["long"]))
            if b["short"]:
                lines.append("  S: " + " ".join(b["short"]))
            continue
        for label, side in (("L:", "long"), ("S:", "short")):
            if not b[side]:
                continue
            lines.append(f"  {label}")
            for marked in b[side]:
                t = marked.lstrip("●")
                star = "●" if marked.startswith("●") else " "
                d = name_rows.get(t)
                lines.append("  " + star + (_line(t, d)[2:] if d
                             else f"{t:<8}(no range data)"))

    for title, group in (("THEMES", THEMES), ("COMMODITIES", COMMODITIES),
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
    for label, d in (("VIX", vix), ("UST10Y", t10)):
        if d and d.get("band"):
            lo, hi = d["band"]
            rp_s = f"rp {d['rp']:.2f}" if d.get("rp") is not None else "rp ?"
            lines.append(f"  {label} {_px(d['px'])} [{_px(lo)}-{_px(hi)}] {rp_s} "
                         f"{d.get('trend') or ''}".rstrip())
    rich = sum(1 for t in SECTORS
               if data.get(t, {}).get("iv") and data.get(t, {}).get("rv")
               and float(data[t]["iv"]) > float(data[t]["rv"]))
    lines.append(f"  sector vol: options richer than realized in {rich}/"
                 f"{len(SECTORS)} sectors (vol$ rich · vol¢ cheap)")
    return "\n".join(lines)


def build_sector_detail(etf: str) -> str:
    """MARKET <ETF> drill-down: every PM name in that sector WITH price,
    range, rp and trend. The overview keeps the name lists compact (229
    ranged lines would blow Telegram's 4096); this is where the ranges live."""
    etf = etf.upper()
    sector = dict(SECTOR_ETF).get(etf)
    if sector is None:
        return (f"Unknown sector ETF {etf}. One of: "
                + " ".join(e for e, _ in SECTOR_ETF))
    b = _pm_buckets().get(sector, {"long": [], "short": []})
    names = [t.lstrip("●") for t in b["long"] + b["short"]]
    tops = {t.lstrip("●") for t in b["long"] + b["short"] if t.startswith("●")}
    data = {}
    if names:
        for t, rp, trend, iv, rv, px, lo, hi in _rows(
                "SELECT ticker, range_pos, trend_dir, iv, rv, price, "
                "       range_low, range_high FROM v_screener "
                "WHERE ticker = ANY(%s)", (names,)):
            data[t] = {"rp": float(rp) if rp is not None else None,
                       "trend": trend, "iv": iv, "rv": rv, "px": px,
                       "band": (lo, hi) if lo is not None and hi is not None else None}
    etf_data = _fetch().get(etf)
    lines = [f"📸 {etf} — {sector} (Hedgeye PM, ● top idea)"]
    if etf_data:
        lines.append(_line(etf, etf_data))
    for label, side in (("LONGS", "long"), ("SHORTS", "short")):
        if not b[side]:
            continue
        lines.append("")
        lines.append(label)
        for marked in b[side]:
            t = marked.lstrip("●")
            star = "●" if t in tops and marked.startswith("●") else " "
            d = data.get(t)
            if d:
                lines.append(" " + star + _line(t, d)[2:])
            else:
                lines.append(f" {star} {t:<8}(no range data)")
    return "\n".join(lines)[:4000]


def handle_market_command(text: str):
    """Telegram entry — owns MARKET / MARKET UPDATE / MKT and the sector
    drill-down MARKET <ETF> (e.g. MARKET XLE); declines everything else."""
    if not text:
        return None
    up = text.strip().upper()
    if up in SENTINELS:
        return build_market_update()
    if up in ("MARKET FULL", "MKT FULL"):
        # Delivered as a .txt attachment (the BOOK FULL pattern) — ~11K chars
        # of per-name ranges must never spam the chat as 4 chunked messages.
        full = build_market_update(full=True)
        zones = "\n".join(l for l in full.splitlines()[:8] if l.strip())
        return {"document_name":
                    f"market_full_{dt.date.today().isoformat()}.txt",
                "document_text": full,
                "caption": zones[:1024]}
    parts = up.split()
    if len(parts) == 2 and parts[0] in ("MARKET", "MKT") \
            and parts[1] in dict(SECTOR_ETF):
        return build_sector_detail(parts[1])
    return None


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print(build_market_update())
