"""tools/sg_vol.py - SpotGamma Volatility Dashboard, per name, many times a day (Kris 11/24/2026).

Capture: tools/sg_vol_capture.js (capture desk; about every 30 minutes in market hours and once after the close, Kris
11/24: "I want to track the rate of change during the day") saves Downloads/sg_vol_<date>_<time>.json.

  railway run py -m tools.sg_vol --syms               # the line to paste before the capture (indexes + book + stalk)
  railway run py -m tools.sg_vol --ingest-downloads   # load every sg_vol_*.json in Downloads, then file it away
  railway run py -m tools.sg_vol --ingest PATH
  railway run py -m tools.sg_vol --show               # the latest day, one line per name
  railway run py -m tools.sg_vol --show NVDA          # one name's history
  railway run py -m tools.sg_vol --intraday           # today: every name's first, previous and latest read, and the change
  railway run py -m tools.sg_vol --intraday NVDA      # every read of one name, newest first
  railway run py -m tools.sg_vol --ingest PATH --intraday-only   # backfill an old run without touching the daily tables

Tables (migration 148; this tool creates them too):
  sg_vol_term   implied vol by expiry per name, with SpotGamma's normal range (lo, hi) for that expiry
  sg_vol_skew   implied vol by strike for the ~30-day expiry, with the 10/25/50/75/90th percentile bands
  sg_vol_vix    the VIX futures curve (today's and the prior day's quote per contract)
  sg_vol_daily  one line per name per day, worked out from the two above:
                  iv30      implied vol at ~30 days (interpolated on the term structure)
                  iv30_pos  where iv30 sits in SpotGamma's normal range: 0 = bottom, 1 = top, above 1 = over the range
                  front_iv  the nearest expiry at least 2 days out
                  slope     iv at ~90 days minus iv at ~30 days (negative = near-term fear, "backwardation")
                  atm_iv    the skew expiry's implied vol at the money, and atm_pos = where it sits between its
                            10th and 90th percentile (0 to 1)
                  put_skew  iv 5% below the price minus iv 5% above it (bigger = puts bid over calls)
  sg_vol_intraday      the same line as sg_vol_daily, but one per name per CAPTURE (as_of = the time SpotGamma stamped
                       that name's skew read, else the file's captured_at). The daily tables keep only the day's
                       latest run; this one keeps every run, so the change through the day can be read.
  sg_vol_vix_intraday  the VIX futures curve per capture.
All vols are decimals as SpotGamma gives them (0.25 = 25%).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import shutil
import sys

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
RAW_DIR = os.path.join("data", "sg_vol", "raw")
INDEXES = ["SPX", "SPY", "QQQ", "IWM", "NDX", "RUT"]

DDL = """
CREATE TABLE IF NOT EXISTS sg_vol_term (
    trade_date date NOT NULL, sym text NOT NULL, expiry date NOT NULL,
    dte numeric, iv numeric, fiv numeric, lo numeric, hi numeric, captured_at timestamptz,
    PRIMARY KEY (trade_date, sym, expiry)
);
CREATE TABLE IF NOT EXISTS sg_vol_skew (
    trade_date date NOT NULL, sym text NOT NULL, expiry date NOT NULL, strike numeric NOT NULL,
    moneyness numeric, delta numeric, iv numeric, p10 numeric, p25 numeric, median numeric, p75 numeric, p90 numeric,
    min_iv numeric, max_iv numeric, captured_at timestamptz,
    PRIMARY KEY (trade_date, sym, expiry, strike)
);
CREATE TABLE IF NOT EXISTS sg_vol_vix (
    trade_date date NOT NULL, contract text NOT NULL, quote_date date NOT NULL,
    expiry date, close numeric, quote_time timestamptz, captured_at timestamptz,
    PRIMARY KEY (trade_date, contract, quote_date)
);
CREATE TABLE IF NOT EXISTS sg_vol_daily (
    trade_date date NOT NULL, sym text NOT NULL, px numeric,
    iv30 numeric, iv30_lo numeric, iv30_hi numeric, iv30_pos numeric,
    front_iv numeric, front_dte numeric, iv90 numeric, slope numeric,
    skew_expiry date, atm_iv numeric, atm_p10 numeric, atm_median numeric, atm_p90 numeric, atm_pos numeric,
    put_skew numeric, captured_at timestamptz,
    PRIMARY KEY (trade_date, sym)
);
CREATE TABLE IF NOT EXISTS sg_vol_intraday (
    as_of timestamptz NOT NULL, sym text NOT NULL, trade_date date NOT NULL, px numeric,
    iv30 numeric, iv30_lo numeric, iv30_hi numeric, iv30_pos numeric,
    front_iv numeric, front_dte numeric, iv90 numeric, slope numeric,
    skew_expiry date, atm_iv numeric, atm_p10 numeric, atm_median numeric, atm_p90 numeric, atm_pos numeric,
    put_skew numeric, captured_at timestamptz, source_file text,
    PRIMARY KEY (as_of, sym)
);
CREATE INDEX IF NOT EXISTS sg_vol_intraday_sym_day ON sg_vol_intraday (sym, trade_date, as_of);
CREATE TABLE IF NOT EXISTS sg_vol_vix_intraday (
    as_of timestamptz NOT NULL, contract text NOT NULL, quote_date date NOT NULL,
    trade_date date, expiry date, close numeric, quote_time timestamptz, source_file text,
    PRIMARY KEY (as_of, contract, quote_date)
);
"""


def _day(s) -> dt.date:
    """A date from '2026-11-24' or from a full stamp like '2026-11-24T20:31:17.000Z' (intraday runs send the stamp)."""
    return dt.date.fromisoformat(str(s)[:10])


def _ts(s) -> dt.datetime | None:
    """A timezone-aware time from SpotGamma's stamps; a trailing Z is UTC. None when it is only a date or unreadable."""
    s = str(s or "")
    if len(s) <= 10:
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def _d(ms) -> dt.date | None:
    """SpotGamma expiry timestamps are milliseconds; the date is the ET calendar day."""
    try:
        return (dt.datetime.fromtimestamp(float(ms) / 1000, dt.timezone.utc) - dt.timedelta(hours=5)).date()
    except (TypeError, ValueError, OSError):
        return None


