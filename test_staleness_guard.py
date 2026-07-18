"""TIER 0 — staleness guard tests (display-only guard in decision_engine.py).

Pure-function tests, no DB, no network. Assert the SHIPPED behavior:
  * _snapshot_stale_note — fresh -> None, else a short "Nh old" / "Nd old" note.
  * _zone_summary_line   — deterministic zone line; when stale, an ASCII
    "[STALE: <note>] " prefix (NOT the emoji "⚠STALE (...) —" form).
  * _mfr_line_stale_note — the MFR range is stale whenever the MFR snapshot is
    old EVEN when the price is live; flagged on the MFR line only.
"""
from datetime import datetime, timezone, date
import decision_engine as de

NOW = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
sn = de._snapshot_stale_note

# ── _snapshot_stale_note: fresh vs stale, fetched_at vs snapshot_date ─────────
assert sn(date(2026, 7, 13), datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc), now=NOW) is None      # 6h < 20h
assert sn(date(2026, 7, 12), datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc), now=NOW) == "26h old"
assert sn(date(2026, 7, 10), datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc), now=NOW) == "3d old"
assert sn(date(2026, 7, 12), None, now=NOW) is None                                                  # date-only @20:00 -> 18h
assert sn(None, None, now=NOW) == "age unknown"
# naive fetched_at is treated as UTC (no crash, correct age)
assert sn(date(2026, 7, 12), datetime(2026, 7, 12, 12, 0), now=NOW) == "26h old"

# ── _zone_summary_line: clean, stale-stamped (shipped [STALE: ] format), unavailable ──
clean = de._zone_summary_line("MFR", 100, 90, 110)
assert clean == "MFR zone for price 100.0 in range [90.0, 110.0]: mid_range (50% through range; NOT a breach)"
assert de._zone_summary_line("MFR", 100, 90, 110, stale_note=None) == clean
assert de._zone_summary_line("MFR", 100, 90, 110, stale_note="3d old") == "[STALE: 3d old] " + clean
# unavailable early-returns are NEVER stamped
assert de._zone_summary_line("MFR", None, 90, 110, stale_note="3d old") == "MFR zone: unavailable (missing price/low/high)"

# ── _mfr_line_stale_note: stale MFR range flagged even when the price is live ──
mln = de._mfr_line_stale_note
# live price OK (price_note None) + fresh MFR snapshot -> no note
assert mln(None, date(2026, 7, 13), datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc), now=NOW) is None
# live price OK + STALE MFR snapshot -> flagged on the MFR line (the new behavior)
assert mln(None, date(2026, 7, 10), datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc), now=NOW) == "MFR range 3d old"
# price-source note present -> it dominates (price staleness reported verbatim)
assert mln("live fetch failed; last snapshot 3d old", date(2026, 7, 10), None, now=NOW) == "live fetch failed; last snapshot 3d old"
# no price note, no snapshot metadata -> conservative "age unknown"
assert mln(None, None, None, now=NOW) == "MFR range age unknown"

# end-to-end: a live-priced MFR line off a stale snapshot carries the [STALE] stamp
_note = mln(None, date(2026, 7, 10), datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc), now=NOW)
assert de._zone_summary_line("MFR", 100, 90, 110, stale_note=_note) == \
    "[STALE: MFR range 3d old] " + clean

print("guard tests PASS")
