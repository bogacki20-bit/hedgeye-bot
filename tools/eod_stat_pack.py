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

# CALENDAR-DAY window definitions. These replaced trading-day ROW OFFSETS on
# 2026-08-16 (see asof_index). 28 days is deliberately "4 weeks", which is also
# Hedgeye's MoM definition -- their slides print the "4 Wks Ago" LEVEL, so the
# bot now compares against the same point in time rather than 21 rows back.
WINDOW_DAYS = [("1D", 1), ("1W", 7), ("1M", 28), ("3M", 91), ("6M", 182)]
CORR_WINDOW_DAYS = {15: 21, 30: 42, 90: 126, 120: 168, 180: 252}


def asof_index(dates, days_back, ref=None) -> int | None:
    """Index of the last bar ON OR BEFORE (ref - days_back calendar days).

    THE FIX for the 2026-08-16 non-determinism. Every window used to be a ROW
    OFFSET -- cs[-1 - 21]. yfinance returns a varying number of rows for the
    same request (measured: SPHB 627 then 630 rows in the same process, no
    market open in between), so -21 landed on a DIFFERENT CALENDAR DATE run to
    run: SPHB's "1M ago" was 2026-07-13 on one build and 2026-07-16 on the next,
    producing 1M returns of +4.05% and +6.07% from identical inputs.

    Indexing by DATE makes the answer independent of how many rows came back. A
    dropped bar shifts nothing: the comparison point is still "the last session
    on or before four weeks ago". Short windows looked stable before only
    because they rarely spanned a gap; the error grew with the window, which is
    exactly what was observed.
    """
    if not dates:
        return None
    ref = ref or dates[-1]
    from datetime import timedelta
    target = ref - timedelta(days=days_back)
    lo, hi, found = 0, len(dates) - 1, None
    while lo <= hi:                      # bisect: dates are sorted ascending
        mid = (lo + hi) // 2
        if dates[mid] <= target:
            found, lo = mid, mid + 1
        else:
            hi = mid - 1
    return found


def pct_return_asof(closes, dates, days_back) -> float | None:
    """% return from the last close on or before `days_back` CALENDAR days ago.
    Deterministic across fetches with differing row counts."""
    if not closes or not dates or len(closes) != len(dates):
        return None
    i = asof_index(dates, days_back)
    if i is None or i >= len(closes) - 0 or not closes[i]:
        return None
    if i == len(closes) - 1:             # target is the last bar itself
        return None
    return closes[-1] / closes[i] - 1.0


def pct_return(closes, window) -> float | None:
    """Close-to-close % return over `window` ROWS.

    ROW-OFFSET. Retained for pure fixture tests and for callers that hold a
    known-complete series. Everything in the pack that touches fetched data now
    uses pct_return_asof -- see asof_index for why."""
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
    ("1W",  lambda c, d: pct_return_asof(c, d, 7)),
    ("1M",  lambda c, d: pct_return_asof(c, d, 28)),
    ("MTD", lambda c, d: mtd_return(c, d)),
    ("QTD", lambda c, d: qtd_return(c, d)),
]


