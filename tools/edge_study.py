"""tools/edge_study.py - what came before rallies and declines (Kris 11/24/2026).

"Look across everything (TRACE, HIRO, the volatility dashboard, risk ranges) for patterns that precede a rally
or a decline, and see which side a factor leans."

One table, every feed. Each line is a yes/no condition that was known BEFORE the open of a session, and what the
S&P 500 did after it:
  day    the session itself, prior close to that day's close (includes the overnight gap)
  +5d    the 5 sessions after that day's close
  +10d   the 10 sessions after that day's close
compared with every other day in the same stretch of history. Feeds with little history still get a line, marked
"too few days", so the table fills in by itself as the history builds. Nothing here changes a rule.

A second table tests the card's core idea on every Risk Range name: where the prior close sat in Keith's range
(rp), by his trend call, against that name's own next 5 sessions.

  railway run py -m tools.edge_study            # prints the short read, writes data/reviews/edge_study_<date>.md
  railway run py -m tools.edge_study --all      # print every line, not only the ones worth watching

How a line earns "worth watching": 30+ days with the condition on, a gap from the other days that is about twice
its own noise (t 2.0 or more, shrunk for overlapping 5- and 10-day windows), and the same sign in the first and
the second half of the history. With about 80 lines and 3 horizons a few will pass by luck alone, so a pass is a
lead to watch, not a rule.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys

import numpy as np
import pandas as pd

MIN_DAYS = 30
HORIZONS = (("day", 1), ("+5d", 5), ("+10d", 10))
OUT_DIR = os.path.join("data", "reviews")


# ---------------------------------------------------------------- loading
def _q(cur, sql: str, cols: list[str]) -> pd.DataFrame:
    try:
        cur.execute(sql)
        df = pd.DataFrame(cur.fetchall(), columns=cols)
    except Exception:   # a feed's table may not exist yet: that feed just gets no lines
        cur.connection.rollback()
        return pd.DataFrame(columns=cols)
    for c in cols:
        if c not in ("ticker", "trend", "txt", "sym") and len(df):
            try:
                df[c] = pd.to_numeric(df[c])
            except (ValueError, TypeError):
                pass
    return df


def _dated(df: pd.DataFrame, col: str = "d") -> pd.DataFrame:
    df = df.copy()
    df[col] = pd.to_datetime(df[col])
    return df.sort_values(col).drop_duplicates(col, keep="last").set_index(col)


def load() -> dict:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        rr = _q(cur, "SELECT ticker, signal_date, trend, buy_trade, sell_trade, prev_close FROM hedgeye_risk_ranges "
                     "WHERE buy_trade IS NOT NULL AND sell_trade IS NOT NULL AND prev_close IS NOT NULL",
                ["ticker", "d", "trend", "lo", "hi", "pc"])
        hy = _q(cur, "SELECT obs_date, value FROM fred_observations WHERE series_id='BAMLH0A0HYM2' AND value IS NOT NULL",
                ["d", "v"])
        quad = _q(cur, "SELECT date, monthly_quad, quarterly_quad FROM quad_nowcast_daily "
                       "UNION ALL SELECT as_of::date, monthly_quad, quarterly_quad FROM hedgeye_quad",
                  ["d", "mq", "qq"])
        sg = _q(cur, "SELECT note_date, ref_price, vol_trigger, zero_gamma, call_wall, put_wall FROM sg_index_levels "
                     "WHERE sym='SPX'", ["d", "ref", "vt", "zg", "cw", "pw"])
        t1a = _q(cur, "SELECT report_date, gamma_exposure, systematic_flow_risk, gex_throttle FROM t1a_snapshots",
                 ["d", "txt", "trend", "throttle"])
        hub = _q(cur, "SELECT snapshot_date, max(iv_1m), max(rv_1m), max(iv_rank), max(skew_rank), max(call_volume), "
                      "max(put_volume) FROM spotgamma_snapshots WHERE ticker='SPY' AND iv_1m IS NOT NULL GROUP BY 1",
                 ["d", "iv", "rv", "iv_rank", "skew_rank", "cvol", "pvol"])
        watch = _q(cur, "SELECT read_date, verdict, red_reads, total_reads FROM sg_watch_days",
                   ["d", "txt", "red", "total"])
        vol = _q(cur, "SELECT trade_date, iv30_pos, slope, put_skew, atm_pos FROM sg_vol_daily WHERE sym='SPX'",
                 ["d", "iv30_pos", "slope", "put_skew", "atm_pos"])
        tape = _q(cur, "SELECT DISTINCT ON ((read_at AT TIME ZONE 'America/New_York')::date) "
                       "(read_at AT TIME ZONE 'America/New_York')::date, prem_put, prem_call, delta_put, delta_call "
                       "FROM sg_tape_reads WHERE (read_at AT TIME ZONE 'America/New_York')::time >= '15:30' "
                       "ORDER BY 1, read_at DESC", ["d", "prem_put", "prem_call", "delta_put", "delta_call"])
    return {"rr": rr, "hy": hy, "quad": quad, "sg": sg, "t1a": t1a, "hub": hub, "watch": watch, "vol": vol, "tape": tape}


# ---------------------------------------------------------------- panel
def _ticker(rr: pd.DataFrame, t: str) -> pd.DataFrame:
    x = _dated(rr[rr.ticker == t])
    w = (x.hi - x.lo).where(x.hi > x.lo)
    x["rp"] = (x.pc - x.lo) / w
    x["width"] = w / x.pc
    return x


def _prior(df: pd.DataFrame, idx: pd.DatetimeIndex, max_age_days: int = 5) -> pd.DataFrame:
    """Each session gets the feed's last row dated BEFORE that session (never the same day), if it is recent."""
    if df.empty:
        return pd.DataFrame(index=idx, columns=df.columns)
    left = pd.DataFrame({"s": idx})
    right = df.reset_index().rename(columns={df.index.name or "index": "d"})
    m = pd.merge_asof(left, right, left_on="s", right_on="d", allow_exact_matches=False,
                      tolerance=pd.Timedelta(days=max_age_days))
    return m.set_index("s").drop(columns=["d"])


