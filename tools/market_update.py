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
from zoneinfo import ZoneInfo

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


ET = ZoneInfo("America/New_York")


def _in_session(now: dt.datetime | None = None) -> bool:
    from tools.trading_calendar import is_trading_day
    now = (now or _now_et()).astimezone(ET)
    return (is_trading_day(now.date())
            and dt.time(9, 30) <= now.time() <= dt.time(16, 0))


def _now_et() -> dt.datetime:
    """Wall clock in ET, DST-aware. Railway runs in UTC — the 9/25 header
    printed '06:02 PM ET' for a 2:02 PM ET run."""
    return dt.datetime.now(ET)


def _run_id() -> str:
    return f"mkt-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S%f}"


# MARKET ticket 9/25: an unchanged-to-the-cent price vs the previous MARKET run
# while the name's sector ETF moved >= this = the quote didn't really refresh.
STALE_ETF_MOVE = 0.0010
DEAD_SESSIONS = 3          # identical close over this many sessions = dead feed
# desk 10/5 #6: a name this run's fetch missed is only stale if its stored
# quote is older than this — the 11:35 run collided with the 5-min poller,
# priced 32/478, and flagged every name whose quote was 6 minutes old.
STALE_AGE_MIN = 20
_SECTOR_OF = {name: etf for etf, name in SECTOR_ETF}


def _prev_run_prices(tickers: list[str], run_id: str) -> dict:
    """{ticker: price} from the most recent EARLIER MARKET run."""
    return {t: float(p) for t, p in _rows(
        "SELECT DISTINCT ON (ticker) ticker, price FROM quote_snapshots "
        "WHERE ticker = ANY(%s) AND run_id LIKE 'mkt-%%' AND run_id < %s "
        "  AND price IS NOT NULL ORDER BY ticker, run_id DESC", (tickers, run_id))}


RET_HORIZONS = (("1w", 7), ("1m", 30), ("3m", 91))   # calendar days back


def _returns(data: dict) -> None:
    """Trending returns (operator 10/19, Macro Show 1W/1M/3M table): stamp
    d['ret'] = {'1w': pct, '1m': pct, '3m': pct} on every row that has a
    price, vs the MFR close on/just before each horizon date. mfr_snapshots
    holds one close per name per trading day (history from 2026-05)."""
    tickers = [t for t, d in data.items() if d and d.get("px") is not None]
    if not tickers:
        return
    today = _now_et().date()
    hist: dict = {}
    for t, sd, px in _rows(
            "SELECT ticker, snapshot_date, price FROM mfr_snapshots "
            "WHERE ticker = ANY(%s) AND snapshot_date >= %s AND price IS NOT NULL "
            "ORDER BY ticker, snapshot_date", (tickers, today - dt.timedelta(days=100))):
        hist.setdefault(t, []).append((sd, float(px)))
    for t in tickers:
        h = hist.get(t)
        if not h:
            continue
        now = float(data[t]["px"])
        ret = {}
        for label, days in RET_HORIZONS:
            cut = today - dt.timedelta(days=days)
            base = [p for d, p in h if d <= cut]
            # need a close within 7 days of the horizon, or it isn't that horizon
            near = [d for d, p in h if d <= cut]
            if base and (cut - near[-1]).days <= 7 and base[-1]:
                ret[label] = (now / base[-1] - 1) * 100
        data[t]["ret"] = ret


def _ret_tag(d: dict) -> str:
    r = d.get("ret") or {}
    if not r:
        return ""
    return "  " + " ".join(f"{k}{r[k]:+.1f}" for k, _ in RET_HORIZONS if k in r)


def _ss_roster() -> dict:
    """{'long': [...], 'short': [...]} — current Signal Strength roster."""
    out = {"long": [], "short": []}
    for t, side in _rows("SELECT ticker, side FROM ss_roster_history "
                         "WHERE removed_on IS NULL ORDER BY ticker"):
        out.setdefault(side, []).append(t)
    return out


def _dead_feeds(tickers: list[str]) -> set:
    """Names whose MFR close hasn't changed across DEAD_SESSIONS snapshots."""
    return {t for (t,) in _rows(
        "SELECT ticker FROM (SELECT ticker, price, row_number() OVER "
        "  (PARTITION BY ticker ORDER BY snapshot_date DESC) rn FROM mfr_snapshots "
        "  WHERE ticker = ANY(%s) AND snapshot_date >= CURRENT_DATE - 10) s "
        "WHERE rn <= %s GROUP BY ticker "
        "HAVING count(*) >= %s AND count(DISTINCT round(price, 2)) = 1",
        (tickers, DEAD_SESSIONS, DEAD_SESSIONS))}


