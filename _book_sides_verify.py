"""
_book_sides_verify.py — READ-ONLY live check of position_direction (bug #1 fix).
Run on the Lenovo (DATABASE_PUBLIC_URL in .env):
    python _book_sides_verify.py
Prints every book side verdict, then renders `my book shorts` / `my book longs`
exactly as Telegram would. Writes NOTHING.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# load .env if vars not already present
if not (os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")):
    try:
        for ln in open(os.path.join(os.path.dirname(__file__), ".env")):
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, v = ln.split("=", 1)
                os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass

from tools.book_direction import book_sides

FOCUS = ["SHY", "TUA", "HEFT", "AGGH", "ZROZ", "VCLT",          # long rate complex
         "EIS", "PPLT",                                          # outright shorts
         "SBIT", "METD", "GGLS", "MSFD",                         # inverse wrappers
         "AMZN", "EWZ", "BNO", "SLV",                            # bear put spreads
         "XLV", "XLU"]                                           # sector longs

s = book_sides()
print(f"{len(s)} underlyings with a side verdict\n")
print(f"{'ticker':<7}{'side':<7}{'raw':<7}{'net':>12}  legs unk link")
for t in FOCUS:
    v = s.get(t)
    if not v:
        print(f"{t:<7}MISSING — not in latest snapshot?")
        continue
    print(f"{t:<7}{v['side'] or '?':<7}{v['raw_side'] or '?':<7}{v['net']:>12,.0f}  "
          f"{v['legs']:>4} {v['unknown_legs']:>3} {'inv' if v['via_linkage'] else '—'}")

longs  = sorted(t for t, v in s.items() if v["side"] == "long")
shorts = sorted(t for t, v in s.items() if v["side"] == "short")
other  = sorted(t for t, v in s.items() if v["side"] not in ("long", "short"))
print(f"\nLONG  ({len(longs)}): {' '.join(longs)}")
print(f"SHORT ({len(shorts)}): {' '.join(shorts)}")
print(f"OTHER ({len(other)}): {' '.join(other) or 'none'}")

print("\n" + "=" * 60 + "\nSCREEN my book shorts\n" + "=" * 60)
from tools.screener import run_screen
print(run_screen("my book shorts"))
print("\n" + "=" * 60 + "\nSCREEN my book longs\n" + "=" * 60)
print(run_screen("my book longs"))
