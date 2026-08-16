"""NYSE trading-calendar and bar-date assertion tests.

PURE — no DB, no network. The 2026-08-16 defect (a Sunday printed as a bar
date) must be caught by a test that needs neither.

Run: python test_trading_calendar.py
"""
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.trading_calendar import (duplicate_final_bar, is_trading_day,
                                    last_completed_session, nyse_holidays,
                                    previous_trading_day, validate_bar_date)

FAIL = 0


def check(label, got, want):
    global FAIL
    ok = got == want
    print(f"  {'OK' if ok else 'XX'} {label}: got {got!r}"
          + ("" if ok else f" — want {want!r}"))
    if not ok:
        FAIL += 1


# ── 1. THE ACTUAL BUG ───────────────────────────────────────────────────────
print("1. the 2026-08-16 defect:")
check("2026-08-16 is a Sunday", date(2026, 8, 16).strftime("%A"), "Sunday")
check("2026-08-16 is NOT a trading day", is_trading_day(date(2026, 8, 16)), False)
check("2026-08-14 IS a trading day", is_trading_day(date(2026, 8, 14)), True)
NOW = datetime(2026, 8, 16, 10, 27)          # the real build time
ok, why = validate_bar_date(date(2026, 8, 16), now=NOW)
check("a Sunday bar date FAILS validation", ok, False)
check("reason names the weekend", "weekend" in why, True)
check("reason names the real session", "2026-08-14" in why, True)
ok, why = validate_bar_date(date(2026, 8, 14), now=NOW)
check("the real Friday bar PASSES", ok, True)
check("last completed session at Sun 10:27 ET", last_completed_session(NOW),
      date(2026, 8, 14))

# ── 2. the other failure modes the assertion must catch ─────────────────────
print("\n2. future / lagging / missing:")
check("future bar fails",
      validate_bar_date(date(2026, 8, 17), now=NOW)[0], False)
check("future reason says FUTURE",
      "FUTURE" in validate_bar_date(date(2026, 8, 17), now=NOW)[1], True)
check("lagging bar fails",
      validate_bar_date(date(2026, 8, 12), now=NOW)[0], False)
check("lagging reason counts sessions",
      "session(s)" in validate_bar_date(date(2026, 8, 12), now=NOW)[1], True)
check("missing bar fails", validate_bar_date(None, now=NOW)[0], False)

# ── 3. pre-market timing contract (05:30 ET build) ──────────────────────────
print("\n3. the 05:30 ET timing contract:")
# Monday 05:30 ET -> must resolve to FRIDAY, not "today"
mon = datetime(2026, 8, 17, 5, 30)
check("Mon 05:30 resolves to Fri", last_completed_session(mon), date(2026, 8, 14))
# after the close on a session day, today counts
check("Mon 17:00 resolves to Mon",
      last_completed_session(datetime(2026, 8, 17, 17, 0)), date(2026, 8, 17))
check("Mon 09:00 (pre-close) resolves to Fri",
      last_completed_session(datetime(2026, 8, 17, 9, 0)), date(2026, 8, 14))

# ── 4. holidays ─────────────────────────────────────────────────────────────
print("\n4. NYSE holidays:")
h2026 = nyse_holidays(2026)
check("New Year 2026-01-01", date(2026, 1, 1) in h2026, True)
check("MLK 3rd Mon Jan", date(2026, 1, 19) in h2026, True)
check("Washington 3rd Mon Feb", date(2026, 2, 16) in h2026, True)
check("Good Friday 2026-04-03", date(2026, 4, 3) in h2026, True)
check("Memorial last Mon May", date(2026, 5, 25) in h2026, True)
check("Juneteenth 2026-06-19", date(2026, 6, 19) in h2026, True)
check("July 4 2026 falls Sat -> observed Fri 7/3",
      date(2026, 7, 3) in h2026, True)