def _screen_rows(tickers: list[str], live: bool = True, run_id: str | None = None) -> dict:
    """{ticker: row} for any names MARKET prints.

    Corpus-first: when live, a batched Yahoo refresh for exactly these names
    lands in live_quotes / quote_snapshots BEFORE anything renders; the row is
    then read back from v_screener (price = newer of Yahoo vs MFR) and rp is
    recomputed here in Python from that price against the stored band.
    A failed quote keeps the old price but is marked ⚠stale-px."""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    run_id = run_id or _run_id()
    priced: set = set()
    if live:
        try:
            from tools.quotes import refresh
            priced = set(refresh(tickers, run_id=run_id)["quotes"])
        except Exception:
            priced = set()
    out = {}
    for (t, lo, hi, trend, iv, rv, px, at, src, trd, tnd, basis, sector,
         bsrc, asof) in _rows(
            "SELECT ticker, range_low, range_high, trend_dir, iv, rv, price, price_at, "
            "       price_source, tv_trade, tv_trend, tv_basis, gics_sector, "
            "       band_source, range_asof "
            "FROM v_screener WHERE ticker = ANY(%s)", (tickers,)):
        band = (lo, hi) if lo is not None and hi is not None else None
        rp = None
        if px is not None and band and float(hi) > float(lo):
            rp = (float(px) - float(lo)) / (float(hi) - float(lo))
        out[t] = {"rp": rp, "trend": trend, "iv": iv, "rv": rv, "px": px, "band": band,
                  "at": at, "src": src, "live": t in priced, "sector": sector,
                  "tt": (trd, tnd, basis), "stale": None,
                  "bsrc": bsrc, "asof": asof}
        if live and t not in priced and px is not None:
            age = ((dt.datetime.now(dt.timezone.utc) - at).total_seconds() / 60
                   if at is not None else None)
            if age is not None and age > STALE_AGE_MIN and not _in_session():
                # market shut: a quote from the last session's close is current
                from tools.trading_calendar import last_completed_session
                close = dt.datetime.combine(last_completed_session(), dt.time(15, 55),
                                            tzinfo=ET)
                if at >= close:
                    age = 0
            if age is None or age > STALE_AGE_MIN:
                out[t]["stale"] = ("quote failed" if age is None else
                                   f"quote failed, px {age:.0f}m old")
    if live and out:
        prev = _prev_run_prices(list(out), run_id)
        for t, d in out.items():
            etf = _SECTOR_OF.get(d["sector"])
            e, p = out.get(etf), prev.get(etf)
            if (d["stale"] or d["px"] is None or t not in prev or etf in (None, t)
                    or not e or e["px"] is None or not p):
                continue
            etf_move = abs(float(e["px"]) / p - 1)
            if round(float(d["px"]), 2) == round(prev[t], 2) and etf_move >= STALE_ETF_MOVE:
                d["stale"] = f"unchanged vs last run, {etf} {etf_move * 100:+.2f}%"
        for t in _dead_feeds(list(out)):
            if t not in priced:
                out[t]["stale"] = f"dead feed ({DEAD_SESSIONS}+ sessions unchanged)"
    return out


def _fetch(live: bool = True, run_id: str | None = None) -> dict:
    """{ticker: row} for the ETF/macro universe (see _screen_rows), plus the
    Hedgeye Risk Range composites (VIX, UST10Y) that have no screener row."""
    univ = INDEXES + SECTORS + THEMES + COMMODITIES + MACRO
    out = _screen_rows(univ, live=live, run_id=run_id)
    for t, sg in _sg_snaps(list(out)).items():
        out[t]["sg"] = sg
    # desk 10/5 #6: VIX/UST10Y read Hedgeye's prev_close all day (10:13 and
    # 11:35 printed identical) — price them live (^VIX / ^TNX), band from RR
    live_px: dict = {}
    if live:
        try:
            from tools.quotes import refresh
            live_px = refresh(RR_EXTRAS, run_id=run_id or _run_id())["quotes"]
        except Exception:
            live_px = {}
    for t, trend, lo, hi, px, sd in _rows(
            "SELECT DISTINCT ON (ticker) ticker, trend, buy_trade, sell_trade, "
            "       prev_close, signal_date FROM hedgeye_risk_ranges "
            "WHERE ticker = ANY(%s) AND signal_date >= CURRENT_DATE - 7 "
            "ORDER BY ticker, signal_date DESC", (RR_EXTRAS,)):
        at, src = None, f"RR close {sd:%m/%d}"
        if t in live_px:
            px, at, src = live_px[t]["price"], live_px[t]["quote_at"], "live"
        rp = None
        if lo is not None and hi is not None and px is not None and hi > lo:
            rp = float((float(px) - float(lo)) / (float(hi) - float(lo)))
        out[t] = {"rp": rp, "trend": trend, "iv": None, "rv": None,
                  "band": (lo, hi), "px": px, "at": at, "pxsrc": src}
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
            prov = f" ⚠ intraday capture @{fa.astimezone(ET).strftime('%H:%M')} ET — final prints tomorrow 9:35"
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
    if rp is not None and abs(rp) > 2:
        rp_s += "⚠chk"     # desk 10/5 #2: >1 band-width outside — gap or bad data
    tr = d.get("trend") or "?"
    band = d.get("band")
    px = d.get("px")
    rng = ""
    if px is not None and band:
        # desk 10/5 #1: the canonical band's source + as-of ride every rp
        tag = f"·{d['bsrc']}" if d.get("bsrc") else ""
        tag += f"@{d['asof']:%m/%d}" if d.get("asof") else ""
        rng = f"  {_px(px)} [{_px(band[0])}-{_px(band[1])}]{tag}"
    elif px is not None:
        rng = f"  {_px(px)}"
    # quote provenance (MARKET ticket 9/25): every line carries the price's
    # own time in ET; ⚠stale-px when the quote failed / didn't really move.
    at = d.get("at")
    when = f" @{at.astimezone(ET):%H:%M}" if at is not None else ""
    if at is not None and at.astimezone(ET).date() != _now_et().date():
        when = f" @{at.astimezone(ET):%m/%d %H:%M}"
    stale = " ⚠stale-px" if d.get("stale") else ""
    from tools.tt_levels import tt_tag
    tt = tt_tag(*d["tt"]) if d.get("tt") else ""
    return (f"{_ARROW.get(tr, '·')} {t:<8}{rp_s:<6}{tr[:4]}"
            f"{rng}{when}{stale}{tt}{_ret_tag(d)}{_sg_suffix(d)}"
            f"{_vol_tag(d.get('iv'), d.get('rv'))}")