def _same(df: pd.DataFrame, idx: pd.DatetimeIndex) -> pd.DataFrame:
    return df.reindex(idx)


def build(data: dict) -> tuple[pd.DataFrame, list[tuple[str, str, pd.Series]]]:
    """Returns the SPX outcome frame and a list of (feed, condition, yes/no series)."""
    rr = data["rr"]
    spx = _ticker(rr, "SPX")
    idx = spx.index
    out = pd.DataFrame(index=idx)
    close = spx.pc.shift(-1)                     # the session's own close = the next row's prior close
    out["day"] = close / spx.pc - 1
    for name, k in HORIZONS[1:]:
        out[name] = spx.pc.shift(-1 - k) / close - 1

    F: list[tuple[str, str, pd.Series]] = []

    def add(feed: str, name: str, s) -> None:
        s = pd.Series(s, index=idx) if not isinstance(s, pd.Series) else s.reindex(idx)
        F.append((feed, name, s))

    def flag(cond: pd.Series, known: pd.Series) -> pd.Series:
        """yes/no where the inputs were known; blank where they were not."""
        return cond.astype("float").where(known.notna())

    # --- Hedgeye Risk Range: the S&P's own row
    rr_feed = "Risk Range"
    add(rr_feed, "SPX low in range (rp 0.20 or less)", flag(spx.rp <= 0.20, spx.rp))
    add(rr_feed, "SPX high in range (rp 0.80+)", flag(spx.rp >= 0.80, spx.rp))
    add(rr_feed, "SPX below the range low", flag(spx.rp < 0, spx.rp))
    add(rr_feed, "SPX above the range top", flag(spx.rp > 1, spx.rp))
    add(rr_feed, "SPX trend Bullish", flag(spx.trend == "BULLISH", spx.trend))
    add(rr_feed, "SPX trend Bearish", flag(spx.trend == "BEARISH", spx.trend))
    flip = spx.trend.ne(spx.trend.shift(1)) & spx.trend.shift(1).notna()
    add(rr_feed, "SPX trend turned Bearish in the last 5 sessions",
        flag((flip & (spx.trend == "BEARISH")).rolling(5).max() > 0, spx.trend.shift(5)))
    add(rr_feed, "SPX trend turned Bullish in the last 5 sessions",
        flag((flip & (spx.trend == "BULLISH")).rolling(5).max() > 0, spx.trend.shift(5)))
    hh = spx.hi > spx.hi.shift(5)
    hl = spx.lo > spx.lo.shift(5)
    k5 = spx.hi.shift(5)
    add(rr_feed, "SPX range: higher high and higher low vs 5 sessions ago", flag(hh & hl, k5))
    add(rr_feed, "SPX range: lower high and lower low vs 5 sessions ago", flag(~hh & ~hl, k5))
    add(rr_feed, "SPX range: lower high, higher low (narrowing)", flag(~hh & hl, k5))
    add(rr_feed, "SPX range wider than 5 sessions ago by 25%+", flag(spx.width > spx.width.shift(5) * 1.25, spx.width.shift(5)))
    add(rr_feed, "SPX range narrower than 5 sessions ago by 20%+", flag(spx.width < spx.width.shift(5) * 0.80, spx.width.shift(5)))
    add(rr_feed, "SPX Bullish trend and low in range (rp 0.35 or less)", flag((spx.trend == "BULLISH") & (spx.rp <= 0.35), spx.rp))
    add(rr_feed, "SPX Bearish trend and high in range (rp 0.65+)", flag((spx.trend == "BEARISH") & (spx.rp >= 0.65), spx.rp))

    # --- price action from the same closes
    chg = spx.pc.pct_change()
    pa = "Price action"
    add(pa, "SPX down 3+ sessions in a row", flag((chg < 0).rolling(3).sum() == 3, chg.shift(2)))
    add(pa, "SPX up 3+ sessions in a row", flag((chg > 0).rolling(3).sum() == 3, chg.shift(2)))
    add(pa, "SPX fell 1%+ the prior session", flag(chg <= -0.01, chg))
    add(pa, "SPX rose 1%+ the prior session", flag(chg >= 0.01, chg))
    r5 = spx.pc / spx.pc.shift(5) - 1
    add(pa, "SPX down 3%+ over the prior 5 sessions", flag(r5 <= -0.03, r5))
    add(pa, "SPX up 3%+ over the prior 5 sessions", flag(r5 >= 0.03, r5))

    # --- VIX on the Risk Range
    vix = _ticker(rr, "VIX").reindex(idx)
    vf = "VIX (Risk Range)"
    add(vf, "VIX under 19", flag(vix.pc < 19, vix.pc))
    add(vf, "VIX 20 to 29", flag((vix.pc >= 20) & (vix.pc < 30), vix.pc))
    add(vf, "VIX 30+", flag(vix.pc >= 30, vix.pc))
    add(vf, "VIX above its 5-session average", flag(vix.pc > vix.pc.rolling(5).mean(), vix.pc.shift(4)))
    add(vf, "VIX high in its range (rp 0.80+)", flag(vix.rp >= 0.80, vix.rp))
    add(vf, "VIX low in its range (rp 0.20 or less)", flag(vix.rp <= 0.20, vix.rp))
    add(vf, "VIX trend Bullish", flag(vix.trend == "BULLISH", vix.trend))
    add(vf, "VIX range wider than 5 sessions ago by 25%+", flag(vix.width > vix.width.shift(5) * 1.25, vix.width.shift(5)))
    add(vf, "VIX top of range AND SPX bottom of range (0.80+ / 0.20-)", flag((vix.rp >= 0.80) & (spx.rp <= 0.20), vix.rp))
    add(vf, "VIX bottom of range AND SPX top of range (0.20- / 0.80+)", flag((vix.rp <= 0.20) & (spx.rp >= 0.80), vix.rp))

    # --- other markets on the Risk Range
    xf = "Cross-asset (Risk Range)"
    for t, label in (("COMPQ", "Nasdaq"), ("RUT", "Russell 2000"), ("USD", "Dollar"), ("HYG", "High-yield bonds (HYG)"),
                     ("BITCOIN", "Bitcoin"), ("COPPER", "Copper"), ("WTIC", "Oil (WTI)"), ("GOLD", "Gold"),
                     ("UST10Y", "10-year yield"), ("NVDA", "NVDA")):
        x = _ticker(rr, t).reindex(idx)
        add(xf, f"{label} trend Bullish", flag(x.trend == "BULLISH", x.trend))
        add(xf, f"{label} trend Bearish", flag(x.trend == "BEARISH", x.trend))
        if t in ("USD", "HYG", "BITCOIN", "COMPQ", "RUT", "UST10Y"):
            add(xf, f"{label} high in range (rp 0.80+)", flag(x.rp >= 0.80, x.rp))
            add(xf, f"{label} low in range (rp 0.20 or less)", flag(x.rp <= 0.20, x.rp))

    # --- breadth of the whole Risk Range sheet
    r2 = rr.copy()
    r2["d"] = pd.to_datetime(r2["d"])
    r2 = r2[~r2.ticker.isin(["VIX"]) & ~r2.ticker.str.startswith("UST")]
    w = (r2.hi - r2.lo).where(r2.hi > r2.lo)
    r2["rp"] = (r2.pc - r2.lo) / w
    g = r2.groupby("d")
    bull = g.apply(lambda x: (x.trend == "BULLISH").mean(), include_groups=False).reindex(idx)
    low = g.apply(lambda x: (x.rp <= 0.20).mean(), include_groups=False).reindex(idx)
    high = g.apply(lambda x: (x.rp >= 0.80).mean(), include_groups=False).reindex(idx)
    bf = "Breadth (Risk Range sheet)"
    add(bf, "60%+ of the sheet Bullish trend", flag(bull >= 0.60, bull))
    add(bf, "Under 40% of the sheet Bullish trend", flag(bull < 0.40, bull))
    add(bf, "Share Bullish lower than 5 sessions ago by 10 points+", flag(bull <= bull.shift(5) - 0.10, bull.shift(5)))
    add(bf, "Share Bullish higher than 5 sessions ago by 10 points+", flag(bull >= bull.shift(5) + 0.10, bull.shift(5)))
    add(bf, "30%+ of the sheet at the bottom of its range (washed out)", flag(low >= 0.30, low))
    add(bf, "30%+ of the sheet at the top of its range (stretched)", flag(high >= 0.30, high))

    # --- credit (FRED, known the next morning)
    if not data["hy"].empty:
        hy = _dated(data["hy"])
        hy["chg5"] = hy.v - hy.v.shift(5)
        h = _prior(hy, idx)
        cf = "Credit (FRED)"
        add(cf, "High-yield spread wider by 15 bp+ in 5 days", flag(h.chg5 >= 0.15, h.chg5))
        add(cf, "High-yield spread tighter by 15 bp+ in 5 days", flag(h.chg5 <= -0.15, h.chg5))
        add(cf, "High-yield spread above 3.00%", flag(h.v > 3.0, h.v))

    # --- quads
    if not data["quad"].empty:
        qd = _prior(_dated(data["quad"]), idx, max_age_days=10)
        for n in (1, 2, 3, 4):
            add("Hedgeye quad", f"Monthly Quad {n}", flag(qd.mq == n, qd.mq))
        for n in (1, 2, 3, 4):
            add("Hedgeye quad", f"Quarterly Quad {n}", flag(qd.qq == n, qd.qq))

    # --- SpotGamma index levels (the morning's levels, known before the open)
    if not data["sg"].empty:
        s = _same(_dated(data["sg"]), idx)
        sf = "SpotGamma levels"
        add(sf, "SPX below the Volatility Trigger (negative gamma)", flag(s.ref < s.vt, s.vt))
        add(sf, "SPX above the Volatility Trigger (positive gamma)", flag(s.ref >= s.vt, s.vt))
        add(sf, "SPX at or below the put wall", flag(s.ref <= s.pw, s.pw))
        add(sf, "SPX within 1% under the call wall", flag((s.ref <= s.cw) & (s.ref >= s.cw * 0.99), s.cw))
        add(sf, "Volatility Trigger lower than 5 sessions ago", flag(s.vt < s.vt.shift(5), s.vt.shift(5)))

    # --- Tier 1 Alpha (the morning report)
    if not data["t1a"].empty:
        t = _same(_dated(data["t1a"]), idx)
        tf = "Tier 1 Alpha"
        add(tf, "Gamma Negative", flag(t.txt.astype(str).str.lower().str.startswith("neg"), t.txt))
        add(tf, "Systematic flows Bearish", flag(t.trend.astype(str).str.lower().str.startswith("bear"), t.trend))
        add(tf, "Throttle negative", flag(t.throttle < 0, t.throttle))
        add(tf, "Throttle lower than the prior report", flag(t.throttle < t.throttle.shift(1), t.throttle.shift(1)))

    # --- SpotGamma equity hub, SPY (the prior session's close)
    if not data["hub"].empty:
        h = _prior(_dated(data["hub"]), idx)
        hf = "SpotGamma equity hub (SPY)"
        add(hf, "Implied vol above realized", flag(h.iv > h.rv, h.iv))
        add(hf, "Implied vol at or below realized (protection cheap)", flag(h.iv <= h.rv, h.iv))
        add(hf, "IV rank 0.80+", flag(h.iv_rank >= 0.80, h.iv_rank))
        add(hf, "Skew rank 0.20 or less", flag(h.skew_rank <= 0.20, h.skew_rank))
        add(hf, "Put volume above call volume", flag(h.pvol > h.cvol, h.pvol))

    # --- SpotGamma tape (the prior session's last read)
    if not data["tape"].empty:
        t = _prior(_dated(data["tape"]), idx)
        add("SpotGamma tape", "Put premium above call premium at the prior close", flag(t.prem_put > t.prem_call, t.prem_put))

    # --- SpotGamma watcher: TRACE / HIRO day read (the prior session)
    if not data["watch"].empty:
        wdf = _prior(_dated(data["watch"]), idx)
        wf = "SpotGamma watch (TRACE/HIRO)"
        v = wdf.txt.astype(str).str.upper()
        add(wf, "Prior day read: FALLING DAY", flag(v.str.contains("FALL"), wdf.txt))
        add(wf, "Prior day: half or more of the reads red", flag(wdf.red >= wdf.total / 2, wdf.total))

    # --- SpotGamma volatility dashboard (the prior session)
    if not data["vol"].empty:
        v = _prior(_dated(data["vol"]), idx)
        of = "SpotGamma volatility dashboard (SPX)"
        add(of, "30-day implied vol above SpotGamma's normal range", flag(v.iv30_pos > 1, v.iv30_pos))
        add(of, "30-day implied vol in the bottom fifth of the normal range", flag(v.iv30_pos < 0.2, v.iv30_pos))
        add(of, "Term structure inverted (30-day above 90-day)", flag(v.slope < 0, v.slope))
        add(of, "Put skew above 8 vol points (95% vs 105% strikes)", flag(v.put_skew > 0.08, v.put_skew))

    return out, F


