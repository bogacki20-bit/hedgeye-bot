"""_daypack_preview.py — READ-ONLY preview of DAYPACK (assembles the full
pack incl. a live REPORT NOW price fetch; stores nothing).
    python _daypack_preview.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

t0 = time.time()
from tools.daypack import build_daypack
pack = build_daypack()
dt = time.time() - t0

print(pack[:3000])
print(f"\n… [showing first 3,000 of {len(pack):,} chars · built in {dt:.1f}s]")
print("section headers found:")
for ln in pack.split("\n"):
    if ln.startswith("========"):
        print("  " + ln.strip("= "))
print("(read-only preview — nothing stored; live command stores kind='daypack')")
