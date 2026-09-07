"""tools/parse_rerank.py — T6 step 1: parse the Daily ETF Re-Rank mails
into hedgeye_rerank.

Rank = order in the "Macro ETFs by Rank:" line (rank 1 = FDRXX cash,
almost always). Commentary moves parsed into move_bps / action:
  "Bought 100bps GLD, AAAU, and FXB"  -> +100 each
  "Sold 50bps URA" / "sold 150bps of FXA" -> negative
  "sold all EWM and EZA"              -> action='sold_all'
  "Added XLU, GDXJ and EPHE at my mins" -> action='add_min'
Duplicate mails for one note_date collapse (first parse wins).

    py tools/parse_rerank.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import db_pg  # noqa: E402
db_pg._load_dotenv_fallback()
from psycopg2.extras import execute_values  # noqa: E402
from parse_hedgeye_archive import body_text, ARCH  # noqa: E402

TICK = re.compile(r"^[A-Z]{2,6}$")
RANKLINE = re.compile(r"Macro ETFs by Rank\s*:\s*(.*?)Keith(?:'|’)s Commentary",
                      re.S)
COMM = re.compile(r"Keith(?:'|’)s Commentary\s*:\s*\"?(.*?)\"", re.S)
BPS = re.compile(r"(bought|sold|added)\s+(\d{2,4})\s*bps(?:\s+of)?\s+"
                 r"([A-Z][A-Z0-9,\sand]*?)(?=[.\"”]|$)", re.I)
SOLD_ALL = re.compile(r"sold\s+(?:all|out of)\s+"
                      r"([A-Z][A-Z0-9,\sand]*?)(?=[.\"”]|$)", re.I)
ADD_MIN = re.compile(r"added\s+([A-Z][A-Z0-9,\sand]*?)\s+at\s+(?:my|the)\s+min",
                     re.I)


def ticks(blob: str) -> list[str]:
    out = []
    for tok in re.split(r"[,\s]+", blob):
        tok = tok.strip(".,’'\"”")
        if TICK.match(tok) and tok not in ("AND", "THE", "MY", "ALL", "OF"):
            out.append(tok)
    return out


def parse_one(text: str):
    m = RANKLINE.search(text)
    if not m:
        return None, {}
    ranked = ticks(m.group(1))
    moves: dict[str, tuple] = {}
    cm = COMM.search(text)
    if cm:
        c = cm.group(1)
        for verb, bps, blob in BPS.findall(c):
            sign = 1 if verb.lower() in ("bought", "added") else -1
            for t in ticks(blob):
                moves[t] = (sign * int(bps), None)
        for blob in SOLD_ALL.findall(c):
            for t in ticks(blob):
                moves[t] = (None, "sold_all")
        for blob in ADD_MIN.findall(c):
            for t in ticks(blob):
                if t not in moves:
                    moves[t] = (None, "add_min")
    return ranked, moves


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    rows = [r for r in csv.DictReader(
                (ARCH / "manifest.csv").open(encoding="utf-8"))
            if r["file"] and r["product"] == "ETF Pro/Re-Rank"
            and "Re-Rank" in r["subject"]]
    if args.limit:
        rows = rows[:args.limit]

    out = {}
    stats = {"mails": 0, "with_rank": 0, "rank_rows": 0, "move_rows": 0,
             "errors": 0}
    for r in rows:
        stats["mails"] += 1
        try:
            text, _dt, _h, _p = body_text(ARCH / r["file"])
            nd = date.fromisoformat(r["date"])
        except Exception as e:
            stats["errors"] += 1
            print(f"  ERROR {r['file']}: {e}")
            continue
        ranked, moves = parse_one(text)
        if ranked is None:
            continue
        stats["with_rank"] += 1
        uid = f"{r['folder']}/{r['uid']}"
        for i, t in enumerate(ranked, 1):
            key = (nd, t)
            if key not in out:
                mv = moves.get(t, (None, None))
                out[key] = (i, mv[0], mv[1], uid)
                stats["rank_rows"] += 1
        for t, mv in moves.items():
            key = (nd, t)
            if key not in out:
                out[key] = (None, mv[0], mv[1], uid)
                stats["move_rows"] += 1
    print(f"parsed: {stats}; {len(out)} rows")
    if args.dry_run:
        for k in list(out)[:12]:
            print(" ", k, out[k])
        return 0

    vals = [(nd, t, v[0], v[1], v[2], v[3]) for (nd, t), v in out.items()]
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for i in range(0, len(vals), 1000):
            execute_values(cur,
                "INSERT INTO hedgeye_rerank (note_date, ticker, rank, "
                "move_bps, action, source_uid) VALUES %s "
                "ON CONFLICT (note_date, ticker) DO UPDATE SET "
                "rank=EXCLUDED.rank, move_bps=EXCLUDED.move_bps, "
                "action=EXCLUDED.action, source_uid=EXCLUDED.source_uid",
                vals[i:i + 1000], page_size=1000)
        conn.commit()
        cur.execute("SELECT count(*), count(DISTINCT note_date), "
                    "count(DISTINCT ticker) FROM hedgeye_rerank")
        print("hedgeye_rerank rows/issues/tickers:", cur.fetchone())
    return 0


if __name__ == "__main__":
    sys.exit(main())