check("Labor 1st Mon Sep", date(2026, 9, 7) in h2026, True)
check("Thanksgiving 4th Thu Nov", date(2026, 11, 26) in h2026, True)
check("Christmas 2026-12-25 (Friday)", date(2026, 12, 25) in h2026, True)
check("a holiday is not a trading day", is_trading_day(date(2026, 12, 25)), False)
check("day after Thanksgiving IS a session (half day, bar exists)",
      is_trading_day(date(2026, 11, 27)), True)
# the Jan-1-on-Saturday exception
h2022 = nyse_holidays(2022)
check("Jan 1 2022 was a Sat -> NOT observed on Dec 31 2021",
      date(2021, 12, 31) in h2022, False)
check("Juneteenth not observed before 2022",
      date(2021, 6, 18) in nyse_holidays(2021), False)
check("previous_trading_day skips the weekend",
      previous_trading_day(date(2026, 8, 17)), date(2026, 8, 14))
check("previous_trading_day skips a holiday",
      previous_trading_day(date(2026, 12, 28)), date(2026, 12, 24))

# ── 5. duplicate / forward-filled final bar ─────────────────────────────────
print("\n5. duplicate final bar (the partial 'today' row):")
dup_bars = {s: {"closes": [10.0, 11.0, 11.0], "dates": []}
            for s in ("SPY", "TLT", "XLU", "HYG")}
is_dup, detail = duplicate_final_bar(dup_bars)
check("all symbols unchanged -> duplicate", is_dup, True)
check("detail explains it", "forward-fill" in detail, True)
mixed = dict(dup_bars)
mixed["SPY"] = {"closes": [10.0, 11.0, 12.5], "dates": []}
check("one symbol moving -> NOT a duplicate",
      duplicate_final_bar(mixed)[0], False)
check("too few symbols -> declines to judge",
      duplicate_final_bar({"SPY": {"closes": [1.0, 1.0]}})[0], False)

# ── 6. the guard is wired into the pack ─────────────────────────────────────
print("\n6. wired into build_eod_pack:")
import inspect
import tools.eod_stat_pack as eod
src = inspect.getsource(eod.build_eod_pack)
check("calls validate_bar_date", "validate_bar_date" in src, True)
check("calls duplicate_final_bar", "duplicate_final_bar" in src, True)
check("prints DATA AS OF separately", "DATA AS OF" in src, True)
check("prints BUILT separately", "BUILT:" in src, True)
check("blocks rather than printing a table", "EOD PACK BLOCKED" in src, True)
check("pack asks for the correctly-scoped banner",
      "carries_positions=False" in src, True)

# The banner must describe the document it appears in. Assert on the RENDERED
# string, not on module source -- the first version of this test matched its
# own explanatory comment and reported a false failure.
print("\n6b. the banner no longer cries wolf (rendered, not source):")
from tools.book_freshness import status_line as _sl
STALE = date(2026, 8, 7)
rep = _sl(STALE, date(2026, 8, 16), carries_positions=True)
pack = _sl(STALE, date(2026, 8, 16), carries_positions=False)
check("report.py banner DOES promise figures below",
      "figures below" in rep, True)
check("EOD banner does NOT promise figures below",
      "figures below" in pack, False)
check("EOD banner says nothing here is affected",
      "nothing" in pack and "affected" in pack, True)
check("EOD banner still points at where it DOES matter",
      "REPORT" in pack, True)
check("both still state the date", ("2026-08-07" in rep, "2026-08-07" in pack),
      (True, True))
check("both still shout", (rep.startswith("!!"), pack.startswith("!!")),
      (True, True))
fresh = _sl(date(2026, 8, 15), date(2026, 8, 16), carries_positions=False)
check("fresh is identical either way",
      fresh, _sl(date(2026, 8, 15), date(2026, 8, 16), carries_positions=True))
check("unknown date shouts in both scopes",
      (_sl(None, carries_positions=True).startswith("!!"),
       _sl(None, carries_positions=False).startswith("!!")), (True, True))

