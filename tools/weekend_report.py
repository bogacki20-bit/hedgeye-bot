"""WEEKEND / ROTATION command — a personal Macro Monday for the bot's universe.

One Telegram command -> a rotation report prepping the operator for Monday's open:
  1. Regime header      — current Quad + VIX bucket (reused from quad/vix).
  2. Money flowing IN/OUT — tagged sectors ranked by net trend + range position + RS.
  3. Rolling over        — names making consecutive lower DAILY HIGHS (distribution
                           under the tape), cross-checked with RS rolling_over.
  4. Trending leaders    — RS-strong + high Hurst + rising in range (real trend).
  5. Your book vs tape   — held names flagged inside the leader / rolling-over groups.

This module owns the PURE logic (fully unit-tested, no DB); the DB fetches and the
final Telegram string assembly are thin glue on top. Run the tests with:
    python test_weekend.py
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ── pure logic (no DB, no network — unit-tested in test_weekend.py) ──────────

def lower_high_streak(highs) -> int:
    """Count consecutive most-recent sessions that made a LOWER high.

    `highs` is the daily-high series in chronological order (oldest -> newest).
    Walk backward from the newest bar; each bar whose high is STRICTLY below the
    prior bar's high extends the streak. First non-lower high (equal or up) stops
    it. A flat or rising tape returns 0. Fewer than 2 bars returns 0.

    Example: [10, 11, 10.5, 10.2, 9.8] -> 3
      (9.8<10.2, 10.2<10.5, 10.5<11 ... then 11 is NOT < 10, stop).
    """
    hs = [h for h in highs if h is not None]
    if len(hs) < 2:
        return 0
    streak = 0
    for i in range(len(hs) - 1, 0, -1):
        if hs[i] < hs[i - 1]:
            streak += 1
        else:
            break
    return streak


# trailing-return windows in trading sessions (~5/mo≈21/qtr≈63)
_RET_WINDOWS = {"1w": 5, "1m": 21, "3m": 63}


def trailing_returns(closes) -> dict:
    """{'1w','1m','3m'} close-to-close % returns from a close series (oldest→newest).
    A window returns None when the series is too short or the base price is 0.
    This is the 'trending return' — the same 1w/1m/3m read the Macro note quotes."""
    cs = [c for c in closes if c is not None]
    out = {}
    for label, w in _RET_WINDOWS.items():
        out[label] = (cs[-1] / cs[-1 - w] - 1.0) if len(cs) > w and cs[-1 - w] else None
    return out


def vix_bucket(vix) -> str:
    """Hedgeye-style VIX regime bucket (drives the sizing throttle)."""
    if vix is None:
        return "?"
    v = float(vix)
    if v < 9:
        return "Complacent"
    if v < 19:
        return "Investable→full size"
    if v < 27:
        return "Volatile→half size"
    if v < 36:
        return "Reactive→quarter size"
    return "Panic→minimal"


def _pct(x) -> str:
    """A signed percent for a fractional return, or '—' when missing."""
    if x is None:
        return "—"
    return f"{x*100:+.1f}%"


def _norm_sig(s) -> str:
    """Normalize an MFR/Hedgeye signal string to bull/bear/neut."""
    s = (s or "").lower()
    if "bull" in s:
        return "bull"
    if "bear" in s:
        return "bear"
    return "neut"


def detect_flip(current, prior):
    """'bull→bear' etc. when the normalized signal changed vs prior, else None. Pure.
    Used for both trend flips and momentum flips (same shape, different column)."""
    c, p = _norm_sig(current), _norm_sig(prior)
    return f"{p}→{c}" if c != p else None


_SIG_RANK = {"bull": 1, "neut": 0, "bear": -1}


def is_adverse_momentum(side, prior, current) -> bool:
    """True when momentum moved AGAINST a held position — the 'stop adding' trigger.
    Pure. A LONG is hurt when momentum steps DOWN (bull→neut, neut→bear, bull→bear);
    a SHORT is hurt when it steps UP. Any adverse step counts, not only bull↔bear,
    because a long going bull→neutral is already a reason to stop scaling in."""
    p = _SIG_RANK[_norm_sig(prior)]
    c = _SIG_RANK[_norm_sig(current)]
    if c == p:
        return False
    s = (side or "").lower()
    if s == "long":
        return c < p
    if s == "short":
        return c > p
    return False


def select_stop_adding(held_rows) -> list:
    """Held positions whose momentum turned against the position side. Pure.
    Each row: {ticker, side, momo_prev, momo_now, range_pos}. Returns the adverse
    ones with a 'flip' label, sorted by ticker."""
    out = []
    for r in held_rows:
        if is_adverse_momentum(r.get("side"), r.get("momo_prev"), r.get("momo_now")):
            out.append({"ticker": r.get("ticker"), "side": r.get("side") or "?",
                        "flip": f"{_norm_sig(r.get('momo_prev'))}→{_norm_sig(r.get('momo_now'))}",
                        "range_pos": r.get("range_pos")})
    out.sort(key=lambda d: d["ticker"] or "")
    return out


def dist_to_trend(price, lt_low, lt_high):
    """(pct, edge) — % distance from price to the NEARER long-term (TREND) boundary,
    and which line it is ('support' = lt_low, 'resistance' = lt_high). Pure. That
    line is the level price must cross to flip long-term TREND, so a small pct = right
    at the TREND line. Returns (None, None) when data is missing."""
    if price is None or lt_low is None or lt_high is None or price <= 0:
        return (None, None)
    d_low = abs(price - lt_low) / price
    d_high = abs(price - lt_high) / price
    if d_low <= d_high:
        return (round(d_low * 100, 1), "support")
    return (round(d_high * 100, 1), "resistance")


def _rs_dir(slope) -> str:
    """RS-slope sign -> arrow. None/flat -> sideways."""
    if slope is None:
        return "→"
    if slope > 0.01:
        return "↑"
    if slope < -0.01:
        return "↓"
    return "→"


def _ret_dir(ret) -> str:
    """Return-momentum arrow. Universe-wide (RS covers only a handful of names, so
    flow keys off trailing return instead)."""
    if ret is None:
        return "→"
    if ret > 0.005:
        return "↑"
    if ret < -0.005:
        return "↓"
    return "→"


def _flow_verdict(net_trend: int, mom_dir: str) -> str:
    """Sector flow label from net trend (bull-count minus bear-count) and momentum
    direction (sign of the sector's avg trailing return). Accumulation = breadth
    bullish AND price momentum up; distribution = breadth bearish AND momentum down;
    otherwise a soft hold. (Was RS-slope driven, but RS covers <3% of the universe.)"""
    if net_trend > 0 and mom_dir == "↑":
        return "ACCUM"
    if net_trend < 0 and mom_dir == "↓":
        return "DISTRIB"
    return "hold"


# coarse asset-class buckets for names with no GICS sector (ETFs/thematics/futures/
# crypto/FX) — keeps the rotation table to ~16 rows instead of fragmenting into ~45
# single-name Morningstar categories, while preserving the commodity/bond/FX/crypto
# rotation signal (the Quad3 tell).
_NONEQ_CLASS = [
    (("gold", "silver", "copper", "oil", "crude", "gas", "commodit", "metal",
      "agricult", "energy select"), "Commodities"),
    (("bond", "treasur", "credit", "loan", "convertible", "duration", "yield",
      "fixed", "muni", "tips", "inflation-protected", "aggregate"), "Fixed Income"),
    (("currenc", "dollar", "euro", "yen", "pound", "franc", " fx", "single currency"), "Currency"),
    (("crypto", "bitcoin", "ether", "digital asset", "blockchain", "btc"), "Crypto"),
]


def _coarse_sector(gics, subsector, bucket) -> str:
    """GICS sector for equities; a coarse asset class for the non-GICS sleeve. Pure."""
    if gics:
        return gics
    s = (subsector or bucket or "").lower()
    for keys, cls in _NONEQ_CLASS:
        if any(k in s for k in keys):
            return cls
    return "ETF/Thematic"


def _is_hard_flip(flip: str) -> bool:
    """True when a flip is bull↔bear (a real regime change), not a to/from-neutral wobble."""
    a, _, b = (flip or "").partition("→")
    return {a, b} == {"bull", "bear"}


def aggregate_rotation(rows) -> list:
    """Aggregate tagged rows into a per-sector rotation view. Pure.

    Each row: {sector, trend ('bullish'|'bearish'|'neutral'|None), range_pos (0-1|None),
               rs_slope (float|None)}. Returns a list of dicts sorted by net_trend
               desc (money flowing IN at the top, OUT at the bottom):
      {sector, n, net_trend, bull, bear, avg_rp, rs_dir, flow}
    net_trend = #bullish - #bearish across the sector's names.
    """
    def _mean(xs):
        return round(sum(xs) / len(xs), 4) if xs else None

    by_sector: dict = {}
    for r in rows:
        sec = r.get("sector") or "—"
        b = by_sector.setdefault(sec, {"bull": 0, "bear": 0, "neut": 0, "rps": [],
                                       "slopes": [], "1w": [], "1m": [], "3m": []})
        t = (r.get("trend") or "").lower()
        if t == "bullish":
            b["bull"] += 1
        elif t == "bearish":
            b["bear"] += 1
        else:
            b["neut"] += 1                 # neutral / no-trend still counts toward sector size
        if r.get("range_pos") is not None:
            b["rps"].append(float(r["range_pos"]))
        if r.get("rs_slope") is not None:
            b["slopes"].append(float(r["rs_slope"]))
        for w in ("1w", "1m", "3m"):       # sector-average trailing return
            if r.get(w) is not None:
                b[w].append(float(r[w]))
    out = []
    for sec, b in by_sector.items():
        n = b["bull"] + b["bear"] + b["neut"]   # TOTAL names in the sector (breadth denominator)
        avg_rp = round(sum(b["rps"]) / len(b["rps"]), 2) if b["rps"] else None
        avg_slope = (sum(b["slopes"]) / len(b["slopes"])) if b["slopes"] else None
        rs_dir = _rs_dir(avg_slope)
        ret_1m = _mean(b["1m"])
        mom_dir = _ret_dir(ret_1m)                 # universe-wide momentum (drives flow)
        net = b["bull"] - b["bear"]
        out.append({
            "sector": sec, "n": n, "net_trend": net,
            "bull": b["bull"], "bear": b["bear"], "neut": b["neut"],
            "avg_rp": avg_rp, "rs_dir": rs_dir, "mom_dir": mom_dir,
            "flow": _flow_verdict(net, mom_dir),
            "1w": _mean(b["1w"]), "1m": ret_1m, "3m": _mean(b["3m"]),
        })
    out.sort(key=lambda d: (d["net_trend"], d["bull"]), reverse=True)
    return out


def _vstr(a) -> str:
    """Compact 'distance to TREND' cell, e.g. '2.1%s' (2.1% from support) / '4.0%r'."""
    p, edge = a.get("vs_trend"), a.get("trend_edge")
    if p is None or not edge:
        return "—"
    return f"{p:.1f}%{edge[0]}"


def format_asset_table(assets, *, sort_key: str = "1m") -> str:
    """Per-asset trending-return table (the whole universe). Pure.
    assets: [{ticker, sector, trend, range_pos, rs_rank, '1w','1m','3m', lh_streak,
              vs_trend, trend_edge, held}]. Sorted by `sort_key` return descending —
    strongest trend on top, laggards at the bottom."""
    rows = sorted(assets, key=lambda a: (a.get(sort_key) is None, -(a.get(sort_key) or 0.0)))
    L = [f"{'ticker':<9}{'sector':<15}{'tr':>4} {'rp':>5} {'vsTR':>6} "
         f"{'1w':>7} {'1m':>7} {'3m':>7} {'↓hi':>4} own"]
    for a in rows:
        tr = {"bullish": "bull", "bearish": "bear"}.get((a.get("trend") or "").lower(), "neut")
        rp = f"{a['range_pos']:.2f}" if a.get("range_pos") is not None else "  —"
        own = "📗" if a.get("held") else ""
        lh = a.get("lh_streak") or 0
        L.append(f"{(a.get('ticker') or '')[:8]:<9}{(a.get('sector') or '—')[:14]:<15}{tr:>4} "
                 f"{rp:>5} {_vstr(a):>6} {_pct(a.get('1w')):>7} {_pct(a.get('1m')):>7} "
                 f"{_pct(a.get('3m')):>7} {lh:>4} {own}")
    return "\n".join(L)


def format_stop_adding(items) -> str:
    """The action section: held positions where momentum turned against your side.
    Pure. This is the scale-in guard — momentum flipping against a holding means
    hold the size, don't put the next add in."""
    L = [f"═══ ⚠ STOP ADDING — momentum turned against {len(items)} held position(s) ═══"]
    if not items:
        L.append("  none — momentum still with every position you hold")
    else:
        for d in items:
            rp = f"rp {d['range_pos']:.2f}" if d.get("range_pos") is not None else ""
            L.append(f"  {(d['ticker'] or '')[:8]:<8} {(d['side'] or '?'):<5} "
                     f"momentum {d['flip']:<10} {rp:<8} → hold size, don't add")
    return "\n".join(L)


def format_changes(trend_flips, momo_flips, trend_soft: int = 0,
                   momo_soft: int = 0, cap: int = 30) -> str:
    """WHAT-CHANGED section. Pure. Lists only HARD flips (bull↔bear — the real regime
    changes), capped at `cap` with held names first (glue pre-sorts). To/from-neutral
    wobbles are summarized as a count so the section stays readable, not a 365-name dump.
    Each item: {ticker, flip}."""
    def fmt(items, soft):
        if not items:
            base = "none"
        else:
            shown = items[:cap]
            base = "  ".join(f"{d['ticker']} {d['flip']}" for d in shown)
            if len(items) > cap:
                base += f"  (+{len(items) - cap} more)"
        if soft:
            base += f"   [+{soft} to/from neutral]"
        return base
    return ("═══ WHAT CHANGED (vs ~1wk ago) — "
            f"{len(trend_flips)} trend · {len(momo_flips)} momentum sign-flips ═══\n"
            f"TREND bull↔bear:    {fmt(trend_flips, trend_soft)}\n"
            f"MOMENTUM bull↔bear: {fmt(momo_flips, momo_soft)}")


def format_weekend_report(data: dict) -> str:
    """Assemble the full-universe report text (Telegram/attachment). Pure.

    `data` keys:
      regime      : {date, quad, vix, vix_bucket, n_names, n_sectors, pct_bull/bear/neut}
      trend_flips : [{ticker, flip}]  momo_flips: [{ticker, flip}]   (what changed)
      rotation    : aggregate_rotation() output (money-in -> out, w/ avg returns)
      assets      : per-asset rows for the trending-returns table
      rolling     : select_rolling_over() output (distribution watch)
      leaders     : [{ticker, rs_rank, hurst, range_pos}]
      book        : {held_count, with_flow: [str], against: [str]}
    Single-dict signature so new sections don't churn the call site.
    """
    L = []
    r = data.get("regime", {})
    rotation = data.get("rotation", [])
    assets = data.get("assets", [])
    rolling = data.get("rolling", [])
    leaders = data.get("leaders", [])
    book = data.get("book", {})
    L.append(f"📅 WEEKEND ROTATION — {r.get('date','')}  "
             f"(full universe: {r.get('n_names','?')} names · {r.get('n_sectors','?')} sectors)")
    L.append(f"Regime: {r.get('quad','?')} · VIX {r.get('vix','?')} ({r.get('vix_bucket','?')})")
    L.append(f"Breadth: {r.get('pct_bull','?')}% bull · {r.get('pct_bear','?')}% bear · "
             f"{r.get('pct_neut','?')}% neutral")

    L.append("\n" + format_stop_adding(data.get("stop_adding", [])))

    L.append("\n" + format_changes(data.get("trend_flips", []), data.get("momo_flips", []),
                                    data.get("trend_soft", 0), data.get("momo_soft", 0)))

    L.append("\n═══ MONEY FLOWING  (sector · breadth · avg trailing return) ═══")
    L.append(f"{'sector':<18}{'n':>3}{'net':>4} {'b/b':>7} {'rp':>5} {'1w':>7} {'1m':>7} {'3m':>7} mom flow")
    for s in rotation:
        bb = f"{s['bull']}/{s['bear']}"
        rp = f"{s['avg_rp']:.2f}" if s.get("avg_rp") is not None else "  —"
        L.append(f"{(s['sector'] or '—')[:18]:<18}{s['n']:>3}{s['net_trend']:>+4} {bb:>7} {rp:>5} "
                 f"{_pct(s.get('1w')):>7} {_pct(s.get('1m')):>7} {_pct(s.get('3m')):>7} "
                 f"{s.get('mom_dir','→')}  {s['flow']}")

    L.append(f"\n═══ TRENDING RETURNS — every asset (sorted by 1-month) ═══")
    L.append(format_asset_table(assets, sort_key="1m"))

    L.append(f"\n═══ ROLLING OVER — {len(rolling)} names, 3+ lower highs (distribution) ═══")
    if rolling:
        for d in rolling:
            rp = f"{d['range_pos']:.2f}" if d.get('range_pos') is not None else " —"
            own = " 📗" if d.get("held") else ""
            L.append(f"{(d['ticker'] or '')[:8]:<8} {d['lh_streak']}d ↓highs  "
                     f"rp {rp}  RS{d['rs_dir']}{own}")
    else:
        L.append("  none — nothing making a sustained lower-high sequence")

    L.append(f"\n═══ TRENDING LEADERS — RS strong + Hurst>0.6 + rising ═══")
    if leaders:
        for d in leaders:
            rk = f"#{d['rs_rank']}" if d.get('rs_rank') is not None else "—"
            h = f"{d['hurst']:.2f}" if d.get('hurst') is not None else "—"
            rp = f"{d['range_pos']:.2f}" if d.get('range_pos') is not None else "—"
            L.append(f"{(d['ticker'] or '')[:8]:<8} RS{rk:<4} h{h}  rp{rp}↑")
    else:
        L.append("  none clearing the trend + momentum bar")

    L.append(f"\n═══ YOUR BOOK ({book.get('held_count','?')} held) vs the tape ═══")
    wf = book.get("with_flow") or []
    ag = book.get("against") or []
    L.append("With the flow:   " + (", ".join(wf) if wf else "—"))
    L.append("Against / watch: " + (", ".join(ag) if ag else "—"))
    return "\n".join(L)


def select_rolling_over(rows, *, min_streak: int = 2) -> list:
    """Names distributing = making >= min_streak consecutive lower highs. Pure.
    Each row: {ticker, high_series (list), range_pos, rs_slope, held}. Returns
    rows with a computed `lh_streak`, kept when >= min_streak, sorted worst-first."""
    out = []
    for r in rows:
        streak = lower_high_streak(r.get("high_series") or [])
        if streak >= min_streak:
            out.append({
                "ticker": r.get("ticker"), "lh_streak": streak,
                "range_pos": r.get("range_pos"),
                "rs_dir": _rs_dir(r.get("rs_slope")),
                "held": bool(r.get("held")),
            })
    out.sort(key=lambda d: d["lh_streak"], reverse=True)
    return out


# ── data layer (DB + network glue) ──────────────────────────────────────────
# NOTE: needs the live DB (db_pg) and yfinance; can't run in the Cowork sandbox.
# The PURE logic above is unit-tested; this glue is exercised live by Claude Code.

_TREND_FLIP_LOOKBACK_DAYS = 7
_LEADER_RS_CUT = 20          # rank_trend <= this counts as RS-strong
_LEADER_HURST_MIN = 0.60     # Hurst >= this = genuine trend (not chop)


def _fetch_universe_rows():
    """Full enrolled universe from v_screener (WHERE has_range). One dict per ticker."""
    import db_pg
    rows = []
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT ticker, gics_sector, subsector, hedgeye_bucket_0629, "
            "range_pos, trend_dir, momentum_dir, hurst, held "
            "FROM v_screener WHERE has_range = true")
        for tk, gics, sub, bucket, rp, tr, mo, h, held in cur.fetchall():
            rows.append({
                "ticker": tk,
                "sector": _coarse_sector(gics, sub, bucket),
                "trend": (tr or "").lower(),          # BULLISH -> bullish (aggregate lowercases too)
                "momentum": mo,
                "range_pos": float(rp) if rp is not None else None,
                "hurst": float(h) if h is not None else None,
                "held": bool(held),
            })
    return rows


