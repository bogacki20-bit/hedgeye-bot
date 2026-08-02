"""eod_stat_pack.py — the daily EOD stat pack (Phase 1).

Modeled on Hedgeye's Macro Show stat pack (HE_TMS_RR_MC), delivered the way
WEEKEND is: aligned monospace text as a Telegram .txt document. Content of the
stat pack, not its colours — +/− signs carry direction (operator, 2026-07-27:
no web page, no hosted route, no colours).

Phase 1 (this file) needs NO new feeds:
  * tradeable factor board — 8 long/short ETF spreads, 1D/1W/1M/3M/6M/YTD
  * correlation monitor — 4 anchors x 9 assets x 15/30/90/120/180D
  * sector performance, absolute and relative to SPY
  * quad + VIX header, from the same doctrine WEEKEND uses
Phase 2 adds the MFR vol complex (VIX/VXN/RVX/VVIX/MOVE/GVZ/OVX, confirmed
ingesting) and the IVOL table. Phase 3 adds CFTC positioning and an FX realized-
vol proxy. Rates/credit (FRED DGS2/DGS10, HY OAS, BBB) slot into Phase 1's
layout the moment FRED_API_KEY exists in the Railway env — the section prints
`n/a (FRED_API_KEY not set)` until then rather than being absent.

Telegram:  EOD          on-demand
Scheduled: runner-side after the close (same shape as the EOW task).

Every section is guarded: a failing section prints its reason in place and the
pack still assembles. Same no-silent-failures doctrine as REPORT.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date

log = logging.getLogger("eod_stat_pack")

SENTINEL = "EOD"

# ── factor board ────────────────────────────────────────────────────────────
# Liquid ETF proxies read as long-short SPREADS. Short-interest and sales/EPS
# growth quartiles are deliberately absent — no clean ETF proxy exists, and a
# made-up one would look identical to a real reading.
FACTORS = [
    ("Beta",     "SPHB", "SPLV", "High Beta − Low Vol"),
    ("Momentum", "SPMO", "SPY",  "Momentum − Market"),
    ("Style",    "SPYG", "SPYV", "Growth − Value"),
    ("Size",     "IWM",  "SPY",  "Small − Large"),
    ("Quality",  "QUAL", "SPY",  "Quality − Market"),
    ("Yield",    "SPYD", "SPY",  "High Div − Market"),
    ("Low Vol",  "USMV", "SPY",  "Defensives − Market"),
    ("Crowding", "RSP",  "SPY",  "Equal-wt − Cap-wt"),
]

SECTORS = ["XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
           "XLP", "XLRE", "XLU", "XLV", "XLY"]
BENCH = "SPY"

# ── correlation monitor ─────────────────────────────────────────────────────
CORR_ROWS = [("SPX", "SPY"), ("Nasdaq", "QQQ"), ("R2000", "IWM"),
             ("20y UST", "TLT"), ("Oil", "USO"), ("Gold", "GLD"),
             ("Copper", "CPER"), ("HY", "HYG"), ("Bitcoin", "BTC-USD")]
CORR_ANCHORS = [("USD", "UUP"), ("SPX", "SPY"), ("10y", "TLT"), ("Oil", "USO")]
CORR_WINDOWS = [15, 30, 90, 120, 180]

# Trading-day windows. YTD is handled separately — it needs dates, not a count.
RET_WINDOWS = [("1D", 1), ("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126)]

LOOKBACK_DAYS = 400          # enough for 180D corr and a full YTD


# ═══════════════════════ pure logic (no I/O, fixture-tested) ════════════════

def pct_return(closes, window) -> float | None:
    """Close-to-close % return over `window` trading days. None when the series
    is too short or the base is zero — never 0.0, which would read as flat."""
    cs = [c for c in (closes or []) if c is not None]
    if len(cs) <= window or not cs[-1 - window]:
        return None
    return cs[-1] / cs[-1 - window] - 1.0


def ytd_return(closes, dates) -> float | None:
    """Return from the last close of LAST year. Uses dates rather than a fixed
    day count, so it stays right in January when 'YTD' is three sessions."""
    if not closes or not dates or len(closes) != len(dates):
        return None
    this_year = dates[-1].year
    base = None
    for c, d in zip(closes, dates):
        if d.year < this_year and c:
            base = c
        elif d.year >= this_year:
            break
    if not base:
        return None
    return closes[-1] / base - 1.0


def returns_row(closes, dates) -> dict:
    """{'1D','1W','1M','3M','6M','YTD'} for one series."""
    out = {lbl: pct_return(closes, w) for lbl, w in RET_WINDOWS}
    out["YTD"] = ytd_return(closes, dates)
    return out


def spread_row(long_r, short_r) -> dict:
    """Long minus short, per window. None if either leg is missing — a spread
    with one leg guessed is worse than a blank."""
    out = {}
    for k in list(dict(RET_WINDOWS)) + ["YTD"]:
        a, b = long_r.get(k), short_r.get(k)
        out[k] = (a - b) if (a is not None and b is not None) else None
    return out


def daily_returns(closes) -> list:
    cs = [c for c in (closes or []) if c is not None]
    return [cs[i] / cs[i - 1] - 1.0
            for i in range(1, len(cs)) if cs[i - 1]]


FLAT_EPS = 1e-12


def pearson(xs, ys) -> float | None:
    """Correlation of two equal-length return series. None when undefined —
    fewer than 3 points, or either series effectively flat.

    'Effectively' matters. A constant-return series (a perfectly smooth
    compounding curve) has float-noise variance around 1e-35, not exactly zero,
    so a `<= 0` check passes it through and the correlation that comes back is
    built entirely from rounding error — a confident ±1.00 signifying nothing.
    The threshold is scale-relative so it works on returns (~1e-2) and on price
    levels alike."""
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if (sxx <= FLAT_EPS * n * max(1.0, mx * mx)
            or syy <= FLAT_EPS * n * max(1.0, my * my)):
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def corr_over(a_closes, b_closes, window) -> float | None:
    """Correlation of daily returns over the last `window` trading days."""
    ra, rb = daily_returns(a_closes), daily_returns(b_closes)
    n = min(len(ra), len(rb), window)
    if n < 3:
        return None
    return pearson(ra[-n:], rb[-n:])


# ═══════════════════════ formatting ═════════════════════════════════════════

def fmt_pct(v, width=7) -> str:
    """Signed percent. '   n/a' when absent — never 0.0, which reads as flat."""
    if v is None:
        return "n/a".rjust(width)
    return f"{v * 100:+.1f}%".rjust(width)


def fmt_corr(v, width=6) -> str:
    if v is None:
        return "n/a".rjust(width)
    return f"{v:+.2f}".rjust(width)


def format_factor_board(rows) -> str:
    """rows: [(name, long, short, reads_as, spread_dict, long_dict, short_dict)].
    Spread headline with each leg's own trailing returns beneath it — operator
    asked for BOTH, because a spread alone hides which leg moved."""
    cols = [lbl for lbl, _ in RET_WINDOWS] + ["YTD"]
    out = ["FACTOR BOARD — long/short spreads (spread = long − short)",
           f"{'factor':<10}{'pair':<12}" + "".join(c.rjust(8) for c in cols)]
    for name, lng, sht, reads, sp, lr, sr in rows:
        out.append(f"{name:<10}{lng + '/' + sht:<12}"
                   + "".join(fmt_pct(sp.get(c), 8) for c in cols)
                   + f"   {reads}")
        out.append(f"{'':<10}{'  ' + lng:<12}"
                   + "".join(fmt_pct(lr.get(c), 8) for c in cols))
        out.append(f"{'':<10}{'  ' + sht:<12}"
                   + "".join(fmt_pct(sr.get(c), 8) for c in cols))
    return "\n".join(out)


def format_sectors(rows) -> str:
    """rows: [(ticker, ret_dict, rel_dict)] — absolute and vs SPY. Sorted by 1M
    relative, money-in first, matching the REPORT sector-flow convention."""
    cols = [lbl for lbl, _ in RET_WINDOWS] + ["YTD"]
    out = [f"SECTORS — absolute, and relative to {BENCH} (sorted by 1M relative)",
           f"{'sector':<8}" + "".join(c.rjust(8) for c in cols)
           + "   |" + "".join(("r" + c).rjust(8) for c in cols)]
    ranked = sorted(rows, key=lambda r: (r[2].get("1M") is None,
                                         -(r[2].get("1M") or 0)))
    for tkr, ab, rel in ranked:
        out.append(f"{tkr:<8}" + "".join(fmt_pct(ab.get(c), 8) for c in cols)
                   + "   |" + "".join(fmt_pct(rel.get(c), 8) for c in cols))
    return "\n".join(out)


def format_correlations(blocks) -> str:
    """blocks: [(anchor_label, anchor_sym, [(row_label, {window: corr})])].
    One block per anchor — 4 anchors x 5 windows in one table would be 20
    columns and unreadable in a phone-width monospace block."""
    out = ["CORRELATION MONITOR — rolling corr of daily returns"]
    for alabel, asym, rows in blocks:
        out.append("")
        out.append(f"  vs {alabel} ({asym})")
        out.append("  " + f"{'asset':<12}"
                   + "".join((f"{w}D").rjust(7) for w in CORR_WINDOWS))
        for rlabel, vals in rows:
            out.append("  " + f"{rlabel:<12}"
                       + "".join(fmt_corr(vals.get(w), 7) for w in CORR_WINDOWS))
    return "\n".join(out)


# ═══════════════════════ data ═══════════════════════════════════════════════

def _fetch_bars(symbols, lookback_days=LOOKBACK_DAYS):
    """{symbol: {'closes': [...], 'dates': [date,...]}} straight from yfinance.

    Deliberately NOT weekend_report._fetch_bars: that one drops the date index,
    and YTD needs real dates or it silently becomes a fixed day count that is
    wrong every January."""
    try:
        import yfinance as yf
    except Exception as e:
        log.warning("eod: yfinance unavailable: %s", e)
        return {}
    period = f"{int(lookback_days * 1.5) + 30}d"
    out = {}
    syms = list(dict.fromkeys(symbols))
    try:
        df = yf.download(syms, period=period, interval="1d", group_by="ticker",
                         auto_adjust=True, progress=False, threads=True)
    except Exception as e:
        log.warning("eod: yf.download failed: %s", e)
        return {}
    for s in syms:
        try:
            sub = df[s] if len(syms) > 1 else df
            pairs = [(i.date() if hasattr(i, "date") else i, float(c))
                     for i, c in zip(sub.index, sub["Close"].tolist()) if c == c]
            if pairs:
                out[s] = {"dates": [p[0] for p in pairs],
                          "closes": [p[1] for p in pairs]}
        except Exception:
            continue
    return out


def _header() -> list:
    """Quad + VIX, from the same doctrine REPORT and WEEKEND use."""
    lines = [f"EOD STAT PACK — {date.today()}"]
    try:
        import db_pg
        from tools.ps_flow import _quad_for
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            mq, qq = _quad_for(cur, date.today())
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
    return lines


# ── rates + credit (FRED) ───────────────────────────────────────────────────
# Operator 2026-08-02: the FRED key is already in the Railway env. Name isn't
# pinned anywhere, so try the plausible ones and SAY which was found — a section
# that silently reads n/a because the variable is called something else is the
# same class of failure as the empty watchlist.
FRED_ENV_NAMES = ("FRED_API_KEY", "FRED_KEY", "FRED_TOKEN", "FRED_API_TOKEN")
FRED_SERIES = [
    ("2y UST",   "DGS2",           "%"),
    ("10y UST",  "DGS10",          "%"),
    ("HY OAS",   "BAMLH0A0HYM2",   "%"),
    ("BBB OAS",  "BAMLC0A4CBBB",   "%"),
]
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def _fred_key():
    for n in FRED_ENV_NAMES:
        v = os.environ.get(n)
        if v:
            return v, n
    return None, None


def fred_series(series_id, key, days=400) -> list:
    """[(date, value)] oldest→newest, missing observations ('.') dropped.
    Returns [] on any failure — the caller prints the gap."""
    import json as _json
    import urllib.parse
    import urllib.request
    from datetime import timedelta
    start = (date.today() - timedelta(days=days)).isoformat()
    q = urllib.parse.urlencode({"series_id": series_id, "api_key": key,
                                "file_type": "json",
                                "observation_start": start})
    try:
        with urllib.request.urlopen(f"{FRED_URL}?{q}", timeout=20) as r:
            data = _json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception as e:
        log.warning("fred %s failed: %s", series_id, e)
        return []
    out = []
    for o in (data.get("observations") or []):
        v = o.get("value")
        if v in (None, "", "."):        # FRED marks holidays/missing as "."
            continue
        try:
            out.append((o.get("date"), float(v)))
        except ValueError:
            continue
    return out


def level_changes(obs) -> dict:
    """{'last','1D','1W','1M'} — LEVEL changes in basis points, not percent
    returns. A yield going 4.10 -> 4.20 is +10bp; calling that '+2.4%' is the
    kind of number that reads fine and means nothing."""
    if not obs:
        return {}
    vals = [v for _, v in obs]
    last = vals[-1]
    out = {"last": last}
    for lbl, n in (("1D", 1), ("1W", 5), ("1M", 21)):
        out[lbl] = (last - vals[-1 - n]) * 100 if len(vals) > n else None
    return out


def format_rates_credit(rows, curve) -> str:
    """rows: [(label, {last,1D,1W,1M})]. Levels in %, changes in bp."""
    out = ["RATES + CREDIT (level %, changes in bp)",
           f"{'series':<10}{'last':>8}{'1D':>8}{'1W':>8}{'1M':>8}"]

    def _bp(v):
        return "n/a".rjust(8) if v is None else f"{v:+.0f}".rjust(8)

    for label, d in rows:
        if not d:
            out.append(f"{label:<10}" + "n/a (no observations)".rjust(8))
            continue
        out.append(f"{label:<10}{d['last']:>8.2f}"
                   + _bp(d.get("1D")) + _bp(d.get("1W")) + _bp(d.get("1M")))
    if curve:
        out.append(f"{'2-10 curve':<10}{curve['last']:>8.2f}"
                   + _bp(curve.get("1D")) + _bp(curve.get("1W"))
                   + _bp(curve.get("1M"))
                   + ("   INVERTED" if curve["last"] < 0 else ""))
    return "\n".join(out)


def curve_2_10(two, ten) -> dict:
    """10y minus 2y, aligned on DATE not position — the two series have
    different holiday gaps, so zipping them by index silently compares
    different days."""
    if not two or not ten:
        return {}
    d2 = dict(two)
    paired = [(d, v - d2[d]) for d, v in ten if d in d2]
    return level_changes(paired)


def _rates_credit_block() -> str:
    """FRED sections. Prints why it is empty rather than vanishing — an absent
    section reads as 'nothing to report', which is a different claim."""
    key, name = _fred_key()
    if not key:
        return ("RATES + CREDIT: n/a (no FRED key found — looked for "
                + ", ".join(FRED_ENV_NAMES) + ".\n"
                "  Operator says one IS set on Railway; if so it is under a "
                "different name — tell me which and it is a one-line change.)")
    try:
        fetched = {sid: fred_series(sid, key) for _, sid, _ in FRED_SERIES}
        rows = [(label, level_changes(fetched.get(sid) or []))
                for label, sid, _ in FRED_SERIES]
        curve = curve_2_10(fetched.get("DGS2") or [], fetched.get("DGS10") or [])
        empty = [label for label, d in rows if not d]
        block = format_rates_credit(rows, curve)
        if empty:
            block += f"\n  ⚠ no observations returned for: {', '.join(empty)}"
        return block + f"\n  (FRED key from ${name})"
    except Exception as e:
        return f"RATES + CREDIT: unavailable ({e})"


def build_eod_pack() -> str:
    """Assemble the pack. Every section guarded; a failure prints in place."""
    parts = _header()

    need = {BENCH}
    for _, lng, sht, _ in FACTORS:
        need |= {lng, sht}
    need |= set(SECTORS)
    need |= {s for _, s in CORR_ROWS} | {s for _, s in CORR_ANCHORS}
    bars = _fetch_bars(sorted(need))
    got = sum(1 for s in need if bars.get(s, {}).get("closes"))
    parts.append(f"(price data: {got}/{len(need)} symbols)")

    def _rets(sym):
        b = bars.get(sym) or {}
        return returns_row(b.get("closes"), b.get("dates"))

    try:
        rows = []
        for name, lng, sht, reads in FACTORS:
            lr, sr = _rets(lng), _rets(sht)
            rows.append((name, lng, sht, reads, spread_row(lr, sr), lr, sr))
        parts.append("")
        parts.append(format_factor_board(rows))
    except Exception as e:
        parts.append(f"\nFACTOR BOARD: unavailable ({e})")

    try:
        bench = _rets(BENCH)
        srows = []
        for s in SECTORS:
            ab = _rets(s)
            rel = {k: (ab[k] - bench[k])
                   if (ab.get(k) is not None and bench.get(k) is not None) else None
                   for k in ab}
            srows.append((s, ab, rel))
        parts.append("")
        parts.append(format_sectors(srows))
    except Exception as e:
        parts.append(f"\nSECTORS: unavailable ({e})")

    try:
        blocks = []
        for alabel, asym in CORR_ANCHORS:
            ac = (bars.get(asym) or {}).get("closes")
            rows = []
            for rlabel, rsym in CORR_ROWS:
                if rsym == asym:
                    continue                      # corr(x,x) = 1, no information
                rc = (bars.get(rsym) or {}).get("closes")
                rows.append((f"{rlabel} ({rsym})",
                             {w: corr_over(ac, rc, w) for w in CORR_WINDOWS}))
            blocks.append((alabel, asym, rows))
        parts.append("")
        parts.append(format_correlations(blocks))
    except Exception as e:
        parts.append(f"\nCORRELATIONS: unavailable ({e})")

    parts.append("")
    parts.append(_rates_credit_block())
    parts.append("VOL COMPLEX (VIX/VXN/RVX/VVIX/MOVE/GVZ/OVX) + IVOL: Phase 2")
    parts.append("CFTC positioning + FX realized-vol proxy: Phase 3")
    return "\n".join(parts)


# ═══════════════════════ Telegram + schedule hooks ══════════════════════════

def handle_eod_command(text):
    """Telegram hook — owns EOD. Returns a document reply, or None to decline.
    Document rather than an inline code block: the pack runs well past
    Telegram's 4096-char message limit, and notifier.send_telegram does not
    chunk (the weekly-backlog lesson)."""
    t = (text or "").strip().upper()
    if t not in ("EOD", "/EOD", "EOD PACK", "STAT PACK"):
        return None
    try:
        body = build_eod_pack()
    except Exception as e:
        log.error("EOD pack failed: %s", e, exc_info=True)
        return f"🛑 EOD stat pack failed: {e}"
    try:
        from tools.report import store_report
        store_report(body, "eod-pack")
    except Exception as e:
        log.warning("eod: store failed: %s", e)
    return {"document_name": f"eod_stat_pack_{date.today()}.txt",
            "document_text": body,
            "caption": "📊 EOD stat pack"}


def run_scheduled() -> str:
    """Runner-side after-the-close entry point. Posts the pack to Telegram as a
    document. Returns a status string."""
    try:
        body = build_eod_pack()
    except Exception as e:
        log.error("EOD pack failed: %s", e, exc_info=True)
        return f"error:{e}"
    try:
        from tools.report import store_report
        store_report(body, "eod-pack")
    except Exception as e:
        log.warning("eod: store failed: %s", e)
    try:
        from notifier import send_telegram
        ok = send_telegram("EOD stat pack",
                           f"📊 EOD stat pack {date.today()} — "
                           f"{len(body):,} chars, send `EOD` for the file.")
        return f"sent:{len(body)}" if ok is not False else "error:send-failed"
    except Exception as e:
        return f"error:{e}"


if __name__ == "__main__":
    print(build_eod_pack())
