#!/usr/bin/env python3
"""_backlog_provenance.py — what do we have on the Hedgeye side, what's really
active in MFR, and where did every backlogged ticker come from?

READ-ONLY. Writes nothing, calls no MFR write endpoint.

    # everything Hedgeye-side, per feed, to a spreadsheet
    python _backlog_provenance.py --dump-universe universe.csv

    # the TRUE backlog, using the MFR website CSV export as the activated set
    python _backlog_provenance.py --mfr-csv "~/Downloads/mfr_export.csv" --csv backlog.csv

    # attribution using the API (only trustworthy when the API agrees with the CSV)
    python _backlog_provenance.py
    python _backlog_provenance.py --only posmon

WHY --mfr-csv EXISTS (2026-07-30). `MFR BACKLOG` returned 472 names including
AAPL NVDA TSLA META AMZN GOOGL, while the dark footer in the same message said
only 2 names in the whole book lacked an MFR range row. Both cannot be true: MFR
cannot serve a daily range for a ticker that is not activated. The LIST endpoint
(`GET /v2/asset`) is returning a partial slice — the same class of failure as
2026-07-20, when it returned nothing at all and 439 of 456 "missing" names were
already activated. The CSV you can export from the MFR website is ground truth
and does not go through that endpoint. When you pass it, this script uses it AND
reports how far the API disagrees — which is the diagnosis.

The backlog itself is:
    full_universe()  −  active  −  KNOWN_UNCOVERABLE  −  PARKED_FOR_SOURCE
where full_universe() is the union of NINE feeds (tools/source_registry.REGISTRY).

The usual answer to "I've never heard of this ticker": `posmon` — the FROZEN
2026-06-29 Position Monitor seed in ticker_tags.hedgeye_bucket_0629. That is
Hedgeye's whole Long/Short List, not names you trade, and it has no live feed.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WHAT_IT_IS = {
    "etfpro":    "ETF Pro Plus weekly ranges — latest week_of",
    "portsol":   "Portfolio Solutions re-rank — latest snapshot",
    "ideas":     "Investing Ideas / Top Stock Picks — latest snapshot",
    "keiths":    "Keith's Signal Longs/Shorts — latest signal_date",
    "sigstr":    "Signal Strength Stocks (broad roster, side-less)",
    "finsigstr": "Financials Signal Strength = Keith's list, both sides",
    "posmon":    "FROZEN 6/29 Position Monitor seed — whole Long/Short List, NO live feed",
    "book":      "your Fidelity book — latest snapshot, non-cash, qty<>0",
    "btcquant":  "BTC Quant — crypto names with a trend sentiment",
}

_TKR = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_HDR_HINT = re.compile(r"^(ticker|tickers|tkr|symbol|symbols|sym|asset|assets|name|instrument)$", re.I)


def read_mfr_csv(path: str) -> set:
    """Tickers out of the MFR website export. Column is found by header name
    first, then by VOTING on ticker-shaped cells — never by assuming a position,
    because the export format isn't pinned anywhere."""
    p = os.path.expanduser(path)
    with open(p, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.reader(f) if any((c or "").strip() for c in r)]
    if not rows:
        sys.exit(f"LOUD FAIL: {p} is empty.")
    # The export carries a title/blank preamble on some vintages, so look for the
    # header row anywhere near the top rather than assuming row 0.
    col, start = None, 1
    for ri, row in enumerate(rows[:10]):
        for i, cell in enumerate(row):
            if _HDR_HINT.match((cell or "").strip()):
                col, start = i, ri + 1
                break
        if col is not None:
            break
    if col is None:
        votes: dict = {}
        for r in rows[:300]:
            for i, cell in enumerate(r):
                if _TKR.match((cell or "").strip().upper()):
                    votes[i] = votes.get(i, 0) + 1
        if not votes:
            sys.exit(f"LOUD FAIL: no ticker-shaped column found in {p}. "
                     f"First row: {rows[0][:8]}")
        col = max(votes, key=votes.get)
        start = 0
        print(f"  (no ticker header — voted column {col} by cell shape)")
    out = {(r[col] or "").strip().upper() for r in rows[start:] if len(r) > col}
    # _TKR happily matches a header cell like 'SYM' — drop those explicitly so a
    # voted column can't smuggle its own header in as a ticker.
    out = {t for t in out if _TKR.match(t) and not _HDR_HINT.match(t)}
    if not out:
        sys.exit(f"LOUD FAIL: column {col} of {p} held no ticker-shaped values.")
    return out


