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
import re

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
    for t, sg in _sg_snaps(list(out)).items():
        out[t]["sg"] = sg
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


def _pm_buckets(include_bench: bool = False) -> dict:
    """{gics_sector: {'long': […], 'short': […], 'long_bench': […],
    'short_bench': […]}} from the PM buckets. Compact MARKET shows active +
    top-idea only (readability); include_bench=True adds the bench tiers —
    MARKET FULL carries the WHOLE monitor (operator ask 9/19: 'there is
    about 450'). ● prefix marks top ideas, listed first."""
    buckets = ["active_long", "active_short", "top_idea_long", "top_idea_short"]
    if include_bench:
        buckets += ["long_bench", "short_bench"]
    rows = _rows(
        "SELECT ticker, COALESCE(gics_sector,'(untagged)'), hedgeye_bucket_0629 "
        "FROM ticker_tags WHERE hedgeye_bucket_0629 = ANY(%s) "
        "ORDER BY ticker", (buckets,))
    out: dict = {}
    for t, sec, b in rows:
        d = out.setdefault(sec, {"long": [], "short": [],
                                 "long_bench": [], "short_bench": []})
        if b in ("long_bench", "short_bench"):
            d[b].append(t)
        else:
            side = "long" if b.endswith("_long") else "short"
            top = b.startswith("top_idea")
            d[side].append(("●" + t) if top else t)
    for d in out.values():
        for side in ("long", "short"):
            d[side].sort(key=lambda s: (not s.startswith("●"), s.lstrip("●")))
    return out


def _sg_snaps(tickers: list[str]) -> dict:
    """{ticker: {cw, pw, hw, pcr, ivr, dpi}} from the daily EquityHub capture
    (spotgamma_snapshots) — dealer walls + options context per asset, fresh
    rows only (<=3 days). Operator ask 9/19: SG options data on every screen
    so the LLM gets the full picture per liquid-options asset."""
    if not tickers:
        return {}
    from tools.walls_table import gate_walls
    rows = _rows(
        "SELECT DISTINCT ON (ticker) ticker, call_wall, put_wall, hedge_wall, "
        "       put_call_oi_ratio, iv_rank, dpi, price "
        "FROM spotgamma_snapshots WHERE ticker = ANY(%s) "
        "  AND snapshot_date >= CURRENT_DATE - 3 "
        "ORDER BY ticker, snapshot_date DESC", (tickers,))
    out = {}
    for t, cw, pw, hw, pcr, ivr, dpi, px in rows:
        cw, hw, pw, note = gate_walls(cw, hw, pw, px)   # 9/20 sanity gate
        out[t] = {"cw": cw, "pw": pw, "hw": hw, "pcr": pcr, "ivr": ivr,
                  "dpi": dpi, "note": note}
    return out


def _tilt_lines() -> list[str]:
    """GAMMA REGIME lines from sg_tilt (indices_capture stores daily).
    tilt > 1 = dealers long gamma -> pinning, fade the range edges;
    tilt < 1 = short gamma -> moves amplify, follow."""
    rows = _rows(
        "SELECT DISTINCT ON (sym) sym, trade_date, gamma_tilt, fetched_at "
        "FROM sg_tilt ORDER BY sym, trade_date DESC")
    if not rows:
        return []
    parts = []
    prov = ""
    for sym, d, gt, fa in sorted(rows):
        # a same-session print is PROVISIONAL — the 9/21 lesson: the 9:35
        # capture stored 1.05 intraday, the final close print was 1.56
        if d == dt.date.today() and fa is not None:
            prov = f" ⚠ intraday capture @{fa.astimezone().strftime('%H:%M')} — final prints tomorrow 9:35"
        gt = float(gt)
        # three-state rule (9/20): the week of 9/14 printed 1.04->0.97->
        # 0.90->0.83->1.04 — a hard 1.00 split flips regime four times on
        # a 0.21 range. Inside the band the dial isn't distinguishing.
        tag = ("PINNED/FADE" if gt > 1.10
               else "FOLLOW" if gt < 0.90 else "MIXED")
        parts.append(f"{sym} {gt:.2f} {tag}")
    return [f"⚖ GAMMA REGIME ({rows[0][1]}{prov}): " + " · ".join(parts),
            "  >1.10 pinned/fade · <0.90 follow · 0.90-1.10 MIXED "
            "(unresolved — size between)"]


def _sg_suffix(d: dict) -> str:
    sg = d.get("sg")
    if not sg:
        return ""
    if sg.get("note") == "walls-inverted":
        return "  ⋄broken-map"
    if sg.get("cw") is None or sg.get("pw") is None:
        return ""
    def g(v):
        v = float(v)
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:g}"
    hw = f"/{g(sg['hw'])}" if sg.get("hw") is not None else ""
    return f"  ⋄{g(sg['cw'])}{hw}/{g(sg['pw'])}"


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
    # quote provenance (desk fix 9/22 #4: names printed identical marks
    # 10:39->15:35 with nothing saying so) — ·snap = last daily-sync price,
    # no live quote landed inside the fetch budget
    snap = "" if d.get("live") else " ·snap"
    return (f"{_ARROW.get(tr, '·')} {t:<8}{rp_s:<6}{tr[:4]}"
            f"{rng}{snap}{_sg_suffix(d)}{_vol_tag(d.get('iv'), d.get('rv'))}")


