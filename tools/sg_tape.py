"""tools/sg_tape.py - SpotGamma Tape, read every 15 minutes (Kris 11/15/2026).

Capture (capture desk, Kris's FIRST Chrome, every 15 min from 9:30 to 16:15 ET on market days):
  1. Reload the SpotGamma Tape tab (https://dashboard.spotgamma.com/tape), wait ~8 s.
  2. Paste tools/sg_tape_capture.js into the javascript tool; click its green "SG TAPE SAVE" button.
     That downloads Downloads/sg_tape_<YYYYMMDD>_<HHMM>.json.
  3. railway run py -m tools.sg_tape --ingest-downloads
     (loads every sg_tape_*.json in Downloads, then moves it to data/sg_tape/raw/<YYYYMMDD>/)

Read (desk, at check-ins):  railway run py -m tools.sg_tape --read
  The latest read vs the one before it in the same session: premium and its put share over the window,
  put vs call delta added, the day's put/call ratios, the grid prints' aggressor split (buy vs sell
  premium, sweeps, blocks), the largest trades, and any book name on the tape.

Tables (migration 141): sg_tape_reads (one row per read: the day's running put/call totals + top lists),
sg_tape_prints (each print, deduped on SpotGamma's tns index).
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
RAW_DIR = os.path.join("data", "sg_tape", "raw")


def _ts(ms) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, tz=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def parse(d: dict) -> tuple[dict, list[tuple]]:
    """Pure: (read row, print rows) from one capture file."""
    f = d.get("flow") or {}
    g = lambda m, s: (f.get(m) or {}).get(s)
    read = {"read_at": d["read_at"], "stamp": d.get("stamp"),
            "vol_put": g("volume", "put"), "vol_call": g("volume", "call"),
            "prem_put": g("premium", "put"), "prem_call": g("premium", "call"),
            "delta_put": g("delta", "put"), "delta_call": g("delta", "call"),
            "gamma_put": g("gamma", "put"), "gamma_call": g("gamma", "call"),
            "vega_put": g("vega", "put"), "vega_call": g("vega", "call"),
            "n_prints": len(d.get("prints") or []), "lists": json.dumps(d.get("lists") or {})}
    rows = []
    for p in d.get("prints") or []:
        ts = _ts(p.get("ts"))
        if not p.get("tns") or ts is None or not p.get("sym"):
            continue
        rows.append((p["tns"], ts, p["sym"], p.get("aggressor"), p.get("side"), p.get("put"), p.get("strike"),
                     _ts(p.get("expiry")), p.get("size"), p.get("premium"), p.get("spot"), p.get("price"),
                     p.get("bid"), p.get("ask"), p.get("iv"), p.get("delta"), p.get("gamma"), p.get("vega"),
                     p.get("vol"), p.get("oi"), p.get("block"), p.get("sweep"), p.get("spread"), p.get("cross"),
                     p.get("retail"), p.get("next_exp"), p.get("cond"), d["read_at"]))
    return read, rows


def ingest(path: str) -> dict:
    import db_pg
    with open(path, encoding="utf-8") as fh:
        read, rows = parse(json.load(fh))
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cols = list(read)
        cur.execute(f"INSERT INTO sg_tape_reads ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
                    "ON CONFLICT (read_at) DO NOTHING", [read[c] for c in cols])
        new_read = cur.rowcount
        cur.executemany(
            "INSERT INTO sg_tape_prints (tns, ts, sym, aggressor, side, is_put, strike, expiry, size, premium, spot, "
            "price, bid, ask, iv, delta, gamma, vega, day_vol, prev_oi, is_block, is_sweep, is_spread, is_cross, "
            "is_retail, next_exp, cond, read_at) VALUES (" + ",".join(["%s"] * 28) + ") ON CONFLICT (tns) DO NOTHING",
            rows)
        conn.commit()
    return {"file": os.path.basename(path), "read": new_read, "prints": len(rows)}


def ingest_downloads() -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(DOWNLOADS, "sg_tape_*.json"))):
        try:
            r = ingest(p)
        except Exception as e:  # noqa: BLE001 - keep the file, report, go on
            out.append({"file": os.path.basename(p), "error": str(e)[:120]})
            continue
        day = os.path.basename(p)[8:16]
        os.makedirs(os.path.join(RAW_DIR, day), exist_ok=True)
        shutil.move(p, os.path.join(RAW_DIR, day, os.path.basename(p)))
        out.append(r)
    return out


def _m(x) -> str:
    x = float(x or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:,.1f}{suf}"
    return f"${x:,.0f}"


def summarize(cur_r: dict, prev: dict | None, prints: list[dict], book: set[str]) -> str:
    """Pure: the 15-minute read as text."""
    t = dt.datetime.fromisoformat(str(cur_r["read_at"]).replace("Z", "+00:00"))
    from zoneinfo import ZoneInfo
    et = t.astimezone(ZoneInfo("America/New_York"))
    f = lambda k: float(cur_r.get(k) or 0)
    lines = [f"SG TAPE {et:%m/%d %H:%M} ET"]
    pp, pc = f("prem_put"), f("prem_call")
    vp, vc = f("vol_put"), f("vol_call")
    lines.append(f"Day so far: premium {_m(pp + pc)} (put/call {pp / pc:.2f})" if pc else "Day so far: no premium yet")
    if vc:
        lines[-1] += f", volume put/call {vp / vc:.2f}, delta puts {_m(f('delta_put'))} vs calls {_m(f('delta_call'))}"
    reset = prev is not None and float(prev.get("prem_put") or 0) + float(prev.get("prem_call") or 0) > pp + pc
    if prev and not reset:
        pt = dt.datetime.fromisoformat(str(prev["read_at"]).replace("Z", "+00:00"))
        dp = pp - float(prev.get("prem_put") or 0)
        dc = pc - float(prev.get("prem_call") or 0)
        mins = round((t - pt).total_seconds() / 60)
        share = dp / (dp + dc) if (dp + dc) else 0
        lines.append(f"Last {mins} min: premium {_m(dp + dc)}, {share:.0%} puts; delta added puts "
                     f"{_m(f('delta_put') - float(prev.get('delta_put') or 0))} vs calls "
                     f"{_m(f('delta_call') - float(prev.get('delta_call') or 0))}")
    elif reset:
        lines.append("New session (totals reset since the prior read).")
    if prints:
        buy = sum(float(p["premium"] or 0) for p in prints if p["aggressor"] == "buy")
        sell = sum(float(p["premium"] or 0) for p in prints if p["aggressor"] == "sell")
        sw = sum(float(p["premium"] or 0) for p in prints if p["is_sweep"])
        bl = sum(float(p["premium"] or 0) for p in prints if p["is_block"])
        lines.append(f"Grid ({len(prints)} latest prints): buy-aggressor {_m(buy)} vs sell {_m(sell)}; "
                     f"sweeps {_m(sw)}, blocks {_m(bl)}")
    lists = cur_r.get("lists") or {}
    if isinstance(lists, str):
        lists = json.loads(lists)
    big = (lists.get("premium") or [])[:6]
    if big:
        lines.append("Largest trades: " + "; ".join(
            f"{b['underlying'].split(':')[0]} {b['strike']:g}{'P' if b.get('is_put') else 'C'} "
            f"{str(b.get('expiry', ''))[:10]} {_m(b.get('premium'))}" for b in big))
    hits = sorted({b["underlying"] for b in lists.get("premium") or []} & book
                  | {p["sym"] for p in prints} & book)
    if hits:
        lines.append("Book names on the tape: " + ", ".join(hits))
    return "\n".join(lines)


def read_latest(window: int | None = None) -> str:
    """The latest read vs the one before it, or vs the read about `window` minutes earlier (desk glances: 30)."""
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM sg_tape_reads ORDER BY read_at DESC LIMIT 1")
        cols = [c[0] for c in cur.description]
        rs = [dict(zip(cols, r)) for r in cur.fetchall()]
        if not rs:
            return "no tape reads yet"
        if window:
            cur.execute("SELECT * FROM sg_tape_reads WHERE read_at <= %s - make_interval(mins => %s) + interval '3 minutes' "
                        "ORDER BY read_at DESC LIMIT 1", (rs[0]["read_at"], window))
        else:
            cur.execute("SELECT * FROM sg_tape_reads WHERE read_at < %s ORDER BY read_at DESC LIMIT 1", (rs[0]["read_at"],))
        rs += [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("SELECT sym, aggressor, premium, is_sweep, is_block FROM sg_tape_prints WHERE read_at = %s",
                    (rs[0]["read_at"],))
        prints = [dict(zip(["sym", "aggressor", "premium", "is_sweep", "is_block"], r)) for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT symbol FROM book_positions WHERE snapshot_date = "
                    "(SELECT max(snapshot_date) FROM book_positions)")
        book = {r[0] for r in cur.fetchall()}
    return summarize(rs[0], rs[1] if len(rs) > 1 else None, prints, book)


def _cli() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ingest")
    g.add_argument("--ingest-downloads", action="store_true")
    g.add_argument("--read", action="store_true")
    ap.add_argument("--window", type=int, help="with --read: compare with the read about N minutes earlier")
    a = ap.parse_args()
    if a.ingest:
        print(ingest(a.ingest))
    elif a.ingest_downloads:
        for r in ingest_downloads():
            print(r)
    else:
        print(read_latest(a.window))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.exit(_cli())
