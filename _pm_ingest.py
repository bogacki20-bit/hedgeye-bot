"""_pm_ingest.py — ingest a Position Monitor PDF from disk (the Telegram
path works too once deployed; this is the operator's local eyeball flow
for the 7/6 catch-up upload and any future manual one).

    python _pm_ingest.py <path-to-pdf>            # SIMULATED: parse + diff
                                                  #   vs stored buckets, no writes
    python _pm_ingest.py <path-to-pdf> --commit   # store doc_uploads row +
                                                  #   sync buckets (dated with
                                                  #   the REPORT date)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

args = [a for a in sys.argv[1:] if a != "--commit"]
commit = "--commit" in sys.argv
if not args:
    sys.exit("usage: python _pm_ingest.py <path-to-pdf> [--commit]")
path = args[0]
if not os.path.exists(path):
    sys.exit(f"🛑 file not found: {path}")

from pypdf import PdfReader

text = "\n".join((pg.extract_text() or "") for pg in PdfReader(path).pages)
print(f"extracted {len(text)} chars from {os.path.basename(path)}")

from tools.pm_parse import (REMOVAL_GUARD_FRACTION, diff_summary,
                            parse_position_monitor)

p = parse_position_monitor(text)
print(f"parsed: date {p['report_date'] or 'UNDATED ⚠'} · "
      f"roster {len(p['mapping'])} · warnings {len(p['warnings'])}")
for w in p["warnings"]:
    print(f"  ⚠ {w}")
if p["report_date"] is None:
    sys.exit("🛑 UNDATED — refusing to sync. A fact without a date isn't "
             "a fact.")
if not p["mapping"]:
    sys.exit("🛑 0 tickers parsed — layout change? Not syncing.")

from collections import Counter

for b, n in Counter(p["mapping"].values()).most_common():
    print(f"  {b:<16} {n}")

import db_pg

with db_pg.get_conn() as conn, conn.cursor() as cur:
    cur.execute("SELECT ticker, hedgeye_bucket_0629 FROM ticker_tags "
                "WHERE hedgeye_bucket_0629 IS NOT NULL")
    stored = dict(cur.fetchall())

detect = len(stored) == 0 or \
    len(p["mapping"]) >= REMOVAL_GUARD_FRACTION * len(stored)

# SIMULATED diff — identical logic to sync_buckets, zero writes.
sim = []
for t, b in p["mapping"].items():
    if stored.get(t) != b:
        sim.append({"ticker": t, "from": stored.get(t), "to": b})
if detect:
    for t in sorted(set(stored) - set(p["mapping"])):
        sim.append({"ticker": t, "from": stored[t], "to": "removed"})

print("\n" + diff_summary(sim, len(p["mapping"]), p["report_date"],
                          removals_skipped=not detect))

if not commit:
    print(f"\nSIMULATED — nothing written (stored roster: {len(stored)}). "
          f"If the CHANGES read right:")
    print(f"    python _pm_ingest.py {path} --commit")
    sys.exit(0)

from tools.doc_ingest import _maybe_deep_parse, store_upload

row_id = store_upload(os.path.basename(path), "position_monitor",
                      p["report_date"], text,
                      meta={"source": "_pm_ingest local",
                            "pages": "pdf", "chars": len(text)})
if not row_id:
    sys.exit("🛑 store_upload failed — nothing synced.")
print(f"\ndoc_uploads row {row_id} stored (kind=position_monitor, "
      f"note_date {p['report_date']})")
reply = _maybe_deep_parse("position_monitor", row_id, p["report_date"], text)
print(reply.strip() or "🛑 deep parse returned nothing — check _doc_dump "
                       f"{row_id}")