def sector_row(closes, dates) -> dict:
    """Hedgeye's sector table windows: 1-Day, MTD, QTD, YTD (deck p38/p39).
    Deliberately NOT the factor-board windows — the two pages measure
    different things and matching Hedgeye matters more than internal symmetry."""
    return {"price": closes[-1] if closes else None,
            "1D": pct_return_asof(closes, dates, 1),
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
    """{'1D','1W','1M','3M','6M','YTD'} for one series, DATE-INDEXED.
    Row offsets moved with the row count; calendar dates do not."""
    out = {lbl: pct_return_asof(closes, dates, d) for lbl, d in WINDOW_DAYS}
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


def align_on_dates(a_closes, a_dates, b_closes, b_dates) -> tuple:
    """(closes_a, closes_b) restricted to the DATE INTERSECTION of the two.

    2026-08-16, the SECOND defect from the same 24/7 asymmetry. The bar-date
    bug (a Sunday BTC bar defining the header) was one consequence; this is the
    other, and fixing the header did not fix it.

    corr_over used to take `ra[-n:]` and `rb[-n:]` — aligned BY POSITION from
    the end. Bitcoin trades 7 days a week and equities 5, so the two series
    walk different calendars. Measured on the live frame:
        SPY's last 90 returns span 2026-04-07 .. 2026-08-14
        BTC's last 90 returns span 2026-05-18 .. 2026-08-16
    Those are different periods, so every BTC correlation was pairing Monday's
    equity return against Saturday's crypto return and drifting further apart
    the longer the window. The error reached +0.41 at 180D. A control on two
    equity series (identical date grid) moves +0.00 at every window, which is
    what proves the cause is the calendar and not the maths.
    """
    if not (a_dates and b_dates):
        return a_closes, b_closes
    da = dict(zip(a_dates, a_closes or []))
    db = dict(zip(b_dates, b_closes or []))
    common = sorted(set(da) & set(db))
    return [da[d] for d in common], [db[d] for d in common]


def corr_over(a_closes, b_closes, window, a_dates=None, b_dates=None):
    """Correlation of daily returns over the last `window` SHARED sessions.

    Pass dates whenever you have them: they do BOTH jobs. align_on_dates fixes
    the 24/7-vs-5-day pairing, and the window is then bounded by CALENDAR DATE
    rather than by a row count, so a dropped bar cannot silently change which
    period is being measured (the 2026-08-16 non-determinism).
    Without dates this falls back to positional, which is only safe on a known
    complete, shared grid.
    """
    if a_dates and b_dates:
        da = dict(zip(a_dates, a_closes or []))
        db = dict(zip(b_dates, b_closes or []))
        common = sorted(set(da) & set(db))
        if len(common) < 4:
            return None
        span = CORR_WINDOW_DAYS.get(window, int(window * 1.4))
        from datetime import timedelta
        cutoff = common[-1] - timedelta(days=span)
        common = [d for d in common if d >= cutoff]
        a_closes = [da[d] for d in common]
        b_closes = [db[d] for d in common]
        ra, rb = daily_returns(a_closes), daily_returns(b_closes)
        n = min(len(ra), len(rb))
        return pearson(ra, rb) if n >= 3 else None
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


def window_anchor_line(closes, dates, label="windows") -> str:
    """The RESOLVED base date for every window, printed under the returns tables.

    Third time this lesson has paid: print the INPUTS, not just the outputs.
    Prior levels made the frozen HY series obvious on sight; the per-row as-of
    column made the FRED/price split visible; and without these anchors a
    base-date SHIFT is indistinguishable from data CORRUPTION -- which cost two
    builds of argument over whether the indexing fix was a regression.

    Uses SPY (or whatever series is passed) as the reference grid: every symbol
    resolves against the same calendar, so one line covers the table.
    """
    if not dates:
        return "%s: unavailable (no dates)" % label
    bits = []
    for lbl, days_back in WINDOW_DAYS:
        i = asof_index(dates, days_back)
        bits.append("%s=%s" % (lbl, dates[i].strftime("%m-%d") if i is not None
                               else "n/a"))
    ytd_base = None
    yr = dates[-1].year
    for d in dates:
        if d.year < yr:
            ytd_base = d
        else:
            break
    bits.append("YTD=%s" % (ytd_base.strftime("%m-%d") if ytd_base else "n/a"))
    return ("%s (as-of %s): " % (label, dates[-1]) + " - ".join(bits)
            + "\n  definition: 1D=1 calendar day back, 1W=7, 1M=28 "
              "(= Hedgeye's '4 Wks Ago'), 3M=91, 6M=182; each resolves to the "
              "last session ON OR BEFORE that date")


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
    period = f"{int(lookback_days * 1.5) + 30}d"      # see _fetch_bars docstring
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


# ── deterministic bar store ────────────────────────────────────────────────

def _bars_from_store(as_of, symbols) -> dict:
    """Bars previously banked for `as_of`, or {} if none/partial.

    Replay is ALL-OR-NOTHING: a partial store would silently mix banked and
    freshly-fetched series in one pack, which is the same class of bug as
    mixing two row counts."""
    import db_pg
    try:
        with db_pg.get_conn() as c:
            cur = c.cursor()
            cur.execute("SELECT symbol, bars FROM eod_bar_store WHERE as_of=%s",
                        (as_of,))
            rows = cur.fetchall()
    except Exception as e:
        log.warning("bar store unreadable (%s) — fetching live", e)
        return {}
    have = {sym: b for sym, b in rows}
    if not have or not set(symbols) <= set(have):
        return {}
    from datetime import date as _d
    out = {}
    for sym in symbols:
        pairs = have[sym]
        out[sym] = {"dates": [_d.fromisoformat(p[0]) for p in pairs],
                    "closes": [float(p[1]) for p in pairs]}
    return out


def _bank_bars(as_of, bars) -> None:
    """Write the fetched bars for `as_of`. Never raises — failing to bank must
    not stop delivery, but it does mean the build is not replayable, so it
    says so."""
    import json
    import db_pg
    try:
        with db_pg.get_conn() as c:
            cur = c.cursor()
            for sym, b in (bars or {}).items():
                d, cl = b.get("dates") or [], b.get("closes") or []
                if not d:
                    continue
                payload = json.dumps([[x.isoformat(), y] for x, y in zip(d, cl)])
                cur.execute(
                    """INSERT INTO eod_bar_store
                       (as_of, symbol, bars, n_bars, first_bar, last_bar)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (as_of, symbol) DO NOTHING""",
                    (as_of, sym, payload, len(d), d[0], d[-1]))
            c.commit()
        log.info("banked %d symbols for as_of %s", len(bars or {}), as_of)
    except Exception as e:
        log.error("BARS NOT BANKED for %s (%s) — this build is NOT replayable",
                  as_of, e)


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
# §1.2 SERIES DEFINITIONS — each one documented, because a spread is only
# meaningful if you know what was subtracted from what.
#
# 2026-08-16: the pack printed BBB 0.98 against Hedgeye's 0.89. Cause found by
# reading the code, not by guessing: BAMLC0A4CBBB is the ICE BofA BBB US
# Corporate Index OPTION-ADJUSTED SPREAD (already a spread to the curve).
# Hedgeye's slide is explicitly "US Corp BBB MINUS Treasury 10-Year" — a
# different construction with a different denominatorless subtraction, so the
# two can never agree. Replaced with the credit YIELD series and an explicit
# subtraction of DGS10, which reproduces Hedgeye's definition.
#
# HY OAS keeps BAMLH0A0HYM2: that IS the standard HY OAS and matches Hedgeye's
# construction. Its 2.71-vs-2.66 gap is therefore NOT a series mismatch and is
# most likely an as-of difference (pack 8/16 vs deck 8/13 close) — recorded
# here so the next reader does not re-litigate it. Verify against the golden
# reference once a FRED key exists.
FRED_SERIES = [
    ("2y UST",   "DGS2",           "%"),
    ("10y UST",  "DGS10",          "%"),
    ("HY OAS",   "BAMLH0A0HYM2",   "%"),
]
# Derived spreads: (label, minuend_series, subtrahend_series). Computed by
# DATE-ALIGNED subtraction, never by index — the two series have different
# holiday gaps and zipping them by position silently compares different days.
FRED_SPREADS = [
    ("BBB-10y", "BAMLC0A4CBBBEY", "DGS10"),   # BBB effective YIELD minus UST10Y
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
    """{'last','1D','1W','1M', + prior levels and dates} — LEVEL changes in
    basis points, not percent returns. A yield going 4.10 -> 4.20 is +10bp;
    calling that '+2.4%' is the kind of number that reads fine and means
    nothing.

    PRIOR LEVELS AND THEIR DATES ARE RETURNED (2026-08-16). HY OAS printed
    +0/+0/+0 across all three windows while BBB-10y, rendered by this SAME
    function and the SAME formatter, printed +0/+2/-1. So the arithmetic and
    the formatting demonstrably work and the zeros are SPECIFIC TO THAT SERIES:
    it is flat or frozen at source. That could not be diagnosed from the pack
    because the pack printed only the deltas — with the prior LEVELS beside
    them, "flat series" and "broken delta" are distinguishable on sight.
    This is also §2.1's requirement, which is why §2.1 is diagnostic and not
    cosmetic.
    OBS_DATE is carried too: a frozen series shows as a stale last-observation
    date, which is the check that needs no FRED key to interpret.
    """
    if not obs:
        return {}
    vals = [v for _, v in obs]
    raw_dates = [d for d, _ in obs]
    # FRED dates arrive as ISO strings; index by DATE like everything else.
    # If they are NOT parseable, say so and fall back to row offsets rather
    # than raising -- but flag it, because a silent fallback to row offsets is
    # precisely the bug this indexing change exists to remove.
    from datetime import date as _date
    dates, parse_ok = [], True
    for d in raw_dates:
        if isinstance(d, _date):
            dates.append(d)
            continue
        try:
            dates.append(_date.fromisoformat(str(d)[:10]))
        except ValueError:
            parse_ok = False
            break
    last = vals[-1]
    out = {"last": last, "last_date": raw_dates[-1] if raw_dates else None,
           "n_obs": len(vals)}
    # DATE-INDEXED, not row-offset. This was the last row offset left in the
    # pack: FRED series drop holidays, so "21 observations back" was a moving
    # calendar target for exactly the same reason the price windows were.
    # The offsets are the SAME calendar days the price tables use, so "1M" now
    # means one thing in this document instead of two.
    out["date_parse_failed"] = not parse_ok
    if not parse_ok:
        log.error("level_changes: observation dates are not ISO — falling back "
                  "to ROW OFFSETS, which are not deterministic. Windows below "
                  "are approximate.")
    for lbl, days_back in WINDOW_DAYS[:3]:          # 1D=1, 1W=7, 1M=28
        if not parse_ok:                            # degraded, and it says so
            n = {1: 1, 7: 5, 28: 21}[days_back]
            i = len(vals) - 1 - n if len(vals) > n else None
            if i is not None:
                out[lbl] = (last - vals[i]) * 100
                out[lbl + "_level"] = vals[i]
                out[lbl + "_date"] = raw_dates[i]
            else:
                out[lbl] = out[lbl + "_level"] = out[lbl + "_date"] = None
            continue
        i = asof_index(dates, days_back)
        if i is not None and i < len(vals) - 1:
            out[lbl] = (last - vals[i]) * 100
            out[lbl + "_level"] = vals[i]
            out[lbl + "_date"] = raw_dates[i]
            # The NEXT observation after the resolved anchor. Hedgeye may
            # anchor lookbacks on the PUBLICATION date (08-14) while levels
            # come from the prior close (08-13), which would put their
            # "4 Wks Ago" one session later than ours. Printing both sides of
            # the boundary lets one build discriminate that from a resolution
            # bug, instead of a round trip per hypothesis.
            if i + 1 < len(vals):
                out[lbl + "_next_date"] = raw_dates[i + 1]
                out[lbl + "_next_level"] = vals[i + 1]
        else:
            out[lbl] = None
            out[lbl + "_level"] = None
            out[lbl + "_date"] = None
    # A series whose last N observations are all identical is FROZEN, not calm.
    tail = vals[-22:]
    out["frozen"] = len(tail) > 3 and max(tail) == min(tail)
    # The observation dates FRED actually returned, always -- not only when
    # frozen. This answers "is the pack taking the second-newest observation?"
    # directly: if the newest date printed here IS the session and the pack
    # still selects an older one, that is an off-by-one; if the newest date
    # returned is itself a session behind, FRED had not published yet and the
    # pack is correct. Print the input.
    out["date_tail"] = " ".join(str(d) for d in raw_dates[-6:])
    if out["frozen"]:
        # The EVIDENCE, not just the verdict. Five (date, value) pairs settle
        # "wrong series ID" vs "stale at source" in one look, without a key.
        out["obs_tail"] = " ".join("%s=%.2f" % (raw_dates[i], vals[i])
                                   for i in range(max(0, len(vals) - 5),
                                                  len(vals)))
    return out


def format_rates_credit(rows, curve) -> str:
    """rows: [(label, {last,1D,1W,1M,...})]. Levels in %, changes in bp.

    §2.1 template: PRIOR LEVELS are printed beside the deltas. Deltas alone
    cannot distinguish a flat series from a broken one — which is exactly the
    ambiguity that made HY OAS's +0/+0/+0 unreadable for two rounds."""
    # PER-BLOCK WINDOW ANCHORS. This block resolves its windows from ITS OWN
    # as-of (each FRED series' last observation), NOT from the pack's global
    # price as-of -- level_changes calls asof_index without a ref, so the ref is
    # that series' own dates[-1]. That was already true, but it was invisible:
    # with only the returns tables printing an anchor line, a reader comparing
    # a rate against the deck could not tell WHICH base date produced the
    # delta, and a 2-3bp gap at 1M is indistinguishable from a one-session
    # offset. Now each block states its own.
    anchors = ""
    for _lbl, _d in rows:
        if _d and _d.get("last_date"):
            anchors = ("  anchors (rates as-of %s): 1D=%s - 1W=%s - 1M=%s"
                       % (_d.get("last_date"), _d.get("1D_date") or "n/a",
                          _d.get("1W_date") or "n/a", _d.get("1M_date") or "n/a"))
            if _d.get("1M_next_date"):
                anchors += ("\n  1M boundary [%s]: resolved %s=%.2f - next obs "
                            "%s=%.2f  (if the deck matches the NEXT one, it "
                            "anchors lookbacks on the publication date, not the "
                            "close -- a convention difference, not a bug)"
                            % (_lbl, _d.get("1M_date"), _d.get("1M_level"),
                               _d.get("1M_next_date"), _d.get("1M_next_level")))
            break
    out = ["RATES + CREDIT (level %, changes in bp; prior levels shown)",
           "  windows: 1D=1 calendar day  1W=7  1M=28 (= Hedgeye's '4 Wks Ago')",
           f"{'series':<10}{'last':>8}{'1D ago':>8}{'1W ago':>8}{'1M ago':>8}"
           f"{'1D':>7}{'1W':>7}{'1M':>7}  as-of"]
    if anchors:
        out.insert(2, anchors)
    for _lbl, _d in rows:
        if _d and _d.get("date_tail"):
            out.insert(3, "  %s observations returned (last 6): %s  -> selected "
                          "%s as 'last'"
                       % (_lbl, _d["date_tail"], _d.get("last_date")))
            break

    def _bp(v, w=7):
        return "n/a".rjust(w) if v is None else f"{v:+.0f}".rjust(w)

    def _lv(v, w=8):
        return "n/a".rjust(w) if v is None else f"{v:.2f}".rjust(w)

    for label, d in rows:
        if not d:
            out.append(f"{label:<10}" + "n/a (no observations)".rjust(8))
            continue
        out.append(f"{label:<10}{d['last']:>8.2f}"
                   + _lv(d.get("1D_level")) + _lv(d.get("1W_level"))
                   + _lv(d.get("1M_level"))
                   + _bp(d.get("1D")) + _bp(d.get("1W")) + _bp(d.get("1M"))
                   + "  " + str(d.get("last_date") or "?")
                   + ("  !! FROZEN — every observation in the last month is "
                      "identical; this is a STALE SERIES, not a calm one"
                      if d.get("frozen") else "")
                   + ("  [obs tail: %s]" % d["obs_tail"]
                      if d.get("frozen") and d.get("obs_tail") else ""))
    if curve:
        # The derived row must fill the SAME eight columns as every other row.
        # It previously emitted only four, so its DELTAS rendered underneath the
        # prior-level headers and the row read as a different metric entirely.
        # curve_2_10 and spread_series both return prior levels — they run the
        # date-aligned paired series through level_changes — so these come from
        # the spread's own history rather than being re-derived from the legs.
        out.append(f"{'2-10 curve':<10}{curve['last']:>8.2f}"
                   + _lv(curve.get("1D_level")) + _lv(curve.get("1W_level"))
                   + _lv(curve.get("1M_level"))
                   + _bp(curve.get("1D")) + _bp(curve.get("1W"))
                   + _bp(curve.get("1M"))
                   + "  " + str(curve.get("last_date") or "?")
                   + ("   INVERTED" if curve["last"] < 0 else "")
                   + ("  !! FROZEN" if curve.get("frozen") else ""))
    return "\n".join(out)


def spread_series(minuend, subtrahend) -> dict:
    """level_changes for (minuend - subtrahend), aligned ON DATE.

    Same discipline as curve_2_10: the two FRED series have different holiday
    gaps, so zipping by index silently subtracts different days. Used for
    BBB-minus-UST10Y, which is Hedgeye's construction (a raw BBB OAS is a
    different number and will never reconcile to their slide)."""
    if not minuend or not subtrahend:
        return {}
    sub = dict(subtrahend)
    paired = [(d, v - sub[d]) for d, v in minuend if d in sub]
    return level_changes(paired)


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
        wanted = {sid for _, sid, _ in FRED_SERIES}
        for _, a, b in FRED_SPREADS:
            wanted |= {a, b}
        fetched = {sid: fred_series(sid, key) for sid in sorted(wanted)}
        rows = [(label, level_changes(fetched.get(sid) or []))
                for label, sid, _ in FRED_SERIES]
        # Derived spreads, date-aligned (see FRED_SPREADS).
        for label, a, b in FRED_SPREADS:
            rows.append((label, spread_series(fetched.get(a) or [],
                                              fetched.get(b) or [])))
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


def _deployed_sha() -> str | None:
    """The commit the RUNNING process was built from. The single most useful
    field in the artifact table: on 2026-08-16 the first question asked was
    'which build produced this', and nothing recorded it."""
    import subprocess
    try:
        with __import__("db_pg").get_conn() as c:
            cur = c.cursor()
            cur.execute("SELECT value FROM bot_state WHERE key='bot_git_sha'")
            r = cur.fetchone()
            if r and r[0]:
                return r[0]
    except Exception:
        pass
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def _persist_pack(body, last_bar, valid, block_reason, ok_n, total_n, built_et):
    """Retain the artifact. NEVER raises: a failure to archive must not stop
    the pack being delivered. Logs loudly instead, because an unarchived run is
    an undiagnosable one."""
    try:
        import db_pg
        with db_pg.get_conn() as c:
            cur = c.cursor()
            cur.execute(
                """INSERT INTO eod_pack_artifacts
                   (built_at_et, last_bar_date, bar_date_valid, block_reason,
                    deployed_sha, symbols_ok, symbols_total, body)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (built_et, last_bar, valid, block_reason, _deployed_sha(),
                 ok_n, total_n, body))
            rid = cur.fetchone()[0]
            c.commit()
        log.info("eod pack archived as artifact %s (bar %s, valid=%s)",
                 rid, last_bar, valid)
        return rid
    except Exception as e:
        log.error("EOD PACK NOT ARCHIVED (%s) — this run will be "
                  "undiagnosable if it turns out to be wrong", e)
        return None


def build_eod_pack() -> str:
    """Assemble the pack. Every section guarded; a failure prints in place."""
    parts, header_quad, quad_stale = _header()

    need = {BENCH}
    for _, lng, sht, _ in FACTORS:
        need |= {lng, sht}
    need |= set(SECTORS)
    need |= {s for _, s in CORR_ROWS} | {s for _, s in CORR_ANCHORS}
    need |= set(quad_tape.doctrine_tickers())
    # DETERMINISM. Replay banked bars for this as-of when they exist, so a
    # rebuild is byte-identical. yfinance is NOT reproducible -- three identical
    # ranged calls returned 617/617/614 rows for CPER -- so the only way a
    # second build can match the first is to reuse the first build's input.
    from tools.trading_calendar import last_completed_session
    _asof = last_completed_session()
    bars = _bars_from_store(_asof, sorted(need))
    replayed = bool(bars)
    if not bars:
        bars = _fetch_bars(sorted(need))
        _bank_bars(_asof, bars)
    got = sum(1 for s in need if bars.get(s, {}).get("closes"))
    # H2: as-of stamp. The LAST BAR DATE, not the clock — a pack built at 09:00
    # Monday off Friday's closes is not stale by the clock and is stale by three
    # days of tape. Both are printed so the gap between them is visible.
    # Resolve the EQUITY session, not max() over every symbol. BTC-USD trades
    # 24/7 and its weekend bar used to define the header date -- see
    # trading_calendar.resolve_session_date for the full root cause.
    from tools.trading_calendar import resolve_session_date
    last_bar, off_consensus = resolve_session_date(bars)

    # ── §1.1 TRADING-CALENDAR ASSERTION ────────────────────────────────────
    # The bar date is now VALIDATED against an NYSE calendar before anything
    # is printed. On 2026-08-16 this header read "last bar 2026-08-16" — a
    # SUNDAY — so every "1D" field was Thursday->Friday while the header
    # claimed otherwise. It failed silently and looked correct.
    # On failure this returns a BLOCKING banner and NO data table: a pack that
    # cannot say which session it covers must not publish numbers that imply
    # one. DATA AS OF and BUILT are printed as separate, unambiguous facts.
    from tools.trading_calendar import (validate_bar_date, duplicate_final_bar,
                                        last_completed_session)
    ok, why = validate_bar_date(last_bar)
    dup, dup_detail = duplicate_final_bar(bars)
    built = _now_et()
    if not ok or dup:
        reason = why if not ok else ("final bar is a duplicate: " + dup_detail)
        return "\n".join([
            "!! EOD PACK BLOCKED — THE BAR DATE FAILED VALIDATION.",
            "   %s" % reason,
            "   expected last completed session: %s" % last_completed_session(),
            "   BUILT: %s" % built,
            "",
            "   No data table is printed. Every window in this pack ('1D',",
            "   '1W', '1M') is defined relative to the last bar, so if the bar",
            "   date is wrong every one of those labels is wrong too — and a",
            "   table that looks right is worse than no table.",
            "   duplicate-bar check: %s" % dup_detail,
            "   symbols with data: %d/%d" % (got, len(need)),
        ])
        # Archive the FAILURE too -- a blocked run is exactly the one a future
        # investigation needs to see.
        _persist_pack(blocked, last_bar, False, reason, got, len(need), built)
        return blocked
    asof_line_idx = len(parts)
    parts.append(f"DATA AS OF: {last_bar:%a %Y-%m-%d} close")
    parts.append(f"BUILT:      {built}")
    parts.append(f"(price data: {got}/{len(need)} symbols · bar date validated "
                 f"against the NYSE calendar · bars "
                 f"{'REPLAYED from store' if replayed else 'fetched and banked'}"
                 f" for as-of {_asof})")
    if off_consensus:
        # Named, never silent. A 24/7 instrument legitimately has a later bar;
        # it just must not define the session. Anything ELSE in this list is
        # lagging and worth knowing about.
        parts.append(f"  note: {len(off_consensus)} symbol(s) off the session "
                     f"date ({', '.join(off_consensus[:6])}"
                     f"{' ...' if len(off_consensus) > 6 else ''}) — 24/7 or "
                     f"lagging instruments; their data is still used, they do "
                     f"not set the session")
    # Book age. The pack carries TWO independent staleness axes (price bars and
    # the broker book) and one being current says nothing about the other.
    # NB the wording is scoped to what THIS document actually contains: the EOD
    # pack renders no position, weight or fill figures (those are report.py), so
    # a banner promising "figures below" would be crying wolf and would mask a
    # real staleness event later.
    try:
        from tools.book_freshness import book_banner
        parts.append(book_banner(carries_positions=False))
    except Exception as e:
        parts.append(f"!! BOOK AGE UNKNOWN ({e})")

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
        # RESOLVED window base dates. Without these a base-date SHIFT and data
        # CORRUPTION look identical in the output — exactly the ambiguity that
        # cost two builds of argument.
        _ref = bars.get(BENCH) or {}
        parts.append("  " + window_anchor_line(_ref.get("closes"),
                                               _ref.get("dates")))
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
            ab = bars.get(asym) or {}
            ac, ad = ab.get("closes"), ab.get("dates")
            rows = []
            for rlabel, rsym in CORR_ROWS:
                if rsym == asym:
                    continue                      # corr(x,x) = 1, no information
                rb_ = bars.get(rsym) or {}
                rc, rd = rb_.get("closes"), rb_.get("dates")
                # DATES ARE PASSED. Without them a 24/7 instrument (BTC-USD) is
                # paired positionally against 5-day equities and the two series
                # drift onto different calendars — see align_on_dates.
                rows.append((f"{rlabel} ({rsym})",
                             {w: corr_over(ac, rc, w, ad, rd)
                              for w in CORR_WINDOWS}))
            blocks.append((alabel, asym, rows))
        parts.append("")
        parts.append(format_correlations(blocks))
    except Exception as e:
        parts.append(f"\nCORRELATIONS: unavailable ({e})")

    parts.append("")
    _rc = _rates_credit_block()
    parts.append(_rc)
    # HEADER-LEVEL AS-OF SPLIT. The pack mixes a price session with a
    # rates/credit session, and until now only the rates block said so -- a
    # reader on a phone would never scroll far enough to find out. Silently
    # mixing two sessions in one document is the same class of problem as the
    # original Sunday bar, so it is stated at the top or not at all.
    _m = re.search(r"anchors \(rates as-of ([0-9]{4}-[0-9]{2}-[0-9]{2})\)", _rc)
    if _m and _m.group(1) != str(last_bar):
        parts[asof_line_idx] = (
            "DATA AS OF: prices %s close - rates/credit %s   << TWO SESSIONS"
            % (last_bar.strftime("%a %Y-%m-%d"), _m.group(1)))
    elif _m:
        parts[asof_line_idx] = ("DATA AS OF: %s close (prices and rates/credit "
                                "both)" % last_bar.strftime("%a %Y-%m-%d"))
    parts.append("VOL COMPLEX (VIX/VXN/RVX/VVIX/MOVE/GVZ/OVX) + IVOL: Phase 2")
    parts.append("CFTC positioning + FX realized-vol proxy: Phase 3")
    body = "\n".join(parts)
    # §1.1b ARTIFACT RETENTION. Every pack persists with its build time, the
    # RESOLVED bar date, the validation result and the DEPLOYED COMMIT SHA.
    # The 2026-08-16 Sunday-bar bug was unfalsifiable purely because no such
    # record existed; the next occurrence is answerable from one query.
    _persist_pack(body, last_bar, True, None, got, len(need), built)
    return body


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