# ---------------------------------------------------------------- statistics
def _stat(m: pd.Series, y: pd.Series, h: int) -> dict | None:
    ok = m.notna() & y.notna()
    on, off = y[ok & (m == 1)], y[ok & (m == 0)]
    n = len(on)
    if n == 0:
        return {"n": 0}
    r = {"n": n, "n_off": len(off), "avg": on.mean(), "up": (on > 0).mean(),
         "avg_off": off.mean() if len(off) else np.nan, "up_off": (off > 0).mean() if len(off) else np.nan}
    r["gap"] = r["avg"] - r["avg_off"]
    if n >= 5 and len(off) >= 5:
        se = math.sqrt(on.var() / n + off.var() / len(off))
        r["t"] = (r["gap"] / se / math.sqrt(h)) if se > 0 else 0.0   # overlapping windows carry about 1/h the information
    else:
        r["t"] = np.nan
    # the same sign in both halves of the days this condition could be judged on
    dates = y[ok].index
    mid = dates[len(dates) // 2] if len(dates) else None
    halves = []
    for sel in (dates < mid, dates >= mid) if mid is not None else ():
        yy, mm = y[ok][sel], m[ok][sel]
        a, b = yy[mm == 1], yy[mm == 0]
        halves.append(np.sign(a.mean() - b.mean()) if len(a) >= 5 and len(b) >= 5 else 0)
    r["both"] = bool(len(halves) == 2 and halves[0] == halves[1] != 0 and halves[0] == np.sign(r["gap"]))
    r["size"] = on.abs().mean()
    r["size_off"] = off.abs().mean() if len(off) else np.nan
    return r


def verdict(r: dict) -> str:
    if r["n"] < MIN_DAYS or r.get("n_off", 0) < MIN_DAYS:   # needs enough days both ON and OFF to compare
        return "too few days"
    if abs(r.get("t") or 0) >= 2.0 and r["both"]:
        return "WATCH: leans " + ("up" if r["gap"] > 0 else "down")
    return "no lean shown"


def study(out: pd.DataFrame, F) -> pd.DataFrame:
    rows = []
    for feed, name, m in F:
        row = {"feed": feed, "condition": name}
        for hn, h in HORIZONS:
            r = _stat(m, out[hn], h)
            row[hn] = r
        d = row["day"]
        row["days_on"] = d["n"]
        rows.append(row)
    return pd.DataFrame(rows)


def names_study(rr: pd.DataFrame) -> pd.DataFrame:
    """Every Risk Range name: where the prior close sat in the range, by trend, vs its own next sessions."""
    x = rr.copy()
    x["d"] = pd.to_datetime(x["d"])
    x = x[~x.ticker.isin(["VIX"]) & ~x.ticker.str.startswith("UST")].sort_values(["ticker", "d"])
    w = (x.hi - x.lo).where(x.hi > x.lo)
    x["rp"] = (x.pc - x.lo) / w
    g = x.groupby("ticker")
    close = g.pc.shift(-1)
    gap_ok = (g.d.shift(-1) - x.d).dt.days <= 5
    x["day"] = (close / x.pc - 1).where(gap_ok)
    x["+5d"] = (g.pc.shift(-6) / close - 1).where((g.d.shift(-6) - x.d).dt.days <= 12)
    x = x[x.rp.notna() & (x.day.abs() < 0.5)]
    bins = [-np.inf, 0, 0.20, 0.35, 0.65, 0.80, 1.0, np.inf]
    labels = ["below the range low", "0 to 0.20", "0.20 to 0.35", "0.35 to 0.65", "0.65 to 0.80", "0.80 to 1.0",
              "above the range top"]
    x["bucket"] = pd.cut(x.rp, bins=bins, labels=labels)
    mid = x.d.sort_values().iloc[len(x) // 2]
    rows = []
    for tr in ("BULLISH", "BEARISH", "NEUTRAL"):
        base = x[x.trend == tr]
        for b in labels:
            s = base[base.bucket == b]
            if s.empty:
                continue
            r = {"trend": tr.title(), "rp": b, "n": len(s)}
            for hn in ("day", "+5d"):
                y = s[hn].dropna()
                r[hn] = y.mean()
                r[hn + "_up"] = (y > 0).mean()
                r[hn + "_base"] = base[hn].mean()
                a, c = s[s.d < mid][hn].mean(), s[s.d >= mid][hn].mean()
                ba, bc = base[base.d < mid][hn].mean(), base[base.d >= mid][hn].mean()
                r[hn + "_both"] = bool(np.sign(a - ba) == np.sign(c - bc) == np.sign(r[hn] - r[hn + "_base"]))
                # one number per date (bucket names minus every same-trend name that date), so 30 names moving
                # together on one day count once, and the market's own move that day drops out
                sp = (s.groupby("d")[hn].mean() - base.groupby("d")[hn].mean()).dropna()
                k = 5 if hn == "+5d" else 1
                r[hn + "_edge"] = sp.mean() if len(sp) else np.nan
                r[hn + "_t"] = (sp.mean() / (sp.std() / math.sqrt(len(sp))) / math.sqrt(k)) if len(sp) >= 20 and sp.std() > 0 else np.nan
                r[hn + "_dates"] = len(sp)
            rows.append(r)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- report
def _pct(v, d: int = 2) -> str:
    return "-" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:+.{d}f}%"


def _line(row) -> str:
    cells = []
    for hn, _ in HORIZONS:
        r = row[hn]
        if not r or r["n"] == 0:
            cells.append("-")
            continue
        if r["n"] < MIN_DAYS or r.get("n_off", 0) < MIN_DAYS:
            cells.append(f"{_pct(r['avg'])} (n {r['n']}, too few to compare)")
        else:
            cells.append(f"{_pct(r['avg'])} vs {_pct(r['avg_off'])}, up {r['up'] * 100:.0f}% vs {r['up_off'] * 100:.0f}%; {verdict(r)}")
    d = row["day"]
    size = "-" if d["n"] < MIN_DAYS or d.get("n_off", 0) < MIN_DAYS else f"{d['size'] * 100:.2f}% vs {d['size_off'] * 100:.2f}%"
    return f"| {row['condition']} | {row['days_on']} | {cells[0]} | {cells[1]} | {cells[2]} | {size} |"


def report(out: pd.DataFrame, res: pd.DataFrame, names: pd.DataFrame) -> str:
    L = [f"# Pattern study: what came before rallies and declines ({dt.date.today()})", "",
         f"S&P 500 sessions in the study: {out['day'].notna().sum()} ({out.index.min().date()} to {out.index.max().date()}). "
         f"All days: day {_pct(out['day'].mean())}, +5d {_pct(out['+5d'].mean())}, +10d {_pct(out['+10d'].mean())}.", "",
         "Each cell: average S&P move with the condition ON vs every other day, and how often it was up. "
         "\"Move size\" is the average size of the session's move, up or down. A feed with under "
         f"{MIN_DAYS} days ON shows the raw average only.", ""]
    watch = []
    for feed in res.feed.unique():
        sub = res[res.feed == feed]
        L += [f"## {feed}", "", "| Condition | Days on | That day | Next 5 days | Next 10 days | Move size |", "|---|---|---|---|---|---|"]
        for _, row in sub.iterrows():
            L.append(_line(row))
            for hn, _ in HORIZONS:
                r = row[hn]
                if r and r["n"] >= MIN_DAYS and verdict(r).startswith("WATCH"):
                    watch.append((abs(r["t"]), f"{row['condition']} [{feed}] -> {hn}: {_pct(r['avg'])} vs {_pct(r['avg_off'])} "
                                               f"on other days, up {r['up'] * 100:.0f}% vs {r['up_off'] * 100:.0f}%, "
                                               f"{r['n']} days, t {r['t']:+.1f}"))
        L.append("")
    L += ["## Every Risk Range name: position in Keith's range, by his trend call", "",
          "Average move of the name itself (not the S&P). \"vs\" is the average for every day with that trend. "
          "\"Edge\" is the name's move minus the other same-trend names on the same date, one number per date, "
          "so the market's own move drops out; t 2.0+ means the gap is about twice its noise.", "",
          "| Trend | rp at the prior close | Name-days | That day | up | Edge that day (t) | Next 5 days | up | Edge 5 days (t) | Same in both halves (5d) |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    tt = lambda v: "-" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:+.1f}"  # noqa: E731
    for _, r in names.iterrows():
        L.append(f"| {r['trend']} | {r['rp']} | {r['n']} | {_pct(r['day'])} vs {_pct(r['day_base'])} | {r['day_up'] * 100:.0f}% | "
                 f"{_pct(r['day_edge'])} ({tt(r['day_t'])}) | "
                 f"{_pct(r['+5d'])} vs {_pct(r['+5d_base'])} | {r['+5d_up'] * 100:.0f}% | "
                 f"{_pct(r['+5d_edge'])} ({tt(r['+5d_t'])}) | {'yes' if r['+5d_both'] else 'no'} |")
    head = ["## Worth watching (passed all three checks)", ""]
    head += [f"- {w}" for _, w in sorted(watch, reverse=True)] or ["- none"]
    thin = sorted({f for f in res.feed.unique() if res[res.feed == f].days_on.max() < MIN_DAYS})
    head += ["", "## Feeds with too little history to judge yet", ""] + ([f"- {f}" for f in thin] or ["- none"]) + [""]
    return "\n".join(L[:5] + head + L[5:]), watch, thin


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="print the whole report, not only the short read")
    a = ap.parse_args()
    data = load()
    out, F = build(data)
    res = study(out, F)
    names = names_study(data["rr"])
    text, watch, thin = report(out, res, names)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"edge_study_{dt.date.today()}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    if a.all:
        print(text)
    else:
        print(f"edge study: {len(res)} conditions, {out['day'].notna().sum()} S&P sessions; report in {path}")
        print(f"worth watching: {len(watch)}")
        for _, w in sorted(watch, reverse=True):
            print("  " + w)
        print("too little history yet: " + (", ".join(thin) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
