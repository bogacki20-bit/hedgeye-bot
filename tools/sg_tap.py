"""tools/sg_tap.py - file and index the raw SpotGamma captures made by tools/sg_tap.js (Kris 11/24/2026:
"I want to capture all the data in all the products ... a robust data set that we can clean up").

tools/sg_tap.js keeps every answer a SpotGamma page loads, whole, one per line, in
Downloads/sg_tap_<YYYYMMDD>_<HHMMSS>.jsonl.gz. This tool moves each file to data/sg_tap/<date>/ (local only, never
committed; not yet copied anywhere else: the nightly backup covers only the database) and writes one index row per answer, so any feed can be found by
name, symbol and time without opening every file. The raw file is the data set: typed tables are built from it later.

    railway run py -m tools.sg_tap --ingest-downloads     # file + index every sg_tap_* file in Downloads
    railway run py -m tools.sg_tap --ingest PATH
    railway run py -m tools.sg_tap --show                 # today: answers and size per feed, first and last read
    railway run py -m tools.sg_tap --show 2026-11-24
    py -m tools.sg_tap --peek PATH [/v4/latestHiro]       # no database: what one file holds (per feed, or one feed's shape)

Table (migration 150; this tool creates it too):
  sg_tap_index  one row per kept answer: captured_at, host, path (the feed), q (its parameters), sym (worked out
                from q), page (the dashboard page that asked), status, ctype, b64 (binary feeds are kept base64),
                bytes, file, line.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import gzip
import json
import os
import shutil
import sys
from urllib.parse import parse_qs

DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads")
RAW_DIR = os.path.join("data", "sg_tap")

DDL = """
CREATE TABLE IF NOT EXISTS sg_tap_index (
    captured_at timestamptz NOT NULL, host text NOT NULL, path text NOT NULL, q text NOT NULL DEFAULT '',
    sym text, page text, status int, ctype text, b64 boolean, bytes int, file text NOT NULL, line int NOT NULL,
    PRIMARY KEY (file, line)
);
CREATE INDEX IF NOT EXISTS sg_tap_index_path_time ON sg_tap_index (path, captured_at);
CREATE INDEX IF NOT EXISTS sg_tap_index_sym_time ON sg_tap_index (sym, captured_at);
"""


def _open(path: str):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, "r", encoding="utf-8")


def _records(path: str):
    with _open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if line:
                yield i, json.loads(line)


def _sym(q: str) -> str | None:
    """The symbol a feed was asked for, when it names exactly one (sym=, symbol=, ivol_sym=, a one-name syms=)."""
    p = parse_qs(q, keep_blank_values=True)
    for k in ("sym", "symbol", "ivol_sym"):
        if p.get(k):
            return p[k][0][:40]
    if p.get("syms"):
        s = p["syms"][0].replace("-", ",").split(",")
        return s[0][:40] if len(s) == 1 else None
    return None


def _et_day(ts: str) -> str:
    from zoneinfo import ZoneInfo
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def _file_away(path: str) -> str:
    """Move a capture from Downloads to data/sg_tap/<ET date of its first answer>/. Returns the new path."""
    first = next(_records(path), None)
    day = _et_day(first[1]["t"]) if first else dt.date.today().isoformat()
    dest_dir = os.path.join(RAW_DIR, day)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.abspath(path) != os.path.abspath(dest):
        shutil.move(path, dest)
    return dest


def ingest(path: str) -> dict:
    import db_pg
    db_pg._load_dotenv_fallback()
    dest = _file_away(path)
    rel = dest.replace("\\", "/")
    n, size, feeds = 0, 0, set()
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(DDL)
        for i, r in _records(dest):
            body = r.get("body") or ""
            cur.execute("INSERT INTO sg_tap_index (captured_at, host, path, q, sym, page, status, ctype, b64, bytes, file, line) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (file, line) DO NOTHING",
                        (r["t"], r.get("host") or "", r["path"], r.get("q") or "", _sym(r.get("q") or ""), r.get("page"),
                         r.get("status"), (r.get("ctype") or "")[:80], bool(r.get("b64")), len(body), rel, i))
            n += 1
            size += len(body)
            feeds.add(r["path"])
        c.commit()
    return {"file": rel, "answers": n, "feeds": len(feeds), "raw_mb": round(size / 1048576, 1),
            "disk_mb": round(os.path.getsize(dest) / 1048576, 1)}


def ingest_downloads() -> list[dict]:
    out = []
    for p in sorted(glob.glob(os.path.join(DOWNLOADS, "sg_tap_*.jsonl*"))):
        if p.endswith(".crdownload"):
            continue
        out.append(ingest(p))
    return out


def show(day: str | None = None) -> str:
    import db_pg
    db_pg._load_dotenv_fallback()
    day = day or dt.date.today().isoformat()
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(DDL)
        cur.execute("""SELECT path, count(*), count(DISTINCT sym), sum(bytes),
                              to_char(min(captured_at AT TIME ZONE 'America/New_York'), 'HH24:MI'),
                              to_char(max(captured_at AT TIME ZONE 'America/New_York'), 'HH24:MI')
                       FROM sg_tap_index WHERE (captured_at AT TIME ZONE 'America/New_York')::date = %s
                       GROUP BY 1 ORDER BY 4 DESC""", (day,))
        rows = cur.fetchall()
    if not rows:
        return f"sg_tap {day}: nothing kept"
    lines = [f"sg_tap {day}: {sum(r[1] for r in rows)} answers, {len(rows)} feeds, {sum(r[3] for r in rows) / 1048576:.1f} MB raw",
             f"{'feed':42s} {'reads':>6s} {'names':>6s} {'MB':>8s}  first  last"]
    for p, n, syms, b, a, z in rows:
        lines.append(f"{p[:42]:42s} {n:6d} {syms:6d} {b / 1048576:8.2f}  {a}  {z}")
    return "\n".join(lines)


def _shape(x, depth: int = 0) -> str:
    if isinstance(x, dict):
        ks = list(x.keys())
        s = "{%d keys: %s%s}" % (len(ks), ", ".join(map(str, ks[:16])), " ..." if len(ks) > 16 else "")
        return s + (" -> " + _shape(x[ks[0]], depth + 1) if ks and depth < 3 else "")
    if isinstance(x, list):
        return "[%d] " % len(x) + (_shape(x[0], depth + 1) if x and depth < 3 else "")
    return repr(x)[:40]


def peek(path: str, feed: str | None = None) -> str:
    """What one raw file holds, without the database: per feed, or the shape of one feed's biggest answer."""
    by: dict = {}
    best = None
    for _, r in _records(path):
        b = by.setdefault(r["path"], [0, 0, r.get("b64")])
        b[0] += 1
        b[1] += len(r.get("body") or "")
        if feed and r["path"] == feed and (best is None or len(r["body"]) > len(best["body"])):
            best = r
    if feed:
        if not best:
            return f"{feed}: not in this file"
        if best.get("b64"):
            return f"{feed} ?{best['q'][:80]}: binary ({best['ctype']}), {len(best['body']) // 1024} KB base64, kept whole"
        return f"{feed} ?{best['q'][:80]} | {len(best['body']) // 1024} KB\n  " + _shape(json.loads(best["body"]))
    lines = [f"{os.path.basename(path)}: {sum(v[0] for v in by.values())} answers, {len(by)} feeds"]
    for p, (n, b, bin_) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        lines.append(f"  {p[:42]:42s} x{n:<4d} {b / 1024:9.0f} KB{'  (binary)' if bin_ else ''}")
    return "\n".join(lines)


def _cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ingest")
    ap.add_argument("--ingest-downloads", action="store_true")
    ap.add_argument("--show", nargs="?", const="")
    ap.add_argument("--peek", nargs="+")
    a = ap.parse_args()
    if a.peek:
        print(peek(a.peek[0], a.peek[1] if len(a.peek) > 1 else None))
    if a.ingest:
        print(ingest(a.ingest))
    if a.ingest_downloads:
        res = ingest_downloads()
        print("\n".join(map(str, res)) if res else "no sg_tap_* files in Downloads")
    if a.show is not None:
        print(show(a.show or None))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