def _fetch(sql, params=None):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def _wrap(tickers, indent="    ", width=76):
    line = indent
    for t in sorted(tickers):
        if len(line) + len(t) + 1 > width:
            print(line)
            line = indent
        line += t + " "
    if line.strip():
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mfr-csv", help="MFR website export = GROUND TRUTH for what "
                                      "is activated. Bypasses the LIST endpoint.")
    ap.add_argument("--only", help="restrict the detail table to one source tag")
    ap.add_argument("--csv", help="write the per-ticker backlog table here")
    ap.add_argument("--dump-universe", help="write EVERY Hedgeye-side name with "
                                            "per-feed membership here")
    args = ap.parse_args()

    from tools.source_registry import REGISTRY, full_universe
    from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE

    fu = full_universe()
    universe, per_source = fu["universe"], fu["per_source"]
    origin: dict = {}
    for tag, members in per_source.items():
        for t in members:
            origin.setdefault(t, set()).add(tag)

    # ── the activated set ────────────────────────────────────────────────────
    api = None
    try:
        from tools.enrollment import _mfr_active
        api = _mfr_active()
    except Exception as e:
        print(f"  (API watchlist read failed: {e})")
        api = set()
    if args.mfr_csv:
        active = read_mfr_csv(args.mfr_csv)
        src_label = f"MFR CSV export ({os.path.basename(args.mfr_csv)})"
    else:
        active, src_label = api, "API list_watchlist()"

    excluded = set(KNOWN_UNCOVERABLE) | set(PARKED_FOR_SOURCE)
    backlog = sorted((universe - active) - excluded)

    print("=" * 78)
    print("HEDGEYE UNIVERSE vs MFR ACTIVATED  (read-only)")
    print("=" * 78)
    print(f"Hedgeye-side universe (union of {len(REGISTRY)} feeds): {len(universe)}")
    print(f"activated, per {src_label}: {len(active)}")
    print(f"excluded (uncoverable + parked, in universe):        {len(excluded & universe)}")
    print(f"TRUE BACKLOG:                                        {len(backlog)}")

    # ── the diagnosis: does the API agree with the CSV? ──────────────────────
    if args.mfr_csv and api is not None:
        only_csv = active - api
        only_api = api - active
        print(f"\nAPI CROSS-CHECK  (this is the 7/20 + 7/30 bug, measured)")
        print(f"  list_watchlist() returned : {len(api)}")
        print(f"  CSV export says activated : {len(active)}")
        print(f"  in CSV but MISSING from API: {len(only_csv)}  <-- the API's error")
        print(f"  in API but not in CSV      : {len(only_api)}")
        if len(api) < len(active) * 0.9:
            print(f"  ⚠️  The LIST endpoint is under-reporting by "
                  f"{len(active) - len(api)} names ({1 - len(api)/max(1,len(active)):.0%}). "
                  f"Every backlog computed from it is inflated by roughly that much.\n"
                  f"      Do NOT enroll from an API-derived backlog until this is fixed.")
            if only_csv:
                print("  a sample the API failed to report as active:")
                _wrap(sorted(only_csv)[:40], indent="      ")
    if not active:
        print("\n  ⚠️  Activated set is EMPTY — every universe name will look "
              "backlogged.\n      Re-run with --mfr-csv before believing anything below.")

    # ── per-feed contribution ────────────────────────────────────────────────
    print("\nPER-FEED CONTRIBUTION")
    print(f"  {'tag':<10}{'members':>8}{'backlogged':>12}{'ONLY source':>13}   what it is")
    bl = set(backlog)
    for s in REGISTRY:
        m = set(per_source.get(s.tag, ()))
        in_bl = m & bl
        solo = {t for t in in_bl if origin.get(t, set()) == {s.tag}}
        print(f"  {s.tag:<10}{len(m):>8}{len(in_bl):>12}{len(solo):>13}   "
              f"{WHAT_IT_IS.get(s.tag, s.name)}")

    # ── grouped by exact origin combination ─────────────────────────────────
    print("\nBACKLOG GROUPED BY ORIGIN  (exact feed combination)")
    groups: dict = {}
    for t in backlog:
        groups.setdefault("+".join(sorted(origin.get(t, {"<none>"}))), []).append(t)
    for combo, ts in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"\n  [{combo}]  {len(ts)}")
        _wrap(ts)
    if "<none>" in groups:
        print("\n  ⚠️  '<none>' = on the backlog with no feed claiming it — "
              "impossible by construction.\n      Investigate before enrolling those.")

    # ── junk-token sweep (parser hygiene, independent of any backlog) ────────
    WORDS = {"FROM", "LIST", "JUST", "BEEN", "SIGNAL", "THE", "AND", "FOR", "WITH",
             "THIS", "THAT", "HAVE", "WILL", "NEW", "TOP", "BUY", "SELL", "LONG",
             "SHORT", "ADD", "TRIM", "HOLD", "DATA", "NOTE", "CALL", "PUT"}
    junk = sorted(WORDS & universe)
    if junk:
        print(f"\nSUSPECT TOKENS IN THE UNIVERSE ({len(junk)}) — English words a "
              f"parser scraped\nas tickers. Not a backlog problem; a source problem.")
        for t in junk:
            print(f"  {t:<10} from: {','.join(sorted(origin.get(t, ())))}")

    # ── enrichment ───────────────────────────────────────────────────────────
    held, has_range, sector = set(), set(), {}
    try:
        held = {r[0].upper() for r in _fetch(
            "SELECT DISTINCT underlying FROM book_positions WHERE snapshot_date = "
            "(SELECT max(snapshot_date) FROM book_positions) "
            "AND asset_class <> 'cash' AND COALESCE(quantity,0) <> 0") if r[0]}
        served = _fetch("SELECT count(DISTINCT ticker) FROM mfr_snapshots "
                        "WHERE snapshot_date >= CURRENT_DATE - 7")
        print(f"\nMFR has served ranges for {served[0][0]} distinct tickers in the "
              f"last 7 days.\n(MFR cannot serve a range for a ticker that is not "
              f"activated — so that is a FLOOR\non the true activated count. "
              f"Compare it to the numbers at the top.)")
        if universe:
            has_range = {r[0].upper() for r in _fetch(
                "SELECT DISTINCT ticker FROM mfr_snapshots WHERE ticker = ANY(%s)",
                (sorted(universe),)) if r[0]}
        sector = {r[0].upper(): (r[1] or "") for r in _fetch(
            "SELECT ticker, gics_sector FROM ticker_tags WHERE ticker = ANY(%s)",
            (sorted(universe),)) if r[0]}
    except Exception as e:
        print(f"\n  (enrichment skipped: {e})")

    # ── the two lists worth acting on ────────────────────────────────────────
    posmon_only = sorted(t for t in backlog if origin.get(t, set()) == {"posmon"})
    if posmon_only:
        print(f"\nFROZEN-SEED ONLY ({len(posmon_only)}) — ONLY from the 6/29 Position "
              f"Monitor seed.\nHedgeye's Long/Short List, not your book, no live feed. "
              f"Enrolling them creates\nrange data, NOT alert streams.")
        _wrap(posmon_only, indent="  ")

    held_dark = sorted(t for t in backlog if t in held and t not in has_range)
    if held_dark:
        print(f"\n⚠️  YOU HOLD THESE AND THEY HAVE NO MFR RANGE ({len(held_dark)}) "
              f"— highest priority:\n  " + " ".join(held_dark))

    # ── detail table + exports ───────────────────────────────────────────────
    print("\nEVERY BACKLOGGED TICKER")
    print(f"  {'ticker':<12}{'held':<6}{'range':<7}{'sector':<24}origin")
    rows = []
    for t in backlog:
        src = ",".join(sorted(origin.get(t, {"<none>"})))
        if args.only and args.only not in src.split(","):
            continue
        row = (t, "yes" if t in held else "", "row" if t in has_range else "DARK",
               (sector.get(t) or "")[:22], src)
        rows.append(row)
        print(f"  {row[0]:<12}{row[1]:<6}{row[2]:<7}{row[3]:<24}{row[4]}")
    if args.only:
        print(f"  ({len(rows)} shown · --only {args.only})")

    if args.csv:
        with open(os.path.expanduser(args.csv), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ticker", "held", "range", "sector", "origin"])
            w.writerows(rows)
        print(f"\nwrote {len(rows)} backlog rows -> {args.csv}")

    if args.dump_universe:
        tags = [s.tag for s in REGISTRY]
        with open(os.path.expanduser(args.dump_universe), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ticker", "active_in_mfr", "held", "has_range", "sector",
                        "n_feeds"] + tags)
            for t in sorted(universe):
                o = origin.get(t, set())
                w.writerow([t, "yes" if t in active else "",
                            "yes" if t in held else "",
                            "yes" if t in has_range else "",
                            sector.get(t, ""), len(o)]
                           + ["x" if tag in o else "" for tag in tags])
        print(f"wrote {len(universe)} universe rows -> {args.dump_universe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
