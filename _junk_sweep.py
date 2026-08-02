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


# Hedgeye tables that carry VALUES — publishing a number or a side for a ticker
# is an act of coverage. A bare uppercase token appearing in prose is not.
STRONG_SQL = [
    ("risk_range",  "SELECT DISTINCT ticker FROM hedgeye_risk_ranges "
                    "WHERE ticker = ANY(%s) AND buy_trade IS NOT NULL "
                    "AND sell_trade IS NOT NULL"),
    ("etf_pro",     "SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges "
                    "WHERE ticker = ANY(%s) AND range_low IS NOT NULL "
                    "AND range_high IS NOT NULL"),
    ("keiths",      "SELECT DISTINCT ticker FROM hedgeye_keiths_signals "
                    "WHERE ticker = ANY(%s) AND side IN ('long','short')"),
    ("ideas",       "SELECT DISTINCT ticker FROM hedgeye_investing_ideas "
                    "WHERE ticker = ANY(%s) AND rank IS NOT NULL"),
    ("portsol",     "SELECT DISTINCT ticker FROM hedgeye_portfolio_solutions "
                    "WHERE ticker = ANY(%s) AND rank IS NOT NULL"),
]

# Position Monitor buckets are a VALUE, but an OCR-DERIVED one: tools/pm_parse
# reads a PDF, and its own comment records that sector headers collide with the
# ticker pattern ("GLL, RETAIL, ENERGY on the 7/6 PDF"). The guard for that only
# fires when the sector line is followed IMMEDIATELY by a bucket header; any
# other layout mints a bucketed pseudo-ticker. posmon is also the largest feed
# (~435 of the universe), so treating its bucket as unconditional proof would
# launder every OCR artifact into "COVERED".
#
# So a bucket counts only when something independent agrees: a values feed, a
# price quote, or the book. The PM is a single-stock/ETF list — every real entry
# on it quotes.
OCR_SQL = [
    ("posmon_seed", "SELECT DISTINCT ticker FROM ticker_tags "
                    "WHERE ticker = ANY(%s) AND hedgeye_bucket_0629 IS NOT NULL"),
    ("posmon_live", "SELECT DISTINCT ticker FROM bucket_history "
                    "WHERE ticker = ANY(%s) AND bucket IS NOT NULL"),
]

# pm_parse's own vocabulary — a "ticker" equal to one of these is a header the
# parser mis-read, not a security.
PM_VOCAB = {"ACTIVE", "LONGS", "SHORTS", "TOP", "IDEA", "BENCH", "LONG", "SHORT",
            "RETAIL", "ENERGY", "GLL", "MACRO", "TECH", "FINANCIALS", "HEALTH",
            "CARE", "CONSUMER", "INDUSTRIALS", "UTILITIES", "MATERIALS",
            "STAPLES", "DISCRETIONARY", "COMM", "REITS", "SECTOR", "MONITOR",
            "POSITION", "HEDGEYE"}
# Membership-only tables: ticker + flags, no values. This is the output of the
# unfiltered regex, so weak-only support is the fingerprint of a scraped word.
WEAK_SQL = [
    ("sigstr", "SELECT DISTINCT ticker FROM hedgeye_signal_strength "
               "WHERE ticker = ANY(%s)"),
    ("ss_roster", "SELECT DISTINCT ticker FROM ss_roster_history "
                  "WHERE ticker = ANY(%s)"),
]


def gather_evidence(cands) -> dict:
    """{ticker: {"strong": [src], "weak": [src]}}. One query per source. A source
    that errors is skipped LOUDLY — silently missing evidence would convict a
    name Hedgeye does cover."""
    ev = {t: {"strong": [], "ocr": [], "weak": []} for t in cands}
    for kind, specs in (("strong", STRONG_SQL), ("ocr", OCR_SQL),
                        ("weak", WEAK_SQL)):
        for label, sql in specs:
            try:
                for r in _fetch(sql, (list(cands),)):
                    if r[0] and r[0].upper() in ev:
                        ev[r[0].upper()][kind].append(label)
            except Exception as e:
                print(f"  ⚠️  evidence source {label} unavailable ({e}) — names "
                      f"relying on it may be misjudged.")
    return ev


