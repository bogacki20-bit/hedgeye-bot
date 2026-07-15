"""
report_now.py — REPORT NOW: the intraday companion to the EOD REPORT
(operator sprint spec, Sat 2026-07-11).

What it does (facts only, judgment stays with the operator/LLM):
  1. Universe = the FULL MFR database — every ticker whose latest
     mfr_snapshot has a DEFINED signal (range_high > range_low AND a trend
     from the usual stack: Hedgeye RR > BTC Quant > MFR, wrapper-adjusted —
     the same COALESCE SCREEN gates on). NOT just the SS list.
     ("has defined signal strength" = MFR signal defined; names without a
     range/trend are counted, never silently dropped.)
  2. LIVE prices — ONE batched yf.download for the whole universe (the
     0.5s-per-ticker path rate-limits at ~300 names; see yfinance_client
     5/15 note). rp_now = (live - range_low) / (range_high - range_low)
     against the STORED range. Names yfinance misses are counted loud.
  3. Rotation cues from the SAME sector-flow math the EOD report prints
     (rp Δ3d + flow-quality ✓/✗): sectors with Δ3d >= +ROT_DELTA are HOT ->
     surface LONG candidates from them; Δ3d <= -ROT_DELTA are COOLING ->
     surface SHORT candidates from them.
  4. Candidates: hot-sector + trend BULLISH + rp_now <= LONG_RP_MAX (longs);
     cooling-sector + trend BEARISH + rp_now >= SHORT_RP_MIN (shorts,
     IND-only — IRAs are long-only). ALL compliant names surface, even at
     cap — breaches are FLAGGED, never hidden (override = operator call).
  5. Sizing rules in the header (operator doctrine, constants below):
     longs start 100 bps, add 50/100 bps; ETF cap 4% in report (6% actual
     ceiling — flagged); shorts HARD 2% max, 50 bps starter.
     Cap flags computable on HELD names (fills + Fidelity description);
     unheld names carry the header rules.
  6. EDGES: book positions whose LIVE rp sits at a range extreme
     (<= EDGE_LO or >= EDGE_HI) right now.

Output: compact, one Telegram message (<3500 chars target), LLM-friendly.
Stored to report_rows kind='now'. Read-only — zero writes to signal tables.
Telegram: REPORT NOW (routed via tools/report.handle_report_command).
"""
from __future__ import annotations

import logging
from datetime import datetime

log = logging.getLogger("report_now")

# ── operator doctrine constants (sprint notes 7/11) ──
ROT_DELTA = 0.10          # |Δ3d rp| >= this = a rotation cue
LONG_RP_MAX = 0.35        # long candidates: live rp at/below
SHORT_RP_MIN = 0.65       # short candidates: live rp at/above
EDGE_LO, EDGE_HI = 0.15, 0.85
STARTER_BPS = 100
ADD_BPS = "50/100"
ETF_CAP = 4.0             # % of account, report cap
ETF_CEILING = 6.0         # actual ceiling — flag, don't hide
SHORT_HARD = 2.0          # HARD max % of account
SHORT_STARTER_BPS = 50
MAX_PER_SECTION = 10

# Sector-ETF cue symbol -> canonical ticker_tags.gics_sector (screener's
# vocabulary — 'Technology', not 'Information Technology'). Without this
# bridge the cues (XLV) can never match tagged names ('Health Care').
ETF_TO_GICS = {"XLB": "Materials", "XLC": "Communication Services",
               "XLE": "Energy", "XLF": "Financials", "XLI": "Industrials",
               "XLK": "Technology", "XLP": "Consumer Staples",
               "XLRE": "Real Estate", "XLU": "Utilities",
               "XLV": "Health Care", "XLY": "Consumer Discretionary"}


# ═══════════════════════ pure logic (fixture-tested) ════════════════════════

def rotation_cues(flows, delta: float = ROT_DELTA) -> tuple:
    """flows: [(sector, rp_now, d3, mark)] — the EOD SECTOR FLOW rows.
    Returns (hot, cooling): sectors whose Δ3d crosses ±delta, each as
    (sector, d3, mark), sorted by |Δ| descending. None-Δ never cues."""
    hot = sorted([(s, d, m) for s, _rp, d, m in flows
                  if d is not None and d >= delta], key=lambda x: -x[1])
    cool = sorted([(s, d, m) for s, _rp, d, m in flows
                   if d is not None and d <= -delta], key=lambda x: x[1])
    return hot, cool


def live_rp(price, lo, hi):
    """rp of a LIVE price inside the stored range. None when undefined —
    never a guess. NOT clamped: -0.2 / 1.3 are real information (through
    the range edge)."""
    if price is None or lo is None or hi is None or hi <= lo:
        return None
    return (float(price) - float(lo)) / (float(hi) - float(lo))


