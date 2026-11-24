"""tools/sg_watch_load.py - load the SpotGamma watcher's TRACE/HIRO reads into the bot (Kris 11/24/2026).

The watcher chat writes one line every 10 minutes to data/sg_watch/watch_<date>.csv and one line a day to
data/sg_watch/day_reads.csv. Those files stay on the laptop. This loads them into two tables so the history
can be queried and tested for patterns:
  sg_watch_reads  one row per read (date + time), numbers parsed, the free-text notes kept as written
  sg_watch_days   one row per day (the day read)

  railway run py -m tools.sg_watch_load            # every file in data/sg_watch (safe to re-run)
  railway run py -m tools.sg_watch_load --status   # rows per day

Re-running replaces a row with the file's current line, so a corrected file corrects the table.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import os
import re
import sys

WATCH_DIR = os.path.join("data", "sg_watch")

DDL = """
CREATE TABLE IF NOT EXISTS sg_watch_reads (
    read_date date NOT NULL,
    read_time text NOT NULL,
    read_at timestamp,
    is_setup boolean NOT NULL DEFAULT false,
    spx numeric,
    vs_vol_trigger text,
    stability numeric,
    gamma_zone text,
    floor_below numeric,
    ceiling_above numeric,
    hiro_raw text,
    hiro_usd numeric,
    hiro_30m text,
    charm_pin numeric,
    alerts text,
    test_below numeric,
    balance numeric,
    test_above numeric,
    drift text,
    hiro_vs_map text,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (read_date, read_time)
);
CREATE TABLE IF NOT EXISTS sg_watch_days (
    read_date date PRIMARY KEY,
    red_reads int,
    total_reads int,
    hiro_open_raw text,
    hiro_open_usd numeric,
    hiro_close_raw text,
    hiro_close_usd numeric,
    spx_close numeric,
    below_vol_trigger text,
    below_put_wall text,
    verdict text,
    in_a_row int,
    loaded_at timestamptz NOT NULL DEFAULT now()
);
"""

_MULT = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def num(s: str | None) -> float | None:
    """'7,213' -> 7213.0 ; blank or text -> None."""
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def money(s: str | None) -> float | None:
    """'260M' -> 260000000.0 ; '-8.9B' -> -8900000000.0 ; blank -> None."""
    m = re.fullmatch(r"\s*\$?(-?\d+(?:\.\d+)?)\s*([KMBT]?)\s*", (s or "").replace(",", ""), re.I)
    if not m:
        return None
    return float(m.group(1)) * _MULT.get(m.group(2).upper(), 1)


def _txt(s: str | None) -> str | None:
    s = (s or "").strip()
    return s or None


def parse_watch(path: str) -> list[dict]:
    day = dt.date.fromisoformat(re.search(r"watch_(\d{4}-\d{2}-\d{2})\.csv$", path).group(1))
    out = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            t = (r.get("time") or "").strip()
            if not t:
                continue
            hm = re.match(r"(\d{1,2}):(\d{2})", t)
            at = dt.datetime.combine(day, dt.time(int(hm.group(1)), int(hm.group(2)))) if hm else None
            out.append({
                "read_date": day, "read_time": t, "read_at": at, "is_setup": "SETUP" in t.upper(),
                "spx": num(r.get("spx")), "vs_vol_trigger": _txt(r.get("vs_vol_trigger")),
                "stability": num(r.get("stability")), "gamma_zone": _txt(r.get("gamma_zone")),
                "floor_below": num(r.get("floor_below")), "ceiling_above": num(r.get("ceiling_above")),
                "hiro_raw": _txt(r.get("hiro")), "hiro_usd": money(r.get("hiro")),
                "hiro_30m": _txt(r.get("hiro_30m")), "charm_pin": num(r.get("charm_pin")),
                "alerts": _txt(r.get("alerts")), "test_below": num(r.get("test_below")),
                "balance": num(r.get("balance")), "test_above": num(r.get("test_above")),
                "drift": _txt(r.get("drift")), "hiro_vs_map": _txt(r.get("hiro_vs_map")),
            })
    return out


def parse_days(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                day = dt.date.fromisoformat((r.get("date") or "").strip())
            except ValueError:
                continue
            n = lambda k: (int(v) if (v := num(r.get(k))) is not None else None)  # noqa: E731
            out.append({
                "read_date": day, "red_reads": n("red_reads"), "total_reads": n("total_reads"),
                "hiro_open_raw": _txt(r.get("hiro_open")), "hiro_open_usd": money(r.get("hiro_open")),
                "hiro_close_raw": _txt(r.get("hiro_close")), "hiro_close_usd": money(r.get("hiro_close")),
                "spx_close": num(r.get("spx_close")), "below_vol_trigger": _txt(r.get("below_vol_trigger")),
                "below_put_wall": _txt(r.get("below_put_wall")), "verdict": _txt(r.get("verdict")),
                "in_a_row": n("in_a_row"),
            })
    return out


def _upsert(cur, table: str, keys: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    sets = ",".join(f"{c}=EXCLUDED.{c}" for c in cols if c not in keys) + ",loaded_at=now()"
    cur.executemany(
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))}) "
        f"ON CONFLICT ({','.join(keys)}) DO UPDATE SET {sets}",
        [[r[c] for c in cols] for r in rows])


def load() -> dict:
    import db_pg
    reads: list[dict] = []
    for p in sorted(glob.glob(os.path.join(WATCH_DIR, "watch_*.csv"))):
        reads.extend(parse_watch(p))
    days_path = os.path.join(WATCH_DIR, "day_reads.csv")
    days = parse_days(days_path) if os.path.exists(days_path) else []
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        _upsert(cur, "sg_watch_reads", ["read_date", "read_time"], reads)
        _upsert(cur, "sg_watch_days", ["read_date"], days)
        conn.commit()
    return {"reads": len(reads), "days": len(days)}


def status() -> str:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
        cur.execute("SELECT r.read_date, count(*), min(r.read_time), max(r.read_time), d.verdict "
                    "FROM sg_watch_reads r LEFT JOIN sg_watch_days d USING (read_date) "
                    "GROUP BY r.read_date, d.verdict ORDER BY r.read_date DESC LIMIT 15")
        rows = cur.fetchall()
    if not rows:
        return "sg_watch_reads: empty"
    return "\n".join(f"{d}  {n} reads  {a} to {b}  day read: {v or '-'}" for d, n, a, b, v in rows)


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        print(status())
        return 0
    r = load()
    print(f"sg_watch: loaded {r['reads']} reads, {r['days']} day reads")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
