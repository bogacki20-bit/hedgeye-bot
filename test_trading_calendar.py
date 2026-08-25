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


# ── 10. CORRELATION DATE ALIGNMENT — the SECOND defect from the same cause ──
print("\n10. a 24/7 series must not be paired positionally against equities:")
from tools.eod_stat_pack import align_on_dates, corr_over
from datetime import timedelta as _td

# equities: weekdays only. crypto: every day. Same nominal length.
eq_d, cr_d, eq_c, cr_c = [], [], [], []
d = date(2026, 1, 1)
px = 100.0
while len(cr_d) < 200:
    px += 0.5
    cr_d.append(d); cr_c.append(px)
    if d.weekday() < 5:
        eq_d.append(d); eq_c.append(px)
    d += _td(days=1)
check("crypto has more bars than equities", len(cr_d) > len(eq_d), True)
ca, cb = align_on_dates(eq_c, eq_d, cr_c, cr_d)
check("aligned lengths match", len(ca), len(cb))
check("aligned length == the equity grid", len(ca), len(eq_c))
check("no dates passed -> unchanged (back-compat)",
      align_on_dates(eq_c, None, cr_c, None), (eq_c, cr_c))
check("empty intersection -> empty",
      align_on_dates([1.0], [date(2020, 1, 1)], [2.0], [date(2021, 1, 1)]),
      ([], []))

# Isolate ALIGNMENT as the only variable. The 24/7 series carries weekend BARS
# but no weekend MOVEMENT, so on the shared weekday grid its returns are
# identical to the equity's. Date-aligned must therefore be exactly 1.0, and
# anything less is purely the pairing being wrong.
# (My first version of this fixture let the crypto move on weekends and then
# asserted ~1.0. That was a bad assertion, not a bug: after alignment a Fri->Mon
# crypto return legitimately CONTAINS the weekend, so it is genuinely a
# different number. It measured 0.806, which is correct behaviour.)
import random as _rnd
_rnd.seed(7)
eqd, eqc = [], [100.0]
crd, crc = [], []
dd, px = date(2026, 1, 1), 100.0
while len(crd) < 300:
    if dd.weekday() < 5:                      # equities move on weekdays only
        px *= 1 + _rnd.uniform(-1, 1) / 100
        eqd.append(dd)
        if len(eqd) > 1:
            eqc.append(px)
    crd.append(dd)                            # crypto has a bar EVERY day ...
    crc.append(px)                            # ... but is flat over weekends
    dd += _td(days=1)
eqc = eqc[:len(eqd)]
aligned = corr_over(eqc, crc, 90, eqd, crd)
positional = corr_over(eqc, crc, 90)
check("same weekday returns -> aligned corr is 1.0",
      aligned is not None and aligned > 0.999, True)
check("positional pairing destroys it",
      positional is not None and positional < 0.9, True)
print("     aligned %.3f vs positional %.3f" % (aligned, positional))

# same-grid pairs must be untouched by the change
check("same date grid -> alignment is a no-op",
      round(corr_over(eqc, eqc, 30, eqd, eqd) or 0, 6),
      round(corr_over(eqc, eqc, 30) or 0, 6))

import inspect as _i
_src = _i.getsource(eod.build_eod_pack)
check("pack passes dates into corr_over", "corr_over(ac, rc, w, ad, rd)" in _src,
      True)

# ── 11. book age counts TRADING days ────────────────────────────────────────
print("\n11. book age in trading days:")
from tools.book_freshness import age_days as _age
check("Fri book read Sunday = 0 sessions", _age(date(2026, 8, 14), date(2026, 8, 16)), 0)
check("Fri book read Monday = 1 session", _age(date(2026, 8, 14), date(2026, 8, 17)), 1)
check("Fri 8/7 read Sun 8/16 = 5 sessions", _age(date(2026, 8, 7), date(2026, 8, 16)), 5)
check("same day = 0", _age(date(2026, 8, 14), date(2026, 8, 14)), 0)
check("None -> None", _age(None, date(2026, 8, 16)), None)
from tools.book_freshness import is_stale as _stale
check("Fri book on Sunday is NOT stale", _stale(date(2026, 8, 14), date(2026, 8, 16)), False)
check("a 5-session-old book IS stale", _stale(date(2026, 8, 7), date(2026, 8, 16)), True)