def pick_candidates(rows, hot, cooling,
                    long_max: float = LONG_RP_MAX,
                    short_min: float = SHORT_RP_MIN) -> tuple:
    """rows: [{ticker, sector, trend_dir, rp_live}] — full universe, tagged
    names only (sector routing needs a tag; untagged are counted upstream).
    Returns (longs, shorts) sorted by rp (asc / desc), each row untouched.
    Longs: hot sector + BULLISH + rp <= long_max.
    Shorts: cooling sector + BEARISH + rp >= short_min."""
    hot_s = {s for s, _, _ in hot}
    cool_s = {s for s, _, _ in cooling}
    longs = sorted([r for r in rows
                    if r.get("sector") in hot_s
                    and r.get("trend_dir") == "BULLISH"
                    and r.get("rp_live") is not None
                    and r["rp_live"] <= long_max],
                   key=lambda r: r["rp_live"])
    shorts = sorted([r for r in rows
                     if r.get("sector") in cool_s
                     and r.get("trend_dir") == "BEARISH"
                     and r.get("rp_live") is not None
                     and r["rp_live"] >= short_min],
                    key=lambda r: -r["rp_live"])
    return longs, shorts


def sizing_flags(side: str, acct_pct, is_fund: bool) -> str:
    """Cap-breach flags for a HELD name (flag, never hide):
    fund long over 4% report-cap '⚠cap4' / over 6% ceiling '⚠ceil6' ·
    short over the HARD 2% '⚠HARD2'. '' when inside all caps or unknown."""
    if acct_pct is None:
        return ""
    if side == "short" and acct_pct > SHORT_HARD:
        return "⚠HARD2"
    if side == "long" and is_fund:
        if acct_pct > ETF_CEILING:
            return "⚠ceil6"
        if acct_pct > ETF_CAP:
            return "⚠cap4"
    return ""


def collapse_share_classes(cands: list, direction: str) -> list:
    """Edit 4: PBR + PBR-A are one company, two lines. Merge same-base
    share classes (X and X-<sfx> both present) into one entry: ticker
    'PBR/-A', rp_live = the direction-extreme (max for shorts, min for
    longs), rp_span = (lo, hi) rendered as a range. ctx_key = base so a
    held base still gets its fill context."""
    groups: dict = {}
    for r in cands:
        groups.setdefault(r["ticker"].split("-")[0], []).append(r)
    out, merged_bases = [], set()
    for r in cands:
        base = r["ticker"].split("-")[0]
        if base in merged_bases:
            continue                        # consumed by an earlier merge
        group = groups[base]
        pair = (len(group) > 1
                and any("-" in g["ticker"] for g in group)
                and any("-" not in g["ticker"] for g in group))
        if not pair:
            out.append(r)                   # incl. A/B-only pairs: untouched
            continue
        merged_bases.add(base)
        rps = [g["rp_live"] for g in group]
        sfx = "/".join("-" + g["ticker"].split("-", 1)[1]
                       for g in group if "-" in g["ticker"])
        merged = dict(next(g for g in group if "-" not in g["ticker"]))
        merged["ticker"] = f"{base}/{sfx}"
        merged["ctx_key"] = base
        merged["rp_live"] = max(rps) if direction == "short" else min(rps)
        merged["rp_span"] = (min(rps), max(rps))
        out.append(merged)
    out.sort(key=lambda x: x["rp_live"], reverse=(direction == "short"))
    return out


def fmt_candidate(r, ctx: dict | None) -> str:
    """'●●BYD 0.14' unheld (tier mark when roster-active/top-idea) ·
    'RH 0.31 held71%,IND' held · 'WM 0.22 held117%,IND⚠cap4' breached-but-
    surfaced · 'PBR/-A 0.75-0.95' merged share classes."""
    span = r.get("rp_span")
    rp_s = (f"{span[0]:.2f}-{span[1]:.2f}" if span and span[0] != span[1]
            else f"{r['rp_live']:.2f}")
    s = f"{r.get('tier', '')}{r['ticker']} {rp_s}"
    if ctx:
        fill = ctx.get("fill")
        fill_s = f"{fill:.0f}%" if fill is not None else "?%"
        is_fund = (ctx.get("tgt_src") or "").startswith(("dflt-core",
                                                         "dflt-fi",
                                                         "dflt-sat"))
        side = "short" if r.get("trend_dir") == "BEARISH" else "long"
        s += (f" held{fill_s},{ctx.get('acct') or '?'}"
              + sizing_flags(side, ctx.get("acct_pct"), is_fund))
    return s