def _pm_name_rows(names: list[str]) -> dict:
    """v_screener rows + SG walls for PM names (MARKET FULL / drill-down)."""
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
    for t, sg in _sg_snaps(list(out)).items():
        out[t]["sg"] = sg
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
             "⋄ = SG walls call/hedge/put (EquityHub) — reliable on liquid "
             "options (indexes, megacaps); sparse-OI names = noise",
             ""]
    lines.append("🎯 ZONES")
    lines.append("  add-LONG  (bull, low in range):  "
                 + (", ".join(add_long) or "none"))
    lines.append("  add-SHORT (bear, high in range): "
                 + (", ".join(add_short) or "none"))
    if stretched:
        lines.append("  range edge (trim/cover): " + ", ".join(stretched))
    tilt = _tilt_lines()
    if tilt:
        lines.append("")
        lines.extend(tilt)
        # Backtest 9/19 (n=238 rp-low buys): +0.86% fwd5 on tilt<1 days vs
        # -0.21% on tilt>=1 days — zone entries need the regime filter.
        m = re.search(r"SPX (\d+\.\d+)", tilt[0])
        if m:
            gt = float(m.group(1))
            if gt < 0.90:
                hint = "FAVORED today (SPX follow regime: backtest +0.86% fwd5)"
            elif gt > 1.10:
                hint = "half-size today (SPX pinned: backtest -0.21% fwd5)"
            else:
                hint = (f"MIXED regime (tilt {gt:.2f} inside 0.90-1.10) — "
                        f"size between, let the walls decide")
            lines.append("  → zone entries " + hint)

    # energy complex — diesel/CL1/cracks (operator 9/20: the refiner
    # sleeve's driver belongs on every market snapshot)
    try:
        from tools.energy_complex import build_block
        lines.append("")
        lines.extend(build_block())
    except Exception as e:
        lines.append(f"⛽ ENERGY COMPLEX unavailable: {e}")

    lines.append("")
    lines.append("INDEXES")
    for t in INDEXES:
        if t in data:
            lines.append("  " + _line(t, data[t]))

    # ── PM mirror: sector ETF header, Hedgeye longs/shorts under it.
    #    Compact mode lists tickers; FULL mode gives every name its own
    #    ranged line (arrives as several chunked Telegram messages). ──
    buckets = _pm_buckets(include_bench=full)
    name_rows = {}
    if full:
        all_names = [t.lstrip("●") for b in buckets.values()
                     for t in b["long"] + b["short"]
                     + b.get("long_bench", []) + b.get("short_bench", [])]
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
        for label, side in (("L:", "long"), ("S:", "short"),
                            ("L-bench:", "long_bench"), ("S-bench:", "short_bench")):
            if not b.get(side):
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

    # SECTOR PRO overlay (operator 9/20): the sector-analyst rosters beyond
    # the Monday Position Monitor — Retail Pro sided tags on XLY, Keith's
    # Financials list on XLF. Names already shown above are skipped.
    _PRO = {"XLY": ("Retail Pro monitor", "retailpro"),
            "XLF": ("Financials Pro monitor", "finmon")}
    if etf in _PRO:
        label, kind = _PRO[etf]
        try:
            from tools.source_registry import finmon_side, retailpro_side
            fn = retailpro_side if kind == "retailpro" else finmon_side
            shown = set(names)
            extra = []
            for side in ("long", "short"):
                for t in sorted(fn(side) - shown):
                    extra.append((t, side))
            if extra:
                lines.append("")
                lines.append(f"SECTOR PRO — {label} (beyond the monitor)")
                xdata = {}
                for t, rp, trend, iv, rv, px, lo, hi in _rows(
                        "SELECT ticker, range_pos, trend_dir, iv, rv, price, "
                        "range_low, range_high FROM v_screener "
                        "WHERE ticker = ANY(%s)", ([t for t, _ in extra],)):
                    xdata[t] = {"rp": float(rp) if rp is not None else None,
                                "trend": trend, "iv": iv, "rv": rv, "px": px,
                                "band": (lo, hi) if lo is not None and
                                        hi is not None else None}
                for t, side in extra:
                    d = xdata.get(t)
                    tag = "S" if side == "short" else "L"
                    if d:
                        lines.append(f"  {tag} " + _line(t, d)[2:])
                    else:
                        lines.append(f"  {tag} {t:<8}(no range data)")
        except Exception as e:  # noqa: BLE001
            lines.append(f"  (sector-pro overlay unavailable: {e})")
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