# ── 12. §2.1 prior levels are rendered (the HY diagnostic) ─────────────────
print("\n12. prior levels beside deltas (makes flat-vs-broken visible):")
# DISTINCT dates, CONSTANT value -- that is what a frozen series looks like.
# The first version of this fixture repeated one date 30 times, which is not a
# real FRED shape and which date-indexing correctly refuses to resolve.
obs_flat = [((date(2026, 7, 1) + _td(days=i)).isoformat(), 2.71)
            for i in range(40)]
lc = eod.level_changes(obs_flat)
check("a frozen series is FLAGGED", lc["frozen"], True)
check("its deltas are genuinely zero", (lc["1D"], lc["1W"], lc["1M"]), (0.0, 0.0, 0.0))
check("prior levels are carried", lc["1M_level"], 2.71)
check("last observation date carried", lc["last_date"], "2026-08-09")
obs_live = [("2026-07-%02d" % (i + 1), 2.60 + i * 0.01) for i in range(30)]
lc2 = eod.level_changes(obs_live)
check("a live series is NOT flagged frozen", lc2["frozen"], False)
rendered = eod.format_rates_credit([("HY OAS", lc)], {})
check("FROZEN warning reaches the output", "FROZEN" in rendered, True)
check("prior-level columns rendered", "1M ago" in rendered, True)
check("live series shows no FROZEN warning",
      "FROZEN" in eod.format_rates_credit([("BBB-10y", lc2)], {}), False)


# ── 13. DETERMINISM: date-indexed windows survive a dropped bar ─────────────
print("\n13. windows are indexed by DATE, not row offset:")
from tools.eod_stat_pack import (asof_index, pct_return_asof, pct_return,
                                 WINDOW_DAYS, returns_row)

# a clean weekday series, and the SAME series with one interior bar dropped --
# exactly what yfinance does between calls (SPHB 627 rows then 630).
# Returns must VARY. My first version compounded at a constant 0.1%/bar, which
# makes a row-offset return identical no matter which rows it spans -- the
# fixture could not have shown the bug it was written to show.
_rnd.seed(11)
d0 = date(2026, 1, 1)
full_d, full_c = [], []
dd, px = d0, 100.0
while len(full_d) < 200:
    if dd.weekday() < 5:
        px *= 1 + _rnd.uniform(-1.5, 1.5) / 100
        full_d.append(dd); full_c.append(px)
    dd += _td(days=1)
# The dropped bar must fall INSIDE the window being measured. My first version
# dropped index 150 while the 21-row window covered only the last 21 rows, so
# the gap was never spanned and both methods agreed -- a fixture that proved
# nothing. Real yfinance drops land anywhere, recent bars included.
drop_at = len(full_d) - 10
gap_d = full_d[:drop_at] + full_d[drop_at + 1:]
gap_c = full_c[:drop_at] + full_c[drop_at + 1:]
check("the two frames differ by exactly one row",
      len(full_d) - len(gap_d), 1)

print("     ROW-OFFSET (old behaviour) -- the bug:")
old_full = pct_return(full_c, 21)
old_gap = pct_return(gap_c, 21)
check("row offset gives DIFFERENT answers", abs(old_full - old_gap) > 1e-9, True)
print("       full %.4f%%  vs gapped %.4f%%" % (old_full * 100, old_gap * 100))

print("     DATE-INDEXED (new behaviour) -- the fix:")
new_full = pct_return_asof(full_c, full_d, 28)
new_gap = pct_return_asof(gap_c, gap_d, 28)
check("date indexing gives the SAME answer", abs(new_full - new_gap) < 1e-12, True)
print("       full %.4f%%  vs gapped %.4f%%" % (new_full * 100, new_gap * 100))

check("asof_index lands on the last bar <= target",
      full_d[asof_index(full_d, 28)] <= full_d[-1] - _td(days=28), True)