def fmt_grouped(title, cands, ctx_map, cues, cap: int = MAX_PER_SECTION) -> str:
    """Edit 1: candidates grouped BY SECTOR CUE, in cue order, each group
    labeled with its ETF symbol + flow-quality mark — so a ✗ (fade) cue
    stays attached to its names. cues: [(gics, etf, mark)] in cue order."""
    if not cands:
        return f"{title}: none"
    parts, shown = [], 0
    for gics, etf, mark in cues:
        sec = [r for r in cands if r.get("sector") == gics]
        if not sec or shown >= cap:
            continue
        take = sec[:cap - shown]
        inner = " · ".join(
            fmt_candidate(r, ctx_map.get(r.get("ctx_key", r["ticker"])))
            for r in take)
        parts.append(f"{etf}{mark}: {inner}")
        shown += len(take)
    more = f" +{len(cands) - shown} more" if len(cands) > shown else ""
    return f"{title}: " + (" | ".join(parts) + more if parts else "none")


def tier_mark(bucket) -> str:
    """SCREEN's tier vocabulary, marks only where they add signal:
    ●● active · ● top-idea. Bench/untagged unmarked (the '·' bench dot
    would collide with the list separator)."""
    b = bucket or ""
    if b.startswith("active"):
        return "●●"
    if b.startswith("top_idea"):
        return "●"
    return ""


# ═══════════════════════ live prices (one batch) ════════════════════════════

MFR_FALLBACK_MAX = 5      # polite cap on per-name MFR price fallbacks


def batch_live_prices(tickers: list) -> tuple:
    """{ticker: last_price} via ONE yf.download for the whole set (the
    0.5s-per-name path rate-limits at ~300). Names yfinance misses fall
    back to MFR's latestPrice — capped at MFR_FALLBACK_MAX per run so an
    outage can't turn into a 278-call hammering of their read API.
    Returns (prices, missing_names). Never raises."""
    if not tickers:
        return {}, []
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE
    except Exception:
        HEDGEYE_TO_YFINANCE = {}
    sym_of = {t: HEDGEYE_TO_YFINANCE.get(t, t) for t in tickers}
    prices = {}
    try:
        import yfinance as yf
        data = yf.download(list(set(sym_of.values())), period="1d",
                           interval="5m", progress=False, threads=True,
                           group_by="ticker", auto_adjust=False)
        for t, sym in sym_of.items():
            try:
                frame = data[sym] if len(sym_of) > 1 else data
                closes = frame["Close"].dropna()
                if len(closes):
                    prices[t] = float(closes.iloc[-1])
            except Exception:
                continue
    except Exception as e:
        log.warning("REPORT NOW: batch price download failed: %s", e)
    missing = [t for t in tickers if t not in prices]
    if missing and len(missing) <= MFR_FALLBACK_MAX:
        try:
            from mfr_client import fetch_raw
            for t in missing:
                p = (fetch_raw(t) or {}).get("price")
                if p is not None:
                    prices[t] = float(p)
                    log.info("REPORT NOW: %s priced via MFR fallback", t)
        except Exception as e:
            log.warning("REPORT NOW: MFR price fallback failed: %s", e)
        missing = [t for t in tickers if t not in prices]
    return prices, missing


# ═══════════════════════ assembly ═══════════════════════════════════════════

