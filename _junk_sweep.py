#!/usr/bin/env python3
"""_junk_sweep.py — find NON-TICKERS that parsers scraped into the universe.

READ-ONLY by default. Prints a paste-ready KNOWN_UNCOVERABLE block; writes
nothing unless you act on it yourself.

    python _junk_sweep.py                 # sweep the backlog (fast, the usual case)
    python _junk_sweep.py --all           # sweep the ENTIRE universe (slower)
    python _junk_sweep.py --tickers "BEEN FROM LIST SIGNAL HAS JUST"

WHY NOT A WORD LIST (2026-08-01). The 20-name backlog contained BEEN, FROM,
LIST, SIGNAL — English words that `parser_signal_strength.TICKER_RE`
(`\\b([A-Z]{1,5})\\b`, no filter) lifted out of prose and headers like
"SIGNAL STRENGTH STOCKS" and "LONG SHORT LIST". The obvious fix is a stopword
list, and the repo already has one in corpus_rag._TICKER_STOPWORDS.

That list contains HAS and JUST.

HAS is Hasbro (NASDAQ). JUST is the Goldman Sachs JUST U.S. Large Cap Equity
ETF (NYSE Arca). Both are legitimate Hedgeye-universe names, and both would be
silently deleted. A word list cannot tell a word from a ticker that looks like
one — so this asks the market instead:

    junk  <=>  MFR has NEVER served it a range
               AND the price feed returns no quote
               AND you don't hold it

All three must be true. HAS and JUST quote fine, so they can never be flagged
no matter how English they look. Deleting universe members is operator-gated by
design: this proposes, you decide.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fetch(sql, params=None):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def classify(tickers, has_range, quoted, held) -> dict:
    """Pure. {ticker: verdict}. Three independent pieces of evidence; a name is
    only JUNK when every one of them says it does not exist as a tradeable
    symbol. 'quote-only' and 'range-only' are deliberately NOT junk — one
    missing source is a coverage gap, not proof of nonexistence."""
    out = {}
    for t in tickers:
        r, q, h = t in has_range, t in quoted, t in held
        if h:
            out[t] = "HELD — real, you own it"
        elif r and q:
            out[t] = "real (range + quote)"
        elif q:
            out[t] = "real (quote, no MFR range = enrollment gap)"
        elif r:
            out[t] = "range but no quote — check the symbol mapping"
        else:
            out[t] = "JUNK — no range, no quote, not held"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="sweep the whole universe, not just the backlog")
    ap.add_argument("--tickers", help="space/comma separated list to test instead")
    args = ap.parse_args()

    from tools.source_registry import REGISTRY, full_universe
    from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE

    fu = full_universe()
    universe, per_source = fu["universe"], fu["per_source"]
    origin: dict = {}
    for tag, members in per_source.items():
        for t in members:
            origin.setdefault(t, set()).add(tag)

    if args.tickers:
        cands = sorted({t.strip().upper()
                        for t in args.tickers.replace(",", " ").split() if t.strip()})
        label = "explicit list"
    elif args.all:
        cands = sorted(universe)
        label = "entire universe"
    else:
        try:
            from tools.enrollment import _mfr_active
            active = _mfr_active()
        except Exception as e:
            print(f"  (watchlist read failed: {e}) — falling back to --all")
            active = set()
        if not active:
            print("  ⚠️  watchlist read is EMPTY — sweeping the whole universe "
                  "instead of a bogus backlog.")
            cands = sorted(universe)
        else:
            cands = sorted((universe - active)
                           - set(KNOWN_UNCOVERABLE) - set(PARKED_FOR_SOURCE))
        label = "current backlog"

    print("=" * 74)
    print(f"JUNK SWEEP — {len(cands)} candidates ({label})")
    print("=" * 74)
    if not cands:
        print("nothing to test.")
        return 0

    has_range, held = set(), set()
    try:
        has_range = {r[0].upper() for r in _fetch(
            "SELECT DISTINCT ticker FROM mfr_snapshots WHERE ticker = ANY(%s)",
            (cands,)) if r[0]}
        held = {r[0].upper() for r in _fetch(
            "SELECT DISTINCT underlying FROM book_positions WHERE snapshot_date = "
            "(SELECT max(snapshot_date) FROM book_positions) "
            "AND asset_class <> 'cash' AND COALESCE(quantity,0) <> 0") if r[0]}
    except Exception as e:
        print(f"  DB evidence unavailable ({e}) — cannot judge. Stopping.")
        return 2

    quoted = set()
    try:
        from price_monitor import fetch_prices
        # the bot's own price path, so a symbol it can't price is a symbol it
        # could never have alerted on anyway
        prices = fetch_prices(list(cands)) or {}
        quoted = {str(k).upper() for k, v in prices.items() if v is not None}
        print(f"price feed answered for {len(quoted)}/{len(cands)}")
    except Exception as e:
        print(f"  ⚠️  price feed unavailable ({e}). Without quotes NOTHING can be "
              f"judged junk —\n      every verdict below would be a guess. Stopping.")
        return 2

    verdicts = classify(cands, has_range, quoted, held)
    junk = sorted(t for t, v in verdicts.items() if v.startswith("JUNK"))
    odd = sorted(t for t, v in verdicts.items() if v.startswith("range but"))

    print(f"\n{'ticker':<12}{'verdict':<46}origin")
    for t in cands:
        print(f"{t:<12}{verdicts[t]:<46}{','.join(sorted(origin.get(t, ())))}")

    if odd:
        print(f"\n⚠️  RANGE BUT NO QUOTE ({len(odd)}) — MFR knows them, the price "
              f"feed doesn't.\n   Symbol-mapping problem, NOT junk. Do not delete:")
        print("   " + " ".join(odd))

    if not junk:
        print("\n✅ No junk found — every candidate resolves somewhere.")
        return 0

    print(f"\n🗑  JUNK ({len(junk)}) — no MFR range, no quote, not held.")
    print("   Which feed introduced each (this is where the parser needs fixing):")
    by_feed: dict = {}
    for t in junk:
        by_feed.setdefault("+".join(sorted(origin.get(t, {"<none>"}))), []).append(t)
    for feed, ts in sorted(by_feed.items(), key=lambda kv: -len(kv[1])):
        print(f"     {feed:<28} {' '.join(ts)}")

    print("\n   Paste into tools/enrollment_sources.KNOWN_UNCOVERABLE to stop them\n"
          "   appearing in the backlog (a band-aid — fixing the parser is the cure):\n")
    for t in junk:
        print(f'    "{t}",'.ljust(20)
              + f"# not a ticker — scraped by {','.join(sorted(origin.get(t, ())))}")
    print("\n   Cure: the feed(s) above extract tickers with a bare uppercase-token\n"
          "   regex over email text. parser_signal_strength.py:62 is\n"
          "   TICKER_RE = re.compile(r'\\b([A-Z]{1,5})\\b') with no filter at all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