def _interp(pts: list[tuple[float, float]], x: float) -> float | None:
    """Straight-line value at x from (x, y) points; clamps to the ends. None if no points."""
    pts = sorted((a, b) for a, b in pts if a is not None and b is not None)
    if not pts:
        return None
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


def _pos(v, lo, hi) -> float | None:
    if v is None or lo is None or hi is None or hi <= lo:
        return None
    return round((v - lo) / (hi - lo), 3)


def summarize(sym: str, term: dict | None, skew: dict | None) -> dict:
    out = {"sym": sym, "px": (skew or {}).get("px")}
    rows = [r for r in (term or {}).get("rows", []) if r and r[0] is not None and r[2] is not None]
    if rows:
        iv = [(r[0], r[2]) for r in rows]
        out["iv30"] = _interp(iv, 30)
        out["iv30_lo"] = _interp([(r[0], r[4]) for r in rows], 30)
        out["iv30_hi"] = _interp([(r[0], r[5]) for r in rows], 30)
        out["iv30_pos"] = _pos(out["iv30"], out["iv30_lo"], out["iv30_hi"])
        front = next((r for r in sorted(rows) if r[0] >= 2), None)
        if front:
            out["front_iv"], out["front_dte"] = front[2], front[0]
        if max(r[0] for r in rows) >= 60:
            out["iv90"] = _interp(iv, 90)
            if out.get("iv30") is not None and out["iv90"] is not None:
                out["slope"] = round(out["iv90"] - out["iv30"], 5)
    srows = [r for r in (skew or {}).get("rows", []) if r and r[1] is not None and r[3] is not None]
    if srows:
        out["skew_expiry"] = _d(skew.get("expiry"))
        by_m = lambda i: [(r[1], r[i]) for r in srows]  # noqa: E731
        out["atm_iv"] = _interp(by_m(3), 1.0)
        out["atm_p10"], out["atm_median"], out["atm_p90"] = _interp(by_m(4), 1.0), _interp(by_m(6), 1.0), _interp(by_m(8), 1.0)
        out["atm_pos"] = _pos(out["atm_iv"], out["atm_p10"], out["atm_p90"])
        ms = [r[1] for r in srows]
        if min(ms) <= 0.955 and max(ms) >= 1.045:
            out["put_skew"] = round(_interp(by_m(3), 0.95) - _interp(by_m(3), 1.05), 5)
    return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in out.items()}


