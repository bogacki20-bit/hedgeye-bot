"""READ-ONLY wrapper diagnostic: why do METD/GGLS/MSFD/YCS/SQQQ show no
thesis flag in book_alerts while SCREEN says their theses are broken?
Dumps every layer for the wrapper set + PPLT/XLY (count mismatch suspects)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FOCUS = ["SBIT", "EUO", "METD", "GGLS", "MSFD", "YCS", "SQQQ", "PPLT", "XLY", "EIS"]

from tools.book_direction import book_sides
from tools.wrapper_links import get_links
from tools.screener import (_fetch_source_slice, _apply_btcquant_trend,
                            _apply_wrapper_trend)
from tools.book_alerts import _book_rows

sides = book_sides()
links = get_links()

print("=== 1. book_sides (raw truth) ===")
for t in FOCUS:
    v = sides.get(t)
    print(f"  {t:<6} {v if v else 'NOT IN BOOK'}")

print("\n=== 2. wrapper_links rows ===")
for w, lk in sorted(links.items()):
    print(f"  {w:<6} -> {lk['underlying']:<7} inverse={lk['inverse']}")

print("\n=== 3. screener slice for focus set (after btcq+wrapper overrides) ===")
sl = _fetch_source_slice(FOCUS, None)
_apply_btcquant_trend(sl)
_apply_wrapper_trend(sl)
for r in sorted(sl, key=lambda x: x["ticker"]):
    print(f"  {r['ticker']:<6} trend={r.get('trend_dir')!s:<8} "
          f"src={r.get('trend_source')} rp={r.get('range_pos')} "
          f"wrap={r.get('_wrap')}")

print("\n=== 4. book_alerts._book_rows output for focus set ===")
for r in _book_rows():
    if r["ticker"] in FOCUS:
        print(f"  {r['ticker']:<6} side={r['side']:<6} trend={r['trend_dir']!s:<8} "
              f"rp={r['rp_now']} hi5={r['rp_5d_max']} lo5={r['rp_5d_min']} "
              f"wrap={r['wrap']}")

print("\n=== 5. verdicts (what SHOULD flag) ===")
for r in _book_rows():
    t = r["ticker"]
    if t not in FOCUS:
        continue
    td = r.get("trend_dir") or ""
    against = ((r["side"] == "long" and td == "BEARISH") or
               (r["side"] == "short" and td == "BULLISH"))
    print(f"  {t:<6} side={r['side']:<6} trend={td:<8} -> "
          f"{'⚠ SHOULD FLAG' if against else 'aligned'}")