def build_report_now() -> str:
    import db_pg
    from tools.report import _rp_series, _now_and_3d, _range_shape, \
        flow_mark, _SECTORS
    from tools.screener import (_fetch_source_slice, _apply_btcquant_trend,
                                _apply_wrapper_trend)
    from tools.book_direction import book_sides
    from tools.position_targets import compute_fills

    lines = []
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        # universe: latest MFR row per ticker with a DEFINED range
        cur.execute("""
            SELECT DISTINCT ON (ticker) ticker, price, range_low, range_high
            FROM mfr_snapshots
            WHERE range_low IS NOT NULL AND range_high IS NOT NULL
              AND range_high > range_low
            ORDER BY ticker, snapshot_date DESC""")
        ranges = {t: (float(lo), float(hi)) for t, _p, lo, hi in cur.fetchall()}

        # trend stack + sector tags over that universe (same helpers as SCREEN)
        slice_ = _fetch_source_slice(sorted(ranges), None)
        _apply_btcquant_trend(slice_)
        _apply_wrapper_trend(slice_)
        by_t = {r["ticker"]: r for r in slice_}

        # rotation cues — same math the EOD report prints
        ser = _rp_series(cur, list(_SECTORS))
        flows = []
        for s in _SECTORS:
            now_rp, past_rp = _now_and_3d(ser.get(s, []))
            if now_rp is None:
                continue
            d = (now_rp - past_rp) if past_rp is not None else None
            flows.append((s, now_rp, d, flow_mark(d, _range_shape(ser.get(s, [])))))

        sides = book_sides()
        fills = compute_fills(cur, sides)

    hot, cooling = rotation_cues(flows)
    # translate cue ETF symbols -> GICS names for tag matching; header keeps
    # the ETF symbols (operator vocabulary)
    hot_g = [(ETF_TO_GICS.get(s, s), d, m) for s, d, m in hot]
    cool_g = [(ETF_TO_GICS.get(s, s), d, m) for s, d, m in cooling]

    # live prices for names that can matter: tagged w/ defined trend, in a
    # cued sector — plus every held name (EDGES). One batch.
    hot_s = {s for s, _, _ in hot_g}
    cool_s = {s for s, _, _ in cool_g}
    ce = set(fills["cash_equiv"])
    held = {t for t, v in sides.items()
            if v.get("side") in ("long", "short") and t not in ce}
    wanted, untagged_skipped = set(held & set(ranges)), 0
    for t, lohi in ranges.items():
        r = by_t.get(t)
        if not r or not r.get("trend_dir"):
            continue
        sec = r.get("gics_sector")
        if not sec:
            untagged_skipped += 1
            continue
        if sec in hot_s or sec in cool_s:
            wanted.add(t)
    prices, missing = batch_live_prices(sorted(wanted))

    rows = []
    for t in sorted(wanted):
        lo, hi = ranges[t]
        r = by_t.get(t) or {}
        rows.append({"ticker": t, "sector": r.get("gics_sector"),
                     "trend_dir": r.get("trend_dir"),
                     "tier": tier_mark(r.get("hedgeye_bucket_0629")),
                     "rp_live": live_rp(prices.get(t), lo, hi)})
    longs, shorts = pick_candidates(rows, hot_g, cool_g)
    longs = collapse_share_classes(longs, "long")
    shorts = collapse_share_classes(shorts, "short")
    # spec: show ALL compliant candidates even at cap; flags carry breaches.

    ctx = fills["agg"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"REPORT NOW {now} · live px vs stored MFR range · "
                 f"L start {STARTER_BPS}bps add {ADD_BPS} · ETF cap "
                 f"{ETF_CAP:g}%({ETF_CEILING:g}⚠) · S HARD {SHORT_HARD:g}% "
                 f"start {SHORT_STARTER_BPS}bps · ●●=active ●=top-idea")
    lines.append("ROTATION (Δ3d rp): in: "
                 + (" ".join(f"{s}{d:+.2f}{m}" for s, d, m in hot) or "none")
                 + " · out: "
                 + (" ".join(f"{s}{d:+.2f}{m}" for s, d, m in cooling) or "none"))
    # cue lists for grouping: (gics, etf_symbol, mark), cue order preserved
    hot_cues = [(ETF_TO_GICS.get(s, s), s, m) for s, _d, m in hot]
    cool_cues = [(ETF_TO_GICS.get(s, s), s, m) for s, _d, m in cooling]
    lines.append(fmt_grouped(
        f"LONGS (hot sectors · BULL · rp≤{LONG_RP_MAX:g})",
        longs, ctx, hot_cues))
    lines.append(fmt_grouped(
        f"SHORTS (cooling · BEAR · rp≥{SHORT_RP_MIN:g} · IND-only)",
        shorts, ctx, cool_cues))

    # EDGES: held names at live range extremes
    edges = []
    for t in sorted(held & set(ranges)):
        lo, hi = ranges[t]
        rp = live_rp(prices.get(t), lo, hi)
        if rp is None:
            continue
        if rp <= EDGE_LO or rp >= EDGE_HI:
            side = (sides.get(t) or {}).get("side", "?")
            mark = "▼" if rp <= EDGE_LO else "▲"
            c = ctx.get(t, {})
            fill = c.get("fill")
            fill_s = f",{fill:.0f}%" if fill is not None else ""
            edges.append(f"{t}({side[0].upper()}){rp:.2f}{mark}{fill_s}")
    lines.append("EDGES (book at live range extremes): "
                 + (" · ".join(edges[:MAX_PER_SECTION])
                    + (f" +{len(edges) - MAX_PER_SECTION} more"
                       if len(edges) > MAX_PER_SECTION else "")
                    if edges else "none"))

    # loud accounting — nothing disappears silently; few misses get NAMED
    miss_s = (f"no-live-price: {' '.join(missing)}" if missing and
              len(missing) <= 5 else f"{len(missing)} no-live-price")
    lines.append(f"coverage: {len(wanted)} priced-universe · {miss_s} · "
                 f"{untagged_skipped} untagged (no sector tag — invisible "
                 f"to rotation cues)")
    try:
        from tools.relative_strength import render_report_block
        lines.append(render_report_block())
    except Exception as e:
        lines.append(f"RS/GRID: unavailable ({e})")
    return "\n".join(lines)


def handle_report_now() -> str:
    """Called from tools/report.handle_report_command on 'REPORT NOW'."""
    try:
        body = build_report_now()
        from tools.report import store_report
        store_report(body, "now")
        return body
    except Exception as e:
        log.exception("REPORT NOW failed")
        return f"🛑 REPORT NOW error: {e}"