def _upsert(cur, table: str, keys: list[str], cols: list[str], rows: list[list]) -> None:
    if not rows:
        return
    sets = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in keys)
    cur.executemany(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
        f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET {sets}", rows)


DAILY_COLS = ["trade_date", "sym", "px", "iv30", "iv30_lo", "iv30_hi", "iv30_pos", "front_iv", "front_dte", "iv90", "slope",
              "skew_expiry", "atm_iv", "atm_p10", "atm_median", "atm_p90", "atm_pos", "put_skew", "captured_at"]


INTRA_COLS = ["as_of", "sym", "trade_date"] + DAILY_COLS[2:] + ["source_file"]


def ingest(path: str, intraday_only: bool = False) -> dict:
    """Load one capture file. Every run goes into the intraday tables (one row per name per capture); the daily
    tables are overwritten with this run unless intraday_only (use that to backfill an older run of the same day)."""
    import db_pg
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if d.get("kind") != "sg_vol":
        raise ValueError("not an sg_vol capture")
    cap = _ts(d.get("captured_at")) or _ts(d.get("started"))
    day = _day(d["day"])
    src = os.path.basename(path)
    term_rows, skew_rows, daily_rows, vix_rows, intra_rows, vix_intra = [], [], [], [], [], []
    for sym, v in (d.get("syms") or {}).items():
        term, skew = v.get("term"), v.get("skew")
        td = _day((term or skew or {}).get("trade_date") or d["day"])
        as_of = _ts((skew or {}).get("trade_date")) or _ts((term or {}).get("trade_date")) or cap
        seen = set()
        for r in (term or {}).get("rows", []):
            ex = _d(r[1])
            if ex is None or ex in seen or r[2] is None:
                continue
            seen.add(ex)
            term_rows.append([td, sym, ex, r[0], r[2], r[3], r[4], r[5], cap])
        if skew:
            ex, seen = _d(skew.get("expiry")), set()
            for r in skew.get("rows", []):
                if ex is None or r[0] is None or r[0] in seen:
                    continue
                seen.add(r[0])
                skew_rows.append([td, sym, ex, *r[:11], cap])
        s = summarize(sym, term, skew)
        daily_rows.append([td] + [s.get(c) for c in DAILY_COLS[1:-1]] + [cap])
        if as_of is not None:
            intra_rows.append([as_of, sym, td] + [s.get(c) for c in DAILY_COLS[2:-1]] + [cap, src])
    seen = set()
    for r in d.get("vix") or []:
        if not r or not r[0] or not r[2] or (r[0], r[2]) in seen:
            continue
        seen.add((r[0], r[2]))
        vix_rows.append([day, r[0], r[2], r[1], r[3], r[4], cap])
        if cap is not None:
            vix_intra.append([cap, r[0], r[2], day, r[1], r[3], r[4], src])
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        if not intraday_only:
            _upsert(cur, "sg_vol_term", ["trade_date", "sym", "expiry"],
                    ["trade_date", "sym", "expiry", "dte", "iv", "fiv", "lo", "hi", "captured_at"], term_rows)
            _upsert(cur, "sg_vol_skew", ["trade_date", "sym", "expiry", "strike"],
                    ["trade_date", "sym", "expiry", "strike", "moneyness", "delta", "iv", "p10", "p25", "median", "p75",
                     "p90", "min_iv", "max_iv", "captured_at"], skew_rows)
            _upsert(cur, "sg_vol_vix", ["trade_date", "contract", "quote_date"],
                    ["trade_date", "contract", "quote_date", "expiry", "close", "quote_time", "captured_at"], vix_rows)
            _upsert(cur, "sg_vol_daily", ["trade_date", "sym"], DAILY_COLS, daily_rows)
        _upsert(cur, "sg_vol_intraday", ["as_of", "sym"], INTRA_COLS, intra_rows)
        _upsert(cur, "sg_vol_vix_intraday", ["as_of", "contract", "quote_date"],
                ["as_of", "contract", "quote_date", "trade_date", "expiry", "close", "quote_time", "source_file"], vix_intra)
        conn.commit()
    return {"file": src, "names": len(daily_rows), "term": len(term_rows), "skew": len(skew_rows), "vix": len(vix_rows),
            "intraday": len(intra_rows), "daily_tables": "skipped" if intraday_only else "updated",
            "failed": d.get("failed") or []}


def ingest_downloads() -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(DOWNLOADS, "sg_vol_*.json"))):
        try:
            r = ingest(p)
        except Exception as e:  # noqa: BLE001 - keep the file, report, go on
            out.append({"file": os.path.basename(p), "error": str(e)[:160]})
            continue
        day = os.path.basename(p)[7:15]
        os.makedirs(os.path.join(RAW_DIR, day), exist_ok=True)
        shutil.move(p, os.path.join(RAW_DIR, day, os.path.basename(p)))
        out.append(r)
    return out