def classify(tickers, evidence, quoted, held) -> dict:
    """Pure. {ticker: (verdict, detail)}.

    COVERAGE decides membership — did Hedgeye publish DATA for this name. The
    quote is used only to split a real ticker Hedgeye never covered from a word
    that isn't a ticker at all. Both can leave the universe; only the second is
    evidence of a parser bug."""
    out = {}
    for t in tickers:
        ev = evidence.get(t) or {}
        strong = sorted(set(ev.get("strong") or []))
        ocr = sorted(set(ev.get("ocr") or []))
        weak = sorted(set(ev.get("weak") or []))
        if strong:
            out[t] = ("COVERED", "Hedgeye data: " + ",".join(strong)
                      + (" (+" + ",".join(ocr) + ")" if ocr else ""))
        elif ocr and (t in quoted or t in held):
            # bucket corroborated by an independent source
            out[t] = ("COVERED", "Position Monitor bucket (" + ",".join(ocr)
                      + "), corroborated by " + ("book" if t in held else "quote"))
        elif ocr and t.upper() in PM_VOCAB:
            out[t] = ("PM-ARTIFACT", "bucketed by pm_parse but it is a PM header "
                                     "word — sector/bucket line read as a ticker")
        elif ocr:
            out[t] = ("PM-ARTIFACT", "bucketed by " + ",".join(ocr)
                      + " but nothing else knows it — no quote, no values, "
                        "not held. Likely an OCR mis-read.")
        elif t in held:
            out[t] = ("HELD", "you own it — keep regardless of coverage")
        elif weak and t in quoted:
            out[t] = ("TOKEN-ONLY", "real ticker, Hedgeye never published data "
                                    "for it — from " + ",".join(weak))
        elif weak:
            out[t] = ("JUNK", "not a ticker, no Hedgeye data — scraped by "
                              + ",".join(weak))
        elif t in quoted:
            out[t] = ("TOKEN-ONLY", "real ticker, no Hedgeye data, no feed claims it")
        else:
            out[t] = ("JUNK", "no Hedgeye data, no quote, not held")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="sweep the whole universe, not just the backlog")
    ap.add_argument("--tickers", help="space/comma separated list to test instead")
    ap.add_argument("--enrollable", action="store_true",
                    help="print ONLY the clean paste line for MFR -> Activate "
                         "Assets: COVERED + HELD + TOKEN-ONLY, with JUNK and "
                         "PM-ARTIFACT excluded. One command for the "
                         "enroll-everything-real policy.")
    args = ap.parse_args()

    from tools.source_registry import REGISTRY, enrollment_universe
    from tools.enrollment_sources import KNOWN_UNCOVERABLE, PARKED_FOR_SOURCE

    # MUST match what the deployed MFR BACKLOG computes. compile_backlog
    # moved to enrollment_universe() (all-time) in 90f8345; a diagnostic
    # still reading full_universe() reports a different backlog than the
    # bot does, which is worse than reporting nothing.
    fu = enrollment_universe()
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
            # ABORT, don't degrade. 2026-08-02: a run without MFR_API_TOKEN fell
            # back to the whole universe and produced a confident 595-line report
            # answering a different question. That is the exact failure the
            # watchlist guard exists to stop inside the bot, and this script had
            # no business doing it either. Use `railway run python _junk_sweep.py`
            # so the token resolves, or --all to sweep the universe deliberately.
            sys.exit("LOUD FAIL: the MFR watchlist read returned NOTHING, so the "
                     "backlog cannot be computed — every universe name would look "
                     "un-enrolled.\n  Run under `railway run` so MFR_API_TOKEN "
                     "resolves, or pass --all to sweep the whole universe on "
                     "purpose.")
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

    held = set()
    try:
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

    verdicts = classify(cands, gather_evidence(cands), quoted, held)
    junk = sorted(t for t, (v, _) in verdicts.items() if v == "JUNK")
    token_only = sorted(t for t, (v, _) in verdicts.items() if v == "TOKEN-ONLY")
    pm_art = sorted(t for t, (v, _) in verdicts.items() if v == "PM-ARTIFACT")

    if args.enrollable:
        # Real names only. JUNK is not a ticker; PM-ARTIFACT is an OCR mis-read
        # or a naming mismatch — neither belongs in Activate Assets, and pasting
        # them wastes a slot at best.
        good = [t for t in cands
                if verdicts[t][0] in ("COVERED", "HELD", "TOKEN-ONLY")]
        print(f"\nENROLLABLE ({len(good)} of {len(cands)}) — paste into "
              f"MFR → Activate Assets:")
        line = ""
        for t in good:
            if len(line) + len(t) + 1 > 76:
                print(line)
                line = ""
            line += t + " "
        if line.strip():
            print(line)
        dropped = [(t, verdicts[t][0]) for t in cands
                   if verdicts[t][0] in ("JUNK", "PM-ARTIFACT")]
        if dropped:
            print(f"\nexcluded ({len(dropped)}): "
                  + " ".join(f"{t}[{v.split('-')[0].lower()}]" for t, v in dropped))
        return 0

    print(f"\n{'ticker':<10}{'verdict':<12}{'detail':<58}origin")
    for t in cands:
        v, d = verdicts[t]
        print(f"{t:<10}{v:<12}{d[:56]:<58}{','.join(sorted(origin.get(t, ())))}")

    if token_only:
        print(f"\n❓ TOKEN-ONLY ({len(token_only)}) — real tradeable tickers, but "
              f"Hedgeye never\n   published a range, side, bucket or rank for them. "
              f"They entered the universe\n   through a membership-only feed, which "
              f"is what the unfiltered regex writes to.\n   These are the ones a "
              f"stopword list gets WRONG in both directions — your call:")
        for t in token_only:
            print(f"     {t:<10} {verdicts[t][1]}")

    if pm_art:
        print(f"\n📄 PM-ARTIFACT ({len(pm_art)}) — carry a Position Monitor bucket "
              f"but nothing\n   independent knows them. The PM is a single-stock "
              f"/ ETF list, so every real\n   entry quotes. A bucket alone proves "
              f"only that pm_parse assigned one — and its\n   own comment records "
              f"that sector headers (GLL, RETAIL, ENERGY) match the ticker\n   "
              f"pattern. Check these against the PDF before dismissing:")
        for t in pm_art:
            print(f"     {t:<10} {verdicts[t][1]}")

    if not junk and not token_only and not pm_art:
        print("\n✅ Every candidate is covered by real Hedgeye data.")
        return 0
    if not junk:
        return 0

    print(f"\n🗑  JUNK ({len(junk)}) — no Hedgeye data, no quote, not held.")
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
          "   regex over email text. ALREADY BOUNDED: parser_signal_strength\n"
          "   (parse_body, 8/1) and parser_research_common.side_blocks (8/2).\n"
          "   STILL OPEN: parser_retail.py:95 (_SCAN_RE = r'\\b[A-Z]{2,5}\\b')\n"
          "   and the portfolio-actions path. Both assign a side/action to a whole\n"
          "   block, so a scraped token inherits one and survives the value filter\n"
          "   in source_registry.ALLTIME_SQL — which is why anything is left here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