def _fetch_rs_map():
    """{ticker: {rs_slope, rank_trend}} — latest rs_snapshots row per ticker."""
    import db_pg
    out = {}
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT DISTINCT ON (ticker) ticker, rs_slope, rank_trend "
                        "FROM rs_snapshots ORDER BY ticker, snapshot_date DESC")
            for tk, slope, rank in cur.fetchall():
                out[tk] = {"rs_slope": float(slope) if slope is not None else None,
                           "rank_trend": rank}
    except Exception as e:
        log.warning("weekend: RS fetch failed (RS columns blank): %s", e)
    return out


def _fetch_flips():
    """(trend_flips, momo_flips) week-over-week from mfr_snapshots signal history.
    Latest signal per ticker vs the latest signal >= _TREND_FLIP_LOOKBACK_DAYS ago."""
    import db_pg
    now, prev = {}, {}
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT DISTINCT ON (ticker) ticker, trend_signal, momentum_signal "
                        "FROM mfr_snapshots ORDER BY ticker, snapshot_date DESC")
            now = {tk: (tr, mo) for tk, tr, mo in cur.fetchall()}
            cur.execute("SELECT DISTINCT ON (ticker) ticker, trend_signal, momentum_signal "
                        "FROM mfr_snapshots WHERE snapshot_date <= CURRENT_DATE - %s "
                        "ORDER BY ticker, snapshot_date DESC", (_TREND_FLIP_LOOKBACK_DAYS,))
            prev = {tk: (tr, mo) for tk, tr, mo in cur.fetchall()}
    except Exception as e:
        log.warning("weekend: flip history fetch failed: %s", e)
    tflips, mflips = [], []
    for tk, (tr_now, mo_now) in now.items():
        tr_prev, mo_prev = prev.get(tk, (None, None))
        if tk in prev:
            f = detect_flip(tr_now, tr_prev)
            if f:
                tflips.append({"ticker": tk, "flip": f})
            f = detect_flip(mo_now, mo_prev)
            if f:
                mflips.append({"ticker": tk, "flip": f})
    tflips.sort(key=lambda d: d["ticker"])
    mflips.sort(key=lambda d: d["ticker"])
    return tflips, mflips, now, prev


