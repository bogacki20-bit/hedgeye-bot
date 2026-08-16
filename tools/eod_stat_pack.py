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
  * QUAD vs TAPE — rank-correlates realized returns against each Quad's
    expected-return ordering, so the pack can say when its own numbers
    contradict its own header (tools/quad_tape.py)
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
import re
import urllib.error
from datetime import date

from tools import quad_tape

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
             ("20y+ UST", "TLT"), ("Oil", "USO"), ("Gold", "GLD"),
             ("Copper", "CPER"), ("HY", "HYG"), ("Bitcoin", "BTC-USD")]
# H1: TLT is the 20y+ Treasury ETF. It was labelled "20y UST" as a row and
# "10y" as an anchor IN THE SAME PACK, so the correlation monitor appeared to
# carry two different instruments that were one series. 20y+ is the accurate
# one and is now used in both places.
CORR_ANCHORS = [("USD", "UUP"), ("SPX", "SPY"), ("20y+ UST", "TLT"),
                ("Oil", "USO")]
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


def _ptd_return(closes, dates, same_period) -> float | None:
    """Period-to-date from the last close BEFORE the current period started.
    `same_period(d, ref)` says whether d is in the same period as the newest bar.
    Date-driven for the same reason YTD is: a fixed day count is wrong at every
    period boundary, and most wrong on the days you care about."""
    if not closes or not dates or len(closes) != len(dates):
        return None
    ref = dates[-1]
    base = None
    for c, d in zip(closes, dates):
        if not same_period(d, ref) and c:
            base = c
        elif same_period(d, ref):
            break
    if not base:
        return None
    return closes[-1] / base - 1.0


def mtd_return(closes, dates) -> float | None:
    return _ptd_return(closes, dates,
                       lambda d, r: (d.year, d.month) >= (r.year, r.month))