# ── 7. §1.2 spread construction ─────────────────────────────────────────────
print("\n7. BBB is a date-aligned subtraction, not a raw OAS:")
check("BBB-10y is a derived spread",
      any(l == "BBB-10y" for l, _, _ in eod.FRED_SPREADS), True)
check("it subtracts DGS10",
      [b for l, a, b in eod.FRED_SPREADS if l == "BBB-10y"], ["DGS10"])
check("it uses the BBB YIELD series, not the OAS",
      [a for l, a, b in eod.FRED_SPREADS if l == "BBB-10y"], ["BAMLC0A4CBBBEY"])
check("the raw BBB OAS is no longer a printed series",
      any(sid == "BAMLC0A4CBBB" for _, sid, _ in eod.FRED_SERIES), False)
check("HY OAS retained (correct construction)",
      any(sid == "BAMLH0A0HYM2" for _, sid, _ in eod.FRED_SERIES), True)
# date alignment, not index alignment
m = [("2026-08-12", 5.50), ("2026-08-13", 5.60), ("2026-08-14", 5.55)]
s = [("2026-08-12", 4.60), ("2026-08-14", 4.64)]          # 8/13 MISSING
out = eod.spread_series(m, s)
check("aligns on date, skipping the gap", round(out["last"], 4), 0.91)
check("delta uses the aligned pair (bp)", round(out["1D"], 1), 1.0)
check("empty input -> empty dict", eod.spread_series([], s), {})

# ── 8. THE ROOT CAUSE: a 24/7 symbol must not define the session ───────────
print("\n8. BTC-USD must not set the session date (the actual 8/16 bug):")
from tools.trading_calendar import resolve_session_date
FRI, SUN = date(2026, 8, 14), date(2026, 8, 16)
frame = {s: {"dates": [FRI], "closes": [1.0]}
         for s in ("SPY", "TLT", "XLU", "HYG", "USO", "QQQ")}
frame["BTC-USD"] = {"dates": [SUN], "closes": [1.0]}     # 24/7, real Sunday bar
session, off = resolve_session_date(frame)
check("resolves to the EQUITY session, not the crypto bar", session, FRI)
check("naive max() would have said Sunday",
      max(b["dates"][-1] for b in frame.values()), SUN)
check("BTC-USD reported as off-consensus, not dropped silently",
      off, ["BTC-USD"])
check("the resolved date passes validation",
      validate_bar_date(session, now=datetime(2026, 8, 16, 10, 27))[0], True)
# a LAGGING equity symbol is also surfaced, and does not drag the session back
frame2 = dict(frame)
frame2["LAGGY"] = {"dates": [date(2026, 8, 12)], "closes": [1.0]}
s2, off2 = resolve_session_date(frame2)
check("a lagging symbol does not move the session", s2, FRI)
check("lagging symbol is named", "LAGGY" in off2, True)
# all-weekend frame (crypto only) still returns something rather than crashing
only_crypto = {"BTC-USD": {"dates": [SUN], "closes": [1.0]}}
check("crypto-only frame degrades gracefully",
      resolve_session_date(only_crypto)[0], SUN)
check("and that date then FAILS validation, so it still blocks",
      validate_bar_date(SUN, now=datetime(2026, 8, 16, 10, 27))[0], False)
check("empty frame -> None", resolve_session_date({})[0], None)

print("\n9. the pack resolves the session, not max():")
check("uses resolve_session_date", "resolve_session_date(bars)" in src, True)
check("no naive max over all symbols",
      'max((b["dates"][-1] for b in bars.values()' in src, False)
check("off-consensus symbols are printed", "off the session" in src, True)
check("archives the artifact", "_persist_pack" in src, True)
check("archives the BLOCKED run too", "_persist_pack(blocked" in src, True)
check("artifact records the deployed sha",
      "_deployed_sha" in inspect.getsource(eod), True)

print("\n" + ("ALL PASS" if FAIL == 0 else f"{FAIL} FAILURE(S)"))
sys.exit(1 if FAIL else 0)