_YF_EXTRA = {   # non-equity symbols missing from price_monitor.HEDGEYE_TO_YFINANCE
    "BTCUSD": "BTC-USD", "BITCOIN": "BTC-USD", "ETHUSD": "ETH-USD", "SOLUSD": "SOL-USD",
    "XRPUSD": "XRP-USD", "AVAXUSD": "AVAX-USD", "RUNEUSD": "RUNE-USD", "TRXUSD": "TRX-USD",
    "EUR": "EURUSD=X", "GBP": "GBPUSD=X", "JPY": "JPY=X", "CAD": "CAD=X", "CHF": "CHF=X",
}


def _yf_symbol(bot_ticker, hedgeye_map):
    """Map a bot ticker to a yfinance symbol. price_monitor map first, then futures
    (CL_F -> CL=F), then the crypto/FX table, else the raw ticker."""
    if bot_ticker in hedgeye_map and hedgeye_map[bot_ticker]:
        return hedgeye_map[bot_ticker]
    if bot_ticker.endswith("_F"):
        return bot_ticker[:-2] + "=F"
    return _YF_EXTRA.get(bot_ticker, bot_ticker)


def _fetch_bars(tickers, lookback_days=75, chunk=120):
    """{bot_ticker: {'closes':[...], 'highs':[...]}} via batched yf.download.
    Maps bot symbols to yfinance via price_monitor.HEDGEYE_TO_YFINANCE. Best-effort."""
    try:
        import yfinance as yf
    except Exception as e:
        log.warning("weekend: yfinance unavailable: %s", e)
        return {}
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE
    except Exception:
        HEDGEYE_TO_YFINANCE = {}
    fwd = {t: _yf_symbol(t, HEDGEYE_TO_YFINANCE) for t in tickers}
    ysyms = list(dict.fromkeys(v for v in fwd.values() if v))
    period = f"{int(lookback_days * 1.6) + 15}d"
    by_ysym = {}
    for i in range(0, len(ysyms), chunk):
        part = ysyms[i:i + chunk]
        try:
            df = yf.download(part, period=period, interval="1d", group_by="ticker",
                             auto_adjust=False, progress=False, threads=True)
        except Exception as e:
            log.warning("weekend: yf.download chunk failed: %s", e)
            continue
        for s in part:
            try:
                sub = df[s] if len(part) > 1 else df
                closes = [float(x) for x in sub["Close"].tolist() if x == x]
                highs = [float(x) for x in sub["High"].tolist() if x == x]
                if closes:
                    by_ysym[s] = {"closes": closes, "highs": highs}
            except Exception:
                continue
    return {t: by_ysym.get(y, {}) for t, y in fwd.items()}