def qtd_return(closes, dates) -> float | None:
    q = lambda d: (d.year, (d.month - 1) // 3)          # noqa: E731
    return _ptd_return(closes, dates, lambda d, r: q(d) >= q(r))


# Windows QUAD vs TAPE scores. MTD is the one that tests the MONTHLY Quad and
# QTD the quarterly, so the two horizons Hedgeye actually publishes each get a
# row instead of being approximated by a rolling day count.
QUAD_TAPE_WINDOWS = [
    ("1W",  lambda c, d: pct_return(c, 5)),
    ("1M",  lambda c, d: pct_return(c, 21)),
    ("MTD", lambda c, d: mtd_return(c, d)),
    ("QTD", lambda c, d: qtd_return(c, d)),
]


def sector_row(closes, dates) -> dict:
    """Hedgeye's sector table windows: 1-Day, MTD, QTD, YTD (deck p38/p39).
    Deliberately NOT the factor-board windows — the two pages measure
    different things and matching Hedgeye matters more than internal symmetry."""
    return {"price": closes[-1] if closes else None,
            "1D": pct_return(closes, 1),
            "MTD": mtd_return(closes, dates),
            "QTD": qtd_return(closes, dates),
            "YTD": ytd_return(closes, dates)}


def rolling_corr_stats(a_closes, b_closes, window=30, lookback=252) -> dict:
    """52-week summary of a rolling `window`-day correlation (deck p42's right
    panel): {high, low, pct_pos, pct_neg, n}. A single 30D reading tells you
    where correlation is; this tells you whether that is normal."""
    ra, rb = daily_returns(a_closes), daily_returns(b_closes)
    n = min(len(ra), len(rb))
    if n < window + 5:
        return {}
    ra, rb = ra[-n:], rb[-n:]
    series = []
    for end in range(window, n + 1):
        c = pearson(ra[end - window:end], rb[end - window:end])
        if c is not None:
            series.append(c)
    series = series[-lookback:]
    if not series:
        return {}
    pos = sum(1 for c in series if c > 0)
    return {"high": max(series), "low": min(series),
            "pct_pos": pos / len(series), "pct_neg": 1 - pos / len(series),
            "n": len(series)}


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


def _now_et() -> str:
    """Wall clock in ET, for the as-of stamp. Falls back to naive local time
    rather than failing the whole pack over a missing tzdata."""
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime(
            "%Y-%m-%d %H:%M ET")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M (local)")


def _header() -> tuple[list, str | None, bool]:
    """Quad + VIX, from the same doctrine REPORT and WEEKEND use.

    Returns (lines, monthly_quad). The Quad is handed back rather than only
    printed because QUAD vs TAPE has to score against the SAME value the header
    claims — re-reading it separately would let the two disagree.
    """
    lines = [f"EOD STAT PACK — {date.today()}"]
    # FAIL CLOSED. These start at "no Quad, and treat it as unconfirmed", and
    # are only relaxed on the success path at the bottom of the try.
    #
    # The previous version initialised stale=False and assigned mq mid-try, so
    # any exception AFTER the _quad_for call — a dropped connection on cursor
    # teardown, a bad timestamp — left a real Quad paired with stale=False. The
    # header printed "QUAD: unavailable" and the next section printed CONFIRM
    # against that Quad. That is the same class of failure as the bug this
    # whole guard exists to fix, reintroduced by the guard's own error path.
    mq, stale = None, True
    try:
        import db_pg
        from tools.ps_flow import _quad_for
        from tools.quad_regime import quad_staleness, today_market
        today = today_market()
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            _mq, qq = _quad_for(cur, today)
            cur.execute("SELECT max(effective_at) FROM quad_regime_history")
            conf = cur.fetchone()[0]
        st = quad_staleness(conf, today)
        lines.append(f"QUAD: monthly={_mq or '?'} quarterly={qq or '?'} "
                     f"(last confirm {st['confirmed_on'] or 'NONE'})")
        mq, stale = _mq, st["monthly_stale"]
        if stale or st["quarterly_stale"]:
            # Carried forward UNCHANGED and flagged. Not re-derived, not
            # defaulted — the 8/2 header was wrong because July's monthly Quad
            # silently became August's, and the cure for that is saying so, not
            # guessing a replacement.
            axes = ("monthly and quarterly" if st["quarterly_stale"]
                    else "monthly")
            lines.append(f"  ⚠ STALE — carried forward unchanged from "
                         f"{st['confirmed_on'] or 'an unknown date'}. The "
                         f"{axes} Quad has not been")
            lines.append(f"    confirmed for this "
                         f"{'quarter' if st['quarterly_stale'] else 'month'}. "
                         f"Set it with the QUAD: command before trading off "
                         f"this pack.")
    except Exception as e:
        lines.append(f"QUAD: unavailable ({e})")
        mq, stale = None, True
    try:
        from tools.vol_regime import regime_line
        lines.append(regime_line())
    except Exception as e:
        lines.append(f"VOL: unavailable ({e})")
    return lines, mq, stale


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
# FRED keys are exactly 32 lowercase alphanumerics. Anything else 400s on EVERY
# series, which reads as "the whole section is broken" rather than "one variable
# has the wrong value in it".
FRED_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_FRED_ERRORS: list = []


def key_shape_problem(key) -> str | None:
    """Pure. Why this key cannot work, or None. Checked BEFORE the network call
    so a malformed variable is named rather than producing four opaque 400s."""
    if not key:
        return "empty"
    k = key.strip()
    if k != key:
        return "has leading/trailing whitespace — strip it in the Railway var"
    if (k.startswith(("\"", "'")) and k.endswith(("\"", "'"))):
        return "is wrapped in quotes — Railway stores the quotes as part of the value"
    if k.startswith("http"):
        return "looks like a URL, not a key"
    if len(k) != 32:
        return f"is {len(k)} characters, not 32"
    if not FRED_KEY_RE.match(k):
        if FRED_KEY_RE.match(k.lower()):
            return "is uppercase — FRED requires lower-case"
        return "contains non-alphanumeric characters"
    return None


def _fred_key():
    """(key, env_name, was_padded). STRIPS the value.

    2026-08-02: FRED_API_KEY on Railway was " aca4…71cb " — 34 chars, one space
    each side. Every series 400'd. Copying the value out of the Railway UI
    reproduces it clean, so the padding is invisible at exactly the moment you
    go looking for it.

    Being strict about that buys nothing: a leading space is never intentional,
    and no legitimate key is distinguished by its whitespace. So accept it and
    SAY SO — the operator still needs to fix the variable, but not at the cost
    of the section being dark until they do."""
    for n in FRED_ENV_NAMES:
        v = os.environ.get(n)
        if v and v.strip():
            return v.strip(), n, (v != v.strip())
    return None, None, False


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
    except urllib.error.HTTPError as e:
        # FRED puts the ACTUAL reason in the response body — "api_key is not a
        # 32 character alpha-numeric lower-case string", "Bad Request. The
        # series does not exist", etc. Discarding it turns a named, fixable
        # cause into a bare 400 (2026-08-02: four-for-four 400s and no reason).
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        detail = ""
        try:
            detail = _json.loads(body).get("error_message") or ""
        except Exception:
            detail = body
        log.warning("fred %s: http %s — %s", series_id, e.code, detail or "(no body)")
        _FRED_ERRORS.append(f"{series_id}: http {e.code} — {detail or 'no detail'}")
        return []
    except Exception as e:
        log.warning("fred %s failed: %s", series_id, e)
        _FRED_ERRORS.append(f"{series_id}: {type(e).__name__}: {e}")
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
    key, name, padded = _fred_key()
    if not key:
        return ("RATES + CREDIT: n/a (no FRED key found — looked for "
                + ", ".join(FRED_ENV_NAMES) + ".\n"
                "  Operator says one IS set on Railway; if so it is under a "
                "different name — tell me which and it is a one-line change.)")
    problem = key_shape_problem(key)
    if problem:
        return (f"RATES + CREDIT: n/a — ${name} {problem}.\n"
                f"  FRED keys are 32 lower-case alphanumerics. Fix the Railway "
                f"variable and this section fills in with no code change.")
    _FRED_ERRORS.clear()
    try:
        fetched = {sid: fred_series(sid, key) for _, sid, _ in FRED_SERIES}
        rows = [(label, level_changes(fetched.get(sid) or []))
                for label, sid, _ in FRED_SERIES]
        curve = curve_2_10(fetched.get("DGS2") or [], fetched.get("DGS10") or [])
        empty = [label for label, d in rows if not d]
        block = format_rates_credit(rows, curve)
        if empty:
            block += f"\n  ⚠ no observations returned for: {', '.join(empty)}"
        for e in _FRED_ERRORS[:4]:
            block += f"\n    {e}"
        note = ""
        if padded:
            note = (f"\n  ⚠ ${name} has leading/trailing whitespace — stripped "
                    f"here so this works, but fix the Railway variable: it will "
                    f"break anything else that reads it.")
        return block + f"\n  (FRED key from ${name}, {len(key)} chars){note}"
    except Exception as e:
        return f"RATES + CREDIT: unavailable ({e})"


def build_eod_pack() -> str:
    """Assemble the pack. Every section guarded; a failure prints in place."""
    parts, header_quad, quad_stale = _header()

    need = {BENCH}
    for _, lng, sht, _ in FACTORS:
        need |= {lng, sht}
    need |= set(SECTORS)
    need |= {s for _, s in CORR_ROWS} | {s for _, s in CORR_ANCHORS}
    need |= set(quad_tape.doctrine_tickers())
    bars = _fetch_bars(sorted(need))
    got = sum(1 for s in need if bars.get(s, {}).get("closes"))
    # H2: as-of stamp. The LAST BAR DATE, not the clock — a pack built at 09:00
    # Monday off Friday's closes is not stale by the clock and is stale by three
    # days of tape. Both are printed so the gap between them is visible.
    last_bar = max((b["dates"][-1] for b in bars.values()
                    if b.get("dates")), default=None)
    parts.append(f"(price data: {got}/{len(need)} symbols · last bar "
                 f"{last_bar or 'n/a'} · built {_now_et()})")
    # Book age sits next to the tape as-of stamp deliberately: the pack carries
    # TWO independent staleness axes (price bars and the broker book) and one
    # being current says nothing about the other.
    try:
        from tools.book_freshness import book_banner
        parts.append(book_banner())
    except Exception as e:
        parts.append(f"!! BOOK AGE UNKNOWN ({e}) — position-derived figures "
                     f"in this pack are unverified.")

    def _rets(sym):
        b = bars.get(sym) or {}
        return returns_row(b.get("closes"), b.get("dates"))

    parts.append("")
    parts.append(quad_tape.quad_tape_block(bars, header_quad,
                                           QUAD_TAPE_WINDOWS,
                                           stale=quad_stale))

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
