#!/usr/bin/env python3
"""_backlog_provenance.py — WHERE does every MFR-backlog ticker come from?

READ-ONLY. Writes nothing, calls no MFR write endpoint. Run it anywhere the bot's
DB is reachable (laptop uses DATABASE_PUBLIC_URL):

    python _backlog_provenance.py                # full attribution
    python _backlog_provenance.py --only posmon  # just one source's contribution
    python _backlog_provenance.py --csv out.csv  # same data as a spreadsheet

Why this exists (2026-07-30): `MFR BACKLOG` prints a flat list plus per-source
COUNTS — never which ticker came from which feed — so unfamiliar names look like
they appeared from nowhere. The backlog is
    full_universe()  −  MFR active watchlist  −  KNOWN_UNCOVERABLE  −  PARKED_FOR_SOURCE
and `full_universe()` is the union of NINE feeds (tools/source_registry.REGISTRY).
This script inverts that union so every backlogged ticker names its origin.

The usual answer for "I've never heard of this ticker": `posmon` — the FROZEN
2026-06-29 Hedgeye Position Monitor seed in ticker_tags.hedgeye_bucket_0629.
It is the whole Long/Short List, not names you trade, and it has no live feed.
`keiths` / `finsigstr` (Financials Sector Pro) is the other big one.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# What each feed IS, in one line — printed next to the counts so the origin of a
# surprising name is self-explaining.
WHAT_IT_IS = {
    "etfpro":    "ETF Pro Plus weekly ranges — latest week_of",
    "portsol":   "Portfolio Solutions re-rank — latest snapshot",
    "ideas":     "Investing Ideas / Top Stock Picks — latest snapshot",
    "keiths":    "Keith's Signal Longs/Shorts — latest signal_date",
    "sigstr":    "Signal Strength Stocks (broad ~68-name roster, side-less)",
    "finsigstr": "Financials Signal Strength = Keith's list, both sides",
    "posmon":    "FROZEN 6/29 Position Monitor seed — the whole Long/Short List, NO live feed",
    "book":      "your Fidelity book — latest snapshot, non-cash, qty<>0",
    "btcquant":  "BTC Quant — crypto names with a trend sentiment",
}


def _fetch(sql, params=None):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="restrict the detail table to one source tag "
                                   "(etfpro portsol ideas keiths sigstr finsigstr "
                                   "posmon book btcquant)")
    ap.add_argument("--csv", help="also write the per-ticker table to this CSV")
    args = ap.parse_args()

    from tools.source_registry import REGISTRY, full_universe
    from tools.enrollment import _mfr_active
    from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE

    fu = full_universe()
    universe, per_source = fu["universe"], fu["per_source"]
    active = _mfr_active()
    excluded = set(KNOWN_UNCOVERABLE) | set(PARKED_FOR_SOURCE)
    backlog = sorted((universe - active) - excluded)

    # invert the union: ticker -> [source tags that contain it]
    origin: dict = {}
    for tag, members in per_source.items():
        for t in members:
            origin.setdefault(t, []).append(tag)

    print("=" * 78)
    print("MFR BACKLOG PROVENANCE  (read-only)")
    print("=" * 78)
    print(f"universe (union of {len(REGISTRY)} feeds): {len(universe)}")
    print(f"active in MFR watchlist:                 {len(active)}")
    print(f"excluded (uncoverable + parked):         {len(excluded & universe)}")
    print(f"BACKLOG:                                 {len(backlog)}")
    if not active:
        print("\n  ⚠️  MFR watchlist came back EMPTY — every universe name will look\n"
              "      backlogged. Fix mfr_client.list_watchlist() before trusting this.")

    # ── per-feed contribution ────────────────────────────────────────────────
    print("\nPER-FEED CONTRIBUTION")
    print(f"  {'tag':<10}{'members':>8}{'backlogged':>12}{'ONLY source':>13}   what it is")
    bl = set(backlog)
    for s in REGISTRY:
        m = set(per_source.get(s.tag, ()))
        in_bl = m & bl
        solo = {t for t in in_bl if set(origin.get(t, ())) == {s.tag}}
        print(f"  {s.tag:<10}{len(m):>8}{len(in_bl):>12}{len(solo):>13}   "
              f"{WHAT_IT_IS.get(s.tag, s.name)}")

    # ── grouped by exact origin combination — this is the 'where from' answer ─
    print("\nBACKLOG GROUPED BY ORIGIN  (exact feed combination)")
    groups: dict = {}
    for t in backlog:
        groups.setdefault("+".join(sorted(origin.get(t, ["<none>"]))), []).append(t)
    for combo, ts in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"\n  [{combo}]  {len(ts)}")
        line = "    "
        for t in sorted(ts):
            if len(line) + len(t) + 1 > 76:
                print(line)
                line = "    "
            line += t + " "
        if line.strip():
            print(line)
    if "<none>" in groups:
        print("\n  ⚠️  '<none>' means a name is on the backlog with no feed claiming it —\n"
              "      that should be impossible. Investigate before enrolling those.")

    # ── enrichment: held? has a range row? tagged sector? ────────────────────
    held, has_range, sector = set(), set(), {}
    try:
        held = {r[0].upper() for r in _fetch(
            "SELECT DISTINCT underlying FROM book_positions WHERE snapshot_date = "
            "(SELECT max(snapshot_date) FROM book_positions) "
            "AND asset_class <> 'cash' AND COALESCE(quantity,0) <> 0") if r[0]}
        has_range = {r[0].upper() for r in _fetch(
            "SELECT DISTINCT ticker FROM mfr_snapshots WHERE ticker = ANY(%s)",
            (backlog,)) if r[0]}
        sector = {r[0].upper(): (r[1] or "") for r in _fetch(
            "SELECT ticker, gics_sector FROM ticker_tags WHERE ticker = ANY(%s)",
            (backlog,)) if r[0]}
    except Exception as e:
        print(f"\n  (enrichment skipped: {e})")

    print("\nEVERY BACKLOGGED TICKER")
    print(f"  {'ticker':<12}{'held':<6}{'range':<7}{'sector':<24}origin")
    rows = []
    for t in backlog:
        src = ",".join(sorted(origin.get(t, ["<none>"])))
        if args.only and args.only not in src.split(","):
            continue
        row = (t, "yes" if t in held else "", "row" if t in has_range else "DARK",
               (sector.get(t) or "")[:22], src)
        rows.append(row)
        print(f"  {row[0]:<12}{row[1]:<6}{row[2]:<7}{row[3]:<24}{row[4]}")
    if args.only:
        print(f"  ({len(rows)} shown · --only {args.only})")

    # ── the two lists worth acting on ────────────────────────────────────────
    posmon_only = sorted(t for t in backlog
                         if set(origin.get(t, ())) == {"posmon"})
    if posmon_only:
        print(f"\nFROZEN-SEED ONLY ({len(posmon_only)}) — these come ONLY from the "
              f"6/29 Position Monitor\nseed, which has no live feed. They are Hedgeye's "
              f"Long/Short List, not your book.\nEnroll only the ones you'd actually "
              f"trade; the rest belong in KNOWN_UNCOVERABLE\nor behind a posmon opt-out.")
        line = "  "
        for t in posmon_only:
            if len(line) + len(t) + 1 > 76:
                print(line)
                line = "  "
            line += t + " "
        if line.strip():
            print(line)

    held_backlog = sorted(t for t in backlog if t in held)
    if held_backlog:
        print(f"\n⚠️  YOU HOLD THESE AND THEY HAVE NO MFR RANGE ({len(held_backlog)}) "
              f"— highest priority:\n  " + " ".join(held_backlog))

    if args.csv:
        import csv as _csv
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            w.writerow(["ticker", "held", "range", "sector", "origin"])
            w.writerows(rows)
        print(f"\nwrote {len(rows)} rows -> {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