def _fetch_regime(rows):
    """{quad, vix, vix_bucket, n_names, n_sectors, pct_bull/bear/neut} from rows + quad + VIX."""
    quad = "?"
    try:
        from tools.doctrine import current_monthly_quad
        quad = current_monthly_quad() or "?"
    except Exception as e:
        log.warning("weekend: quad read failed: %s", e)
    vix = None
    try:
        import yfinance as yf
        ser = yf.Ticker("^VIX").history(period="7d")["Close"].dropna()
        if len(ser):
            vix = round(float(ser.iloc[-1]), 1)
    except Exception as e:
        log.warning("weekend: VIX read failed: %s", e)
    nb = sum(1 for r in rows if r["trend"] == "bullish")
    nr = sum(1 for r in rows if r["trend"] == "bearish")
    tot = max(len(rows), 1)
    from datetime import date
    return {"date": date.today().strftime("%a %b %d"), "quad": quad, "vix": vix,
            "vix_bucket": vix_bucket(vix), "n_names": len(rows),
            "n_sectors": len({r["sector"] for r in rows}),
            "pct_bull": round(100 * nb / tot), "pct_bear": round(100 * nr / tot),
            "pct_neut": round(100 * (tot - nb - nr) / tot)}


def build_weekend_report():
    """Assemble the full-universe report text, or None if the universe is empty."""
    rows = _fetch_universe_rows()
    if not rows:
        return None
    rs = _fetch_rs_map()
    bars = _fetch_bars([r["ticker"] for r in rows])
    try:
        from tools.screener import _lt_ranges
        lt = _lt_ranges([r["ticker"] for r in rows])   # {ticker: (price, lt_low, lt_high)}
    except Exception as e:
        log.warning("weekend: LT-range fetch failed (vsTREND blank): %s", e)
        lt = {}
    for r in rows:
        rsr = rs.get(r["ticker"], {})
        r["rs_slope"] = rsr.get("rs_slope")
        r["rs_rank"] = rsr.get("rank_trend")
        b = bars.get(r["ticker"], {})
        rets = trailing_returns(b.get("closes", []))
        r["1w"], r["1m"], r["3m"] = rets["1w"], rets["1m"], rets["3m"]
        r["lh_streak"] = lower_high_streak(b.get("highs", []))
        px, ll, lh = lt.get(r["ticker"], (None, None, None))
        r["vs_trend"], r["trend_edge"] = dist_to_trend(px, ll, lh)

    rotation = aggregate_rotation(rows)
    rolling = sorted([{"ticker": r["ticker"], "lh_streak": r["lh_streak"],
                       "range_pos": r["range_pos"], "rs_dir": _rs_dir(r.get("rs_slope")),
                       "held": r["held"]}
                      for r in rows if (r.get("lh_streak") or 0) >= 3],
                     key=lambda d: d["lh_streak"], reverse=True)
    leaders = sorted([r for r in rows
                      if r.get("rs_rank") and r["rs_rank"] <= _LEADER_RS_CUT
                      and (r.get("hurst") or 0) >= _LEADER_HURST_MIN
                      and (r.get("rs_slope") or 0) > 0],
                     key=lambda r: r["rs_rank"])[:15]
    leaders = [{"ticker": r["ticker"], "rs_rank": r["rs_rank"], "hurst": r["hurst"],
                "range_pos": r["range_pos"]} for r in leaders]

    # book overlay: held names with the flow (ACCUM sector, not rolling) vs against
    flow_by_sec = {s["sector"]: s["flow"] for s in rotation}
    with_flow, against = [], []
    for r in rows:
        if not r["held"]:
            continue
        rolling_flag = (r.get("lh_streak") or 0) >= 3 or (r.get("rs_slope") or 0) < 0
        if rolling_flag:
            tag = f" ({r['lh_streak']}d↓hi)" if (r.get("lh_streak") or 0) >= 3 else " (RS↓)"
            against.append(r["ticker"] + tag)
        elif flow_by_sec.get(r["sector"]) == "ACCUM":
            with_flow.append(r["ticker"])
    book = {"held_count": sum(1 for r in rows if r["held"]),
            "with_flow": with_flow[:30], "against": against[:30]}

    # what changed: split into HARD (bull↔bear) vs soft (to/from neutral); list hard
    # only, held names first, and summarize the soft count so it stays readable.
    tflips, mflips, sig_now, sig_prev = _fetch_flips()
    held_set = {r["ticker"] for r in rows if r["held"]}

    def _split(flips):
        hard = [f for f in flips if _is_hard_flip(f["flip"])]
        hard.sort(key=lambda d: (d["ticker"] not in held_set, d["ticker"]))
        return hard, len(flips) - len(hard)

    t_hard, t_soft = _split(tflips)
    m_hard, m_soft = _split(mflips)

    # STOP ADDING: held positions whose momentum turned against the position side.
    sides = {}
    try:
        from tools.book_direction import book_sides
        sides = {k: (v or {}).get("side") for k, v in book_sides().items()}
    except Exception as e:
        log.warning("weekend: book_sides failed (side blank): %s", e)
    held_flips = []
    for r in rows:
        if not r["held"]:
            continue
        n = sig_now.get(r["ticker"])
        p = sig_prev.get(r["ticker"])
        if not n or not p:
            continue
        held_flips.append({"ticker": r["ticker"], "side": sides.get(r["ticker"]),
                           "momo_prev": p[1], "momo_now": n[1], "range_pos": r["range_pos"]})
    stop_adding = select_stop_adding(held_flips)

    return format_weekend_report({
        "regime": _fetch_regime(rows), "stop_adding": stop_adding,
        "trend_flips": t_hard, "momo_flips": m_hard,
        "trend_soft": t_soft, "momo_soft": m_soft,
        "rotation": rotation, "assets": rows, "rolling": rolling,
        "leaders": leaders, "book": book,
    })


def handle_weekend_command(text):
    """Telegram hook — owns WEEKEND / ROTATION. Returns a document reply (big .txt)
    or None to decline. Errors surface as a short string, never a crash."""
    t = (text or "").strip().upper()
    if t not in ("WEEKEND", "ROTATION", "WEEKEND REPORT", "WEEKEND ROTATION"):
        return None
    from datetime import date
    try:
        report = build_weekend_report()
    except Exception as e:
        log.error("weekend report failed: %s", e, exc_info=True)
        return f"🛑 WEEKEND report failed: {e}"
    if not report:
        return "WEEKEND: no universe rows (v_screener empty / has_range all false?)"
    return {"document_name": f"weekend_rotation_{date.today()}.txt",
            "document_text": report,
            "caption": "📅 Weekend rotation — full universe"}