def syms() -> list[str]:
    """Indexes first, then the book (same list the daily SpotGamma run uses), then the desk's watch list."""
    out = list(INDEXES)
    try:
        from tools.sg_pages import book_syms
        out += book_syms()
    except Exception:  # noqa: BLE001
        pass
    try:
        import csv
        with open(os.path.join("data", "stalk", "desk_watch.csv"), encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                t = (r.get("ticker") or r.get("symbol") or r.get("sym") or "").strip().upper()
                if t:
                    out.append(t)
    except OSError:
        pass
    seen, uniq = set(), []
    for s in out:
        s = s.upper()
        if s.isalnum() and s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def _pct(v) -> str:
    return "-" if v is None else f"{float(v) * 100:.1f}%"


def show(sym: str | None = None) -> str:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
        if sym:
            cur.execute("SELECT trade_date, sym, px, iv30, iv30_pos, front_iv, slope, atm_iv, atm_pos, put_skew FROM sg_vol_daily "
                        "WHERE sym = %s ORDER BY trade_date DESC LIMIT 30", (sym.upper(),))
        else:
            cur.execute("SELECT trade_date, sym, px, iv30, iv30_pos, front_iv, slope, atm_iv, atm_pos, put_skew FROM sg_vol_daily "
                        "WHERE trade_date = (SELECT max(trade_date) FROM sg_vol_daily) ORDER BY iv30_pos DESC NULLS LAST")
        rows = cur.fetchall()
        cur.execute("SELECT contract, expiry, close FROM sg_vol_vix WHERE trade_date = (SELECT max(trade_date) FROM sg_vol_vix) "
                    "AND quote_date = trade_date ORDER BY expiry")
        vix = cur.fetchall()
    if not rows:
        return "sg_vol_daily: empty"
    lines = ["date        sym    price     iv30   in range  front   90d-30d  atm iv  atm in band  put skew"]
    for d, s, px, iv30, pos, fr, sl, atm, apos, ps in rows:
        lines.append(f"{d}  {s:<6}{float(px or 0):>8.2f}  {_pct(iv30):>6}  {('-' if pos is None else f'{float(pos):.2f}'):>7}  "
                     f"{_pct(fr):>6}  {_pct(sl):>7}  {_pct(atm):>6}  {('-' if apos is None else f'{float(apos):.2f}'):>10}  {_pct(ps):>8}")
    if vix and not sym:
        lines.append("VIX futures: " + ", ".join(f"{e:%b %d} {float(c):.2f}" for _, e, c in vix if e and c is not None))
    return "\n".join(lines)


def _et(t) -> str:
    """Clock time in New York for a stored stamp."""
    try:
        import zoneinfo
        return t.astimezone(zoneinfo.ZoneInfo("America/New_York")).strftime("%H:%M")
    except Exception:  # noqa: BLE001 - no tz database on this machine: EST is UTC-5
        return (t.astimezone(dt.timezone.utc) - dt.timedelta(hours=5)).strftime("%H:%M")


def _num(v, nd=2) -> str:
    return "-" if v is None else f"{float(v):.{nd}f}"


def show_intraday(sym: str | None = None) -> str:
    """The latest day's reads. No name: one line per name (first, previous and latest read, change since the first).
    A name: every read of it, newest first."""
    import db_pg
    cols = "as_of, sym, px, iv30, iv30_pos, front_iv, slope, atm_iv, atm_pos, put_skew"
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
        if sym:
            cur.execute(f"SELECT {cols} FROM sg_vol_intraday WHERE sym = %s ORDER BY as_of DESC LIMIT 60", (sym.upper(),))
        else:
            cur.execute(f"SELECT {cols} FROM sg_vol_intraday "
                        "WHERE trade_date = (SELECT max(trade_date) FROM sg_vol_intraday) ORDER BY sym, as_of")
        rows = cur.fetchall()
    if not rows:
        return "sg_vol_intraday: empty"
    if sym:
        lines = ["date  ET     sym    price     iv30   in range  front   90d-30d  atm iv  atm in band  put skew"]
        for t, s, px, iv30, pos, fr, sl, atm, apos, ps in rows:
            lines.append(f"{t:%m/%d} {_et(t)}  {s:<6}{float(px or 0):>8.2f}  {_pct(iv30):>6}  {_num(pos):>7}  {_pct(fr):>6}  "
                         f"{_pct(sl):>7}  {_pct(atm):>6}  {_num(apos):>10}  {_pct(ps):>8}")
        return "\n".join(lines)
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r[1], []).append(r)
    out = []
    for s, rs in by.items():
        first, last = rs[0], rs[-1]
        prev = rs[-2] if len(rs) > 1 else None
        fl = lambda v: None if v is None else float(v)  # noqa: E731
        d_iv = None if fl(first[7]) is None or fl(last[7]) is None else (fl(last[7]) - fl(first[7])) * 100
        d_px = None if not fl(first[2]) or not fl(last[2]) else (fl(last[2]) / fl(first[2]) - 1) * 100
        d_sk = None if fl(first[9]) is None or fl(last[9]) is None else (fl(last[9]) - fl(first[9])) * 100
        if len(rs) == 1:  # one read is not a change
            d_iv = d_px = d_sk = None
        out.append((abs(d_iv or 0), s, len(rs), first, prev, last, d_iv, d_px, d_sk))
    out.sort(key=lambda x: (-x[0], x[1]))
    lines = ["sym    reads  first  latest  price chg   atm iv: first > prev > latest   chg pts  in band now  put skew now (chg pts)"]
    for _, s, n, first, prev, last, d_iv, d_px, d_sk in out:
        lines.append(
            f"{s:<6}{n:>5}  {_et(first[0])}  {_et(last[0])}   {('-' if d_px is None else f'{d_px:+.2f}%'):>8}   "
            f"{_pct(first[7]):>6} > {(_pct(prev[7]) if prev else '-'):>6} > {_pct(last[7]):>6}   "
            f"{('-' if d_iv is None else f'{d_iv:+.1f}'):>6}   {_num(last[8]):>10}   "
            f"{_pct(last[9]):>6} ({'-' if d_sk is None else f'{d_sk:+.1f}'})")
    return "\n".join(lines)


def _cli() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ingest")
    g.add_argument("--ingest-downloads", action="store_true")
    g.add_argument("--syms", action="store_true")
    g.add_argument("--show", nargs="?", const="", metavar="SYM")
    g.add_argument("--intraday", nargs="?", const="", metavar="SYM")
    ap.add_argument("--intraday-only", action="store_true", help="with --ingest: load the intraday tables only")
    a = ap.parse_args()
    if a.syms:
        print("window.__sgVolSyms = " + json.dumps(syms()))
    elif a.ingest:
        print(ingest(a.ingest, intraday_only=a.intraday_only))
    elif a.ingest_downloads:
        res = ingest_downloads()
        for r in res:
            print(r)
        if not res:
            print("no sg_vol_*.json in Downloads")
    elif a.intraday is not None:
        print(show_intraday(a.intraday or None))
    else:
        print(show(a.show or None))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
