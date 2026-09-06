"""study_dump.py — the ONLY CSV exit from the paper-signal store (operator
spec 8/29 round 2, Part 3). Postgres tables are the store; this dumps a
date window of them into a zip for external scoring, and nothing else.

    python -m tools.study_dump                        # full history
    python -m tools.study_dump --since 2026-08-01 --until 2026-08-28
    python -m tools.study_dump --out keith_export_2.zip

Zip contents:
  signal_paper_fires.csv  signal_name, variant, ticker, fire_date,
                          features (JSON string, verbatim from jsonb)
  signal_controls.csv     signal_name, ticker, date, seed
  bars_full.csv           ticker, date, close, volume, source — whole
                          universe from mfr_snapshots. MFR publishes
                          previous-day volume; bar D's volume comes from
                          the next stored row when within 4 calendar
                          days, else blank (unknown, never zero).
  ranges_daily.csv        ticker, date, range_low, range_high, source
                          (mfr | hedgeye_rr) — the POST-RR-OVERLAY band
                          the KEITH detector evaluates (production
                          authority order via keith_pattern.build_series)
  trend_daily.csv         ticker, date, trend — from the trend_daily
                          table (blank trend = polled, trend unknown)
  events.csv              source(ss|rta|rerank), ticker, date, action
  MANIFEST.txt            row counts per signal/variant, spans, the 1b
                          coverage-gap report, provenance

READ-ONLY against the store. No returns, win rates, or performance
numbers — scoring happens outside, after the export is audited.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git_provenance() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, cwd=REPO,
                             timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"],
                               capture_output=True, text=True, cwd=REPO,
                               timeout=10).stdout.strip()
        return f"{rev}{' DIRTY-WORKING-TREE' if dirty else ' clean'}"
    except Exception as e:
        return f"unknown ({e})"


def _arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def gap_report(cur) -> list[str]:
    """1b: coverage gaps between the inventory (what we intend to poll)
    and mfr_snapshots (what got snapshotted). Listed, never patched."""
    lines = ["== COVERAGE GAPS (1b: listed, not patched) =="]
    cur.execute("SELECT max(snapshot_date) FROM mfr_snapshots")
    latest = cur.fetchone()[0]
    cur.execute("""SELECT ticker FROM hedgeye_ticker_inventory
                   WHERE is_active
                     AND ticker NOT IN (SELECT DISTINCT ticker
                                        FROM mfr_snapshots
                                        WHERE snapshot_date >= %s)
                   ORDER BY ticker""", (latest - timedelta(days=7),))
    stale = [r[0] for r in cur.fetchall()]
    lines.append(f"active-inventory names with NO mfr snapshot in the last "
                 f"7 days ({len(stale)}): {', '.join(stale) or 'none'}")
    cur.execute("""SELECT count(DISTINCT ticker) FROM mfr_snapshots
                   WHERE ticker NOT IN (SELECT ticker
                                        FROM hedgeye_ticker_inventory)""")
    lines.append(f"snapshotted tickers not in the inventory table: "
                 f"{cur.fetchone()[0]} (historic pollees; their rows remain "
                 "queryable)")
    cur.execute("SELECT count(*) FROM mfr_snapshots WHERE price IS NULL")
    lines.append(f"mfr rows with NULL price: {cur.fetchone()[0]}")
    cur.execute("""SELECT count(*) FROM mfr_snapshots
                   WHERE price IS NOT NULL
                     AND (range_low IS NULL OR range_high IS NULL)""")
    lines.append(f"priced mfr rows missing a range: {cur.fetchone()[0]}")
    return lines


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    since = _arg(argv, "--since")
    until = _arg(argv, "--until")
    since = date.fromisoformat(since) if since else date(1970, 1, 1)
    until = date.fromisoformat(until) if until else date(2999, 1, 1)
    out_zip = _arg(argv, "--out", "keith_export_2.zip")
    if not os.path.isabs(out_zip):
        out_zip = os.path.join(REPO, out_zip)
    dump_dir = os.path.join(REPO, "study_dump_work")
    os.makedirs(dump_dir, exist_ok=True)

    import db_pg
    from tools.keith_pattern import build_series
    counts = {}
    manifest = ["STUDY DUMP — MANIFEST",
                f"generated: {datetime.now().isoformat(timespec='seconds')}"
                f"  git: {_git_provenance()}",
                f"window: {since} .. {until} "
                f"({'full history' if since.year == 1970 else 'clipped'})",
                "store: Postgres is the store; this zip is a scoring dump, "
                "never the system of record.", ""]

    with db_pg.get_conn() as c:
        cur = c.cursor()

        p = os.path.join(dump_dir, "signal_paper_fires.csv")
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["signal_name", "variant", "ticker", "fire_date",
                        "features"])
            cur.execute("""SELECT signal_name, variant, ticker, fire_date,
                                  features
                           FROM signal_paper_fires
                           WHERE fire_date BETWEEN %s AND %s
                           ORDER BY signal_name, variant, fire_date, ticker""",
                        (since, until))
            per_sig = defaultdict(int)
            n = 0
            for sn, v, t, d, f in cur.fetchall():
                w.writerow([sn, v, t, d, json.dumps(f, default=str)])
                per_sig[f"{sn}/{v}" if v else sn] += 1
                n += 1
            counts["signal_paper_fires.csv"] = n
        manifest.append("signal_paper_fires by signal/variant: "
                        + "  ".join(f"{k}={v}"
                                    for k, v in sorted(per_sig.items())))

        p = os.path.join(dump_dir, "signal_controls.csv")
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["signal_name", "ticker", "date", "seed"])
            cur.execute("""SELECT signal_name, ticker, date, seed
                           FROM signal_controls
                           WHERE date BETWEEN %s AND %s
                           ORDER BY signal_name, date, ticker""",
                        (since, until))
            rows = cur.fetchall()
            per_ctl = defaultdict(int)
            for sn, t, d, seed in rows:
                w.writerow([sn, t, d, seed])
                per_ctl[sn] += 1
            counts["signal_controls.csv"] = len(rows)
        manifest.append("signal_controls by generation: "
                        + "  ".join(f"{k}={v}"
                                    for k, v in sorted(per_ctl.items())))

        p = os.path.join(dump_dir, "bars_full.csv")
        cur.execute("""SELECT ticker, snapshot_date, price::float,
                              previous_day_volume
                       FROM mfr_snapshots
                       WHERE price IS NOT NULL
                         AND snapshot_date BETWEEN %s AND %s
                       ORDER BY ticker, snapshot_date""", (since, until))
        by_t = defaultdict(list)
        for t, d, px, pdv in cur.fetchall():
            by_t[t].append((d, px, pdv))
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ticker", "date", "close", "volume", "source"])
            n = 0
            for t in sorted(by_t):
                rows = by_t[t]
                for i, (d, px, _pdv) in enumerate(rows):
                    vol = ""
                    if i + 1 < len(rows):
                        nd, _npx, npdv = rows[i + 1]
                        if npdv is not None and (nd - d).days <= 4:
                            vol = npdv
                    w.writerow([t, d, px, vol, "mfr"])
                    n += 1
            counts["bars_full.csv"] = n

        # post-overlay ranges (production authority order)
        series = build_series(cur)
        cur.execute("""SELECT ticker, signal_date FROM hedgeye_risk_ranges
                       WHERE buy_trade IS NOT NULL AND sell_trade IS NOT NULL
                         AND sell_trade > buy_trade""")
        rr_keys = set(cur.fetchall())
        p = os.path.join(dump_dir, "ranges_daily.csv")
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ticker", "date", "range_low", "range_high",
                        "source"])
            n = 0
            for t in sorted(series):
                for d, _px, lo, hi, _tr in series[t]:
                    if not (since <= d <= until):
                        continue
                    if lo is not None and hi is not None:
                        w.writerow([t, d, lo, hi,
                                    "hedgeye_rr" if (t, d) in rr_keys
                                    else "mfr"])
                    else:
                        w.writerow([t, d, "", "", ""])
                    n += 1
            counts["ranges_daily.csv"] = n

        p = os.path.join(dump_dir, "trend_daily.csv")
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["ticker", "date", "trend"])
            cur.execute("""SELECT ticker, date, trend FROM trend_daily
                           WHERE date BETWEEN %s AND %s
                           ORDER BY ticker, date""", (since, until))
            n = 0
            for t, d, tr in cur.fetchall():
                w.writerow([t, d, tr or ""])
                n += 1
            counts["trend_daily.csv"] = n

        p = os.path.join(dump_dir, "events.csv")
        with open(p, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["source", "ticker", "date", "action"])
            n = 0
            cur.execute("""SELECT ticker, event_date, event FROM ss_flow_events
                           WHERE event_date BETWEEN %s AND %s
                           ORDER BY event_date, ticker""", (since, until))
            for t, d, ev in cur.fetchall():
                w.writerow(["ss", t, d, ev])
                n += 1
            cur.execute("""SELECT ticker, signal_date, action, side,
                                  signal_type, km_flag
                           FROM hedgeye_rta
                           WHERE signal_date BETWEEN %s AND %s
                           ORDER BY signal_date, ticker""", (since, until))
            for t, d, act, side, st_, km in cur.fetchall():
                label = "/".join(x for x in (act, side, st_) if x)
                if km:
                    label += "/KM"
                w.writerow(["rta", t, d, label])
                n += 1
            cur.execute("""SELECT ticker, snapshot_date, rank
                           FROM hedgeye_portfolio_solutions
                           WHERE snapshot_date BETWEEN %s AND %s
                           ORDER BY snapshot_date, rank""", (since, until))
            for t, d, rk in cur.fetchall():
                w.writerow(["rerank", t, d, f"rank={rk}"])
                n += 1
            counts["events.csv"] = n

        manifest.append("")
        manifest.append("== ROW COUNTS ==")
        manifest += [f"{k}: {v}" for k, v in counts.items()]
        manifest.append("")
        manifest += gap_report(cur)

    mpath = os.path.join(dump_dir, "MANIFEST.txt")
    with open(mpath, "w", encoding="utf-8") as fh:
        fh.write("\n".join(manifest) + "\n")

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for fn in sorted(os.listdir(dump_dir)):
            z.write(os.path.join(dump_dir, fn), fn)

    print("\n".join(manifest))
    print(f"\nwrote {out_zip}")


if __name__ == "__main__":
    main()
