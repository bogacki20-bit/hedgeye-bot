#!/usr/bin/env python3
"""_universe_expand.py — how much MORE Hedgeye data could we be enrolling?

READ-ONLY. Counts only; enrolls nothing, changes nothing.

    python _universe_expand.py                 # the full picture
    python _universe_expand.py --csv new.csv   # every currently-missed name

WHY (operator, 2026-08-01): "I get a large list of assets from Hedgeye that I
want data on. We want to be able to go anywhere."

Two structural reasons the current universe is far smaller than what Hedgeye has
actually given us:

1. FEEDS NOT WIRED IN. 22 hedgeye_* tables carry tickers. tools/source_registry
   uses 6 of them. `hedgeye_risk_ranges` — the daily Risk Range Signals product,
   the core subscription — is not a source at all. Nor are RTA, MOMO, HedgAI,
   Retail, Financials, the II tables, signal_changes, or the research/call/macro
   tables. `hedgeye_ticker_inventory` is, by its name, a catalogue of every
   ticker ever seen.

2. LATEST-SNAPSHOT MEMBERSHIP. The feeds that ARE wired in count only their most
   recent snapshot: _etfpro takes MAX(week_of), _ideas and _portsol MAX(
   snapshot_date), _keiths MAX(signal_date). A name Hedgeye ranged last month and
   hasn't repeated silently leaves the universe — so it is never enrolled, and
   there is no range history the day you want to rotate into it. That contradicts
   the repo's own enroll-never-remove doctrine.

This script quantifies both, per table, so the decision is made on numbers rather
than on anyone's impression of them. It does NOT propose enrolling junk: names
are reported with a quote check so parser artifacts are visible before anything
is pasted anywhere.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# (label, table, ticker column, wired-into-registry?, what the product is)
TABLES = [
    ("risk_ranges",   "hedgeye_risk_ranges",        "ticker", False,
     "RISK RANGE SIGNALS — the daily core product"),
    ("ticker_inv",    "hedgeye_ticker_inventory",   "ticker", False,
     "every ticker ever seen (catalogue)"),
    ("rta",           "hedgeye_rta",                "ticker", False,
     "Real-Time Alerts"),
    ("momo",          "hedgeye_momo",               "ticker", False,
     "MOMO Tracker"),
    ("hedgai",        "hedgeye_hedgai",             "ticker", False,
     "HedgAI signals"),
    ("retail",        "hedgeye_retail",             "ticker", False,
     "Retail (McGough)"),
    ("financials",    "hedgeye_financials",         "ticker", False,
     "Financials earnings recap"),
    ("ii_changes",    "hedgeye_ii_changes",         "ticker", False,
     "Investing Ideas add/remove"),
    ("ii_newsletter", "hedgeye_ii_newsletter",      "ticker", False,
     "Investing Ideas newsletter"),
    ("signal_changes", "hedgeye_signal_changes",    "ticker", False,
     "TREND signal changes"),
    ("port_actions",  "hedgeye_portfolio_actions",  "ticker", False,
     "Portfolio Solutions actions"),
    ("the_call",      "hedgeye_the_call",           "ticker", False,
     "The Call @ Hedgeye"),
    ("macro_show",    "hedgeye_macro_show",         "ticker", False,
     "The Macro Show"),
    ("early_look",    "hedgeye_early_look",         "ticker", False,
     "Early Look"),
    ("research",      "hedgeye_research_notes",     "ticker", False,
     "Research notes / MSR / Quads-GIP"),
    # already wired, shown for the all-time vs latest-snapshot comparison
    ("etf_pro",       "hedgeye_etf_pro_ranges",     "ticker", True,
     "ETF Pro Plus (registry uses MAX(week_of) only)"),
    ("ideas",         "hedgeye_investing_ideas",    "ticker", True,
     "Investing Ideas (registry uses MAX(snapshot_date) only)"),
    ("portsol",       "hedgeye_portfolio_solutions", "ticker", True,
     "Portfolio Solutions (registry uses MAX(snapshot_date) only)"),
    ("keiths",        "hedgeye_keiths_signals",     "ticker", True,
     "Keith's Signals (registry uses MAX(signal_date) only)"),
    ("sigstr",        "hedgeye_signal_strength",    "ticker", True,
     "Signal Strength — ⚠ unfiltered regex, expect scraped words"),
]


def _fetch(sql, params=None):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="write every currently-missed name here")
    ap.add_argument("--min-rows", type=int, default=1,
                    help="ignore tickers with fewer than N rows in a table "
                         "(default 1). Raise to 2+ to drop one-off mentions.")
    args = ap.parse_args()

    from tools.source_registry import full_universe
    fu = full_universe()
    universe = fu["universe"]

    try:
        from tools.enrollment import _mfr_active
        active = _mfr_active()
    except Exception as e:
        print(f"  (watchlist read failed: {e})")
        active = set()
    if not active:
        print("  ⚠️  MFR watchlist read is EMPTY — 'already active' counts below\n"
              "      will be wrong. Fix that before acting on this.")

    print("=" * 78)
    print("UNIVERSE EXPANSION — what Hedgeye has given us vs what we enroll")
    print("=" * 78)
    print(f"current universe (6 wired feeds): {len(universe)}")
    print(f"active in MFR:                    {len(active)}")

    per_table, all_new = {}, {}
    print(f"\n{'source':<15}{'distinct':>9}{'NOT in':>9}{'NOT in':>9}   product")
    print(f"{'':<15}{'all-time':>9}{'universe':>9}{'MFR':>9}")
    for label, table, col, wired, what in TABLES:
        try:
            rows = _fetch(
                f"SELECT {col}, count(*) FROM {table} "
                f"WHERE {col} IS NOT NULL AND {col} <> '' "
                f"GROUP BY {col} HAVING count(*) >= %s", (args.min_rows,))
        except Exception as e:
            print(f"{label:<15}{'—':>9}{'—':>9}{'—':>9}   unavailable ({e})")
            continue
        names = {r[0].strip().upper() for r in rows if r[0]}
        new_u = names - universe
        new_m = names - active
        per_table[label] = (names, new_u, new_m)
        for t in new_u:
            all_new.setdefault(t, []).append(label)
        mark = " " if wired else "*"
        print(f"{label:<15}{len(names):>9}{len(new_u):>9}{len(new_m):>9}  {mark}{what}")
    print("\n  * = NOT wired into tools/source_registry — these names cannot reach "
          "enrollment today.")

    if not all_new:
        print("\n✅ Nothing new — every Hedgeye ticker is already in the universe.")
        return 0

    print(f"\nTOTAL NEW NAMES available but not in the universe: {len(all_new)}")
    only_1 = [t for t, srcs in all_new.items() if len(srcs) == 1]
    print(f"  seen in only ONE source: {len(only_1)}  "
          f"(higher junk risk — a single mention may be a scraped word)")
    print(f"  seen in 2+ sources:      {len(all_new) - len(only_1)}  "
          f"(corroborated, safe to enroll)")

    quoted = set()
    try:
        from price_monitor import fetch_prices
        cand = sorted(all_new)
        prices = fetch_prices(cand) or {}
        quoted = {str(k).upper() for k, v in prices.items() if v is not None}
        print(f"\nprice feed answered for {len(quoted)}/{len(cand)} — "
              f"{len(cand) - len(quoted)} do not quote at all "
              f"(parser artifacts, do NOT enroll)")
    except Exception as e:
        print(f"\n  (price check skipped: {e} — cannot separate real names from "
              f"scraped words)")

    real = sorted(t for t in all_new if t in quoted) if quoted else []
    if real:
        print(f"\nENROLLABLE — real, quoting, not yet in MFR ({len(real)}):")
        line = "  "
        for t in real:
            if len(line) + len(t) + 1 > 76:
                print(line)
                line = "  "
            line += t + " "
        if line.strip():
            print(line)

    if args.csv:
        with open(os.path.expanduser(args.csv), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ticker", "quotes", "in_mfr", "n_sources", "sources"])
            for t in sorted(all_new):
                srcs = all_new[t]
                w.writerow([t, "yes" if t in quoted else "",
                            "yes" if t in active else "",
                            len(srcs), " ".join(sorted(srcs))])
        print(f"\nwrote {len(all_new)} rows -> {args.csv}")

    print("\nTo actually widen the universe, two changes in tools/source_registry:\n"
          "  1. add the starred tables above as Sources;\n"
          "  2. drop the MAX(date) filters so membership is ALL-TIME — the repo's\n"
          "     enroll-never-remove doctrine already says a name should not leave\n"
          "     the universe just because a product stopped repeating it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
