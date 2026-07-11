"""_report_now_preview.py — READ-ONLY preview of REPORT NOW: full assembly
against live data, prints timing + the message, stores NOTHING.
    python _report_now_preview.py
Weekend note: yfinance returns Friday's last bars, so rp_live ≈ stored rp —
the pipe is what's being tested, the numbers get interesting Monday 9:31.
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

t0 = time.time()
from tools.report_now import build_report_now
body = build_report_now()
dt = time.time() - t0

print(body)
print(f"\n[{len(body)} chars · built in {dt:.1f}s — Telegram handler runs "
      f"this synchronously; >60s would need a rethink]")
print("(read-only preview — nothing stored; live command stores kind='now')")