check("and on the LATEST such bar",
      full_d[asof_index(full_d, 28) + 1] > full_d[-1] - _td(days=28), True)
check("28d window == Hedgeye's '4 weeks ago'",
      dict(WINDOW_DAYS)["1M"], 28)
check("no window is a row count any more",
      sorted(dict(WINDOW_DAYS).values()), [1, 7, 28, 91, 182])
check("returns_row is date-indexed end to end",
      returns_row(full_c, full_d)["1M"], returns_row(gap_c, gap_d)["1M"])
check("empty input -> None", pct_return_asof([], [], 28), None)
check("series shorter than the window -> None",
      pct_return_asof(full_c[-3:], full_d[-3:], 182), None)

print("\n14. correlations are date-BOUNDED, not last-N-rows:")
# a genuinely independent second series (a constant multiple would be a
# degenerate, zero-variance pair and pearson correctly refuses those)
_rnd.seed(23)
bc, q = [], 50.0
for _ in full_c:
    q *= 1 + _rnd.uniform(-1.2, 1.2) / 100
    bc.append(q)
bc_gap = bc[:drop_at] + bc[drop_at + 1:]
c_full = corr_over(full_c, bc, 90, full_d, full_d)
c_gap = corr_over(gap_c, bc_gap, 90, gap_d, gap_d)
check("both windows resolve", (c_full is not None, c_gap is not None),
      (True, True))
check("a dropped bar barely moves the correlation",
      abs(c_full - c_gap) < 0.10, True)
print("     full %.3f vs one-bar-gapped %.3f (delta %.3f)"
      % (c_full, c_gap, c_gap - c_full))


# ── 15. PERMANENT DETERMINISM REGRESSION (needs DB; failure if unrunnable) ──
print("\n15. two builds for the same as-of are byte-identical:")


def _data_only(txt):
    """Strip the ONLY line that is legitimately allowed to differ between two
    builds: the build clock. Everything else -- including the fetch-vs-replay
    provenance note -- must match, because once bars are banked BOTH runs
    replay and the note is identical too.

    Tightened 2026-08-16: an earlier version also normalised the provenance
    line, which would have hidden a build that silently refetched instead of
    replaying. That is the exact failure this test exists to catch."""
    return "\n".join(ln for ln in (txt or "").splitlines()
                     if not ln.startswith("BUILT:"))


try:
    import re
    import db_pg as _db
    _db._load_dotenv_fallback()
    from tools.eod_stat_pack import build_eod_pack
    # persist=False (2026-08-25): these three builds used to file three rows
    # into the PRODUCTION artifact ledger on every suite sweep — the source
    # of the retracted 8/24 "scheduled tasks built branch packs" finding.
    build_eod_pack(persist=False)       # ensure bars are banked for this as-of
    b1 = build_eod_pack(persist=False)  # then TWO REPLAY builds, as required
    b2 = build_eod_pack(persist=False)
    d1, d2 = _data_only(b1), _data_only(b2)
    check("BOTH builds replayed from the store",
          ("REPLAYED from store" in b1, "REPLAYED from store" in b2),
          (True, True))
    check("two replay builds are byte-identical apart from the clock",
          d1 == d2, True)
    if d1 != d2:
        for i, (x, y) in enumerate(zip(d1.splitlines(), d2.splitlines())):
            if x != y:
                print("     line %d:\n       A: %s\n       B: %s" % (i, x[:100], y[:100]))
                break
    check("the build clock IS allowed to differ",
          "BUILT:" in b1 and "BUILT:" in b2, True)
    # the resolved window anchors must be present and dated
    check("window anchors are printed", "windows (as-of" in b1, True)
    check("the window definition is stated", "definition:" in b1, True)
    check("anchors name a real session, not a row count",
          "1M=" in b1 and "resolves to the last session" in b1, True)
except Exception as _e:
    print("  !! DETERMINISM TEST COULD NOT RUN (%s) — counted as FAILURE." % _e)
    FAIL += 1

print("\n" + ("ALL PASS" if FAIL == 0 else f"{FAIL} FAILURE(S)"))
sys.exit(1 if FAIL else 0)