def _rp_key(rows: dict, t: str, missing: float = 9.0) -> float:
    d = rows.get(t) or {}
    return d["rp"] if d.get("rp") is not None else missing


def _pm_name_rows(names: list[str], live: bool = True, run_id: str | None = None) -> dict:
    """Fresh-priced rows + SG walls for PM names (MARKET FULL / drill-down).
    The sector ETFs ride along in the same quote run so the stale-px check can
    compare each name against its own sector's move."""
    if not names:
        return {}
    out = _screen_rows(list(names) + SECTORS, live=live, run_id=run_id)
    out = {t: d for t, d in out.items() if t in set(names)}
    for t, sg in _sg_snaps(list(out)).items():
        out[t]["sg"] = sg
    return out


def build_market_update(full: bool = False) -> str:
    run_id = _run_id()
    data = _fetch(run_id=run_id)
    if full:
        _returns(data)
    now = _now_et().strftime("%m/%d %I:%M %p")

    add_long, add_short, stretched = [], [], []
    for t in INDEXES + SECTORS + THEMES + COMMODITIES + MACRO:
        d = data.get(t)
        if not d or d.get("rp") is None or not d.get("trend"):
            continue
        if d.get("stale"):          # ticket 9/25: a stale price never makes ZONES
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

    if full:
        # trending returns, ranked — the Macro Show's sector-performance table
        lines.append("")
        lines.append("📈 TRENDING RETURNS (1w · 1m · 3m, ranked by 1m)")
        ranked = [(t, data[t]["ret"]) for t in INDEXES + SECTORS + THEMES + COMMODITIES + MACRO
                  if t in data and data[t].get("ret", {}).get("1m") is not None]
        ranked.sort(key=lambda x: -x[1]["1m"])
        for t, r in ranked:
            cells = "  ".join(f"{k} {r[k]:+6.1f}%" if k in r else f"{k}    n/a"
                              for k, _ in RET_HORIZONS)
            lines.append(f"  {t:<8}{cells}")

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
    ss = _ss_roster() if full else {"long": [], "short": []}
    if full:
        all_names = [t.lstrip("●") for b in buckets.values()
                     for t in b["long"] + b["short"]
                     + b.get("long_bench", []) + b.get("short_bench", [])]
        name_rows = _pm_name_rows(all_names + ss["long"] + ss["short"], run_id=run_id)
        _returns(name_rows)
        # Signal Strength rosters — the whole SS book at once (operator 10/19),
        # each name ranked by range position: longs low-in-range first (add
        # zone), shorts high-in-range first (short-the-bounce zone).
        for side, title, key in (("long", "SIGNAL STRENGTH — LONGS", lambda t: _rp_key(name_rows, t)),
                                 ("short", "SIGNAL STRENGTH — SHORTS", lambda t: -_rp_key(name_rows, t, -9))):
            names = sorted(ss.get(side, []), key=key)
            lines.append("")
            lines.append(f"{title} ({len(names)})")
            for t in names:
                d = name_rows.get(t)
                lines.append("  " + (_line(t, d) if d else f"· {t:<8}(no range data)"))
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
            when = (f"@{d['at'].astimezone(ET):%H:%M}" if d.get("at")
                    else f"({d.get('pxsrc') or 'RR close'})")
            lines.append(f"  {label} {_px(d['px'])} {when} [{_px(lo)}-{_px(hi)}] "
                         f"{rp_s} {d.get('trend') or ''}".rstrip())
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
    run_id = _run_id()
    data = _screen_rows(names + [etf], run_id=run_id) if names else {}
    etf_data = data.get(etf) or _screen_rows([etf], run_id=run_id).get(etf)
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
                xdata = _screen_rows([t for t, _ in extra] + [etf], run_id=run_id)
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
