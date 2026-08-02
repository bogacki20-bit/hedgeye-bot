"""test_quad_tape.py — QUAD vs TAPE (tools/quad_tape.py).

Pure logic only: no network, no DB, no yfinance. The one file-touching test
reads the committed config/hedgeye_doctrine.yaml, which is repo data, not I/O.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.quad_tape import (  # noqa: E402
    DETAIL_PREF, MIN_NAMES, QUADS, TIE_EPS, avg_ranks, doctrine_tickers,
    fit_all_quads, format_quad_tape, headline, load_table, pair, quad_index,
    quad_tape_block, rank_gaps, rho_critical, spearman, verdict,
)
from tools.quad_regime import quad_staleness  # noqa: E402

FAILED = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  {got!r}\n        want {want!r}")
        FAILED.append(name)


def approx(name, got, want, tol=1e-9):
    ok = got is not None and abs(got - want) < tol
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        got  {got!r}\n        want ~{want!r}")
        FAILED.append(name)


# ── quad_staleness (B1) ─────────────────────────────────────────────────────
# The 2026-08-02 bug: the pack printed "monthly=Quad 4 (last confirm
# 2026-07-31)" at the August turn when the confirmed monthly Quad was Quad 3.
# quad_regime_history has no column for WHICH MONTH a monthly_quad is for, so
# the read path returned the latest row and July's monthly silently became
# August's. Staleness is the calendar question that makes that visible.
import datetime as _dt  # noqa: E402


def _stale(eff, asof):
    return quad_staleness(_dt.date.fromisoformat(eff) if eff else None,
                          _dt.date.fromisoformat(asof))


# THE case. One day apart, but across a month boundary.
_b1 = _stale("2026-07-31", "2026-08-02")
check("B1: monthly confirmed in July is stale in August", _b1["monthly_stale"],
      True)
# The quarterly axis was CORRECT that day (Quad 4, still Q3) and must not be
# swept up — over-flagging trains the operator to ignore the flag.
check("B1: quarterly confirmed 7/31 is fresh in Q3", _b1["quarterly_stale"],
      False)
check("B1: reports the confirmation date", _b1["confirmed_on"], "2026-07-31")
check("B1: explains itself", "before this month" in _b1["reason"], True)

# Elapsed DAYS is the wrong measure and would invert both of these.
check("staleness is calendar, not elapsed: 1 day across a month = stale",
      _stale("2026-07-31", "2026-08-01")["monthly_stale"], True)
check("staleness is calendar, not elapsed: 27 days inside a month = fresh",
      _stale("2026-08-01", "2026-08-28")["monthly_stale"], False)

check("same day is fresh", _stale("2026-08-02", "2026-08-02")["monthly_stale"],
      False)
_q = _stale("2026-06-30", "2026-08-02")
check("a prior quarter is stale on both axes",
      (_q["monthly_stale"], _q["quarterly_stale"]), (True, True))
check("prior-quarter reason names the quarter",
      "before this quarter" in _q["reason"], True)
# Quarter boundaries: Q1 Jan-Mar, Q2 Apr-Jun, Q3 Jul-Sep, Q4 Oct-Dec.
check("Mar 31 -> Apr 1 crosses a quarter",
      _stale("2026-03-31", "2026-04-01")["quarterly_stale"], True)
check("Jul 1 -> Sep 30 is the same quarter",
      _stale("2026-07-01", "2026-09-30")["quarterly_stale"], False)
check("Dec 31 -> Jan 1 crosses a year",
      _stale("2025-12-31", "2026-01-01")["monthly_stale"], True)
# An unconfirmable Quad must never present as confirmed.
check("no timestamp is stale on both axes",
      (_stale(None, "2026-08-02")["monthly_stale"],
       _stale(None, "2026-08-02")["quarterly_stale"]), (True, True))
check("no timestamp says so", _stale(None, "2026-08-02")["reason"],
      "no confirmation timestamp")
# asof defaults to TODAY in ET (not "unknown") — the pack always asks about now.
check("asof defaults to today",
      quad_staleness(_dt.date(2026, 8, 2), None),
      quad_staleness(_dt.date(2026, 8, 2), _dt.date.today()))
# Railway runs UTC, so date.today() there is already tomorrow after 20:00 ET.
# A confirmation made this evening ET must not read as yesterday's.
check("a tz-aware evening confirmation keeps its ET date",
      quad_staleness(_dt.datetime(2026, 7, 31, 20, 30,
                                  tzinfo=_dt.timezone(_dt.timedelta(hours=-4))),
                     _dt.date(2026, 8, 2))["confirmed_on"], "2026-07-31")
check("...and is therefore stale, not fresh",
      quad_staleness(_dt.datetime(2026, 7, 31, 20, 30,
                                  tzinfo=_dt.timezone(_dt.timedelta(hours=-4))),
                     _dt.date(2026, 8, 2))["monthly_stale"], True)
# The same instant expressed in UTC is the SAME July confirmation.
check("UTC and ET spellings of one instant agree",
      quad_staleness(_dt.datetime(2026, 8, 1, 0, 30, tzinfo=_dt.timezone.utc),
                     _dt.date(2026, 8, 2))["confirmed_on"], "2026-07-31")
# A quarter rollover hides in the same trap: 6/30 evening ET -> 7/1 UTC.
check("a quarter rollover survives the UTC trap",
      quad_staleness(_dt.datetime(2026, 7, 1, 0, 30, tzinfo=_dt.timezone.utc),
                     _dt.date(2026, 8, 2))["quarterly_stale"], True)
# Strings (a raw text column) must parse, not raise.
check("ISO string with offset", quad_staleness("2026-07-31T20:30:00-04:00",
      _dt.date(2026, 8, 2))["confirmed_on"], "2026-07-31")
check("bare ISO date string", quad_staleness("2026-07-31",
      _dt.date(2026, 8, 2))["confirmed_on"], "2026-07-31")
check("garbage is stale, not a crash",
      quad_staleness("not-a-date", _dt.date(2026, 8, 2))["monthly_stale"], True)
check("garbage says it could not parse",
      "unparseable" in quad_staleness(12345, _dt.date(2026, 8, 2))["reason"],
      True)
# A datetime works the same as a date — the DB column is TIMESTAMPTZ.
check("accepts a datetime",
      quad_staleness(_dt.datetime(2026, 7, 31, 16, 30), _dt.date(2026, 8, 2))
      ["monthly_stale"], True)
# Future-dated confirmation: not stale, but flagged as abnormal.
_ahead = _stale("2026-09-01", "2026-08-02")
check("a future confirmation is not stale", _ahead["monthly_stale"], False)
check("a future confirmation is called out", "AHEAD of" in _ahead["reason"],
      True)


# ── quad_index ──────────────────────────────────────────────────────────────
check("quad_index 'Quad 4'", quad_index("Quad 4"), 3)
check("quad_index 'quad1'", quad_index("quad1"), 0)
check("quad_index 'Q2'", quad_index("Q2"), 1)
check("quad_index '3'", quad_index("3"), 2)
check("quad_index int", quad_index(4), 3)
check("quad_index None", quad_index(None), None)
check("quad_index garbage", quad_index("Quad 9"), None)
check("quad_index empty", quad_index(""), None)

# ── avg_ranks ───────────────────────────────────────────────────────────────
check("avg_ranks strictly increasing", avg_ranks([1.0, 2.0, 3.0]), [1.0, 2.0, 3.0])
check("avg_ranks reversed", avg_ranks([3.0, 2.0, 1.0]), [3.0, 2.0, 1.0])
# A 2-way tie for the bottom two places shares (1+2)/2 = 1.5.
check("avg_ranks pair tie", avg_ranks([5.0, 1.0, 1.0]), [3.0, 1.5, 1.5])
# All tied -> everyone gets the middle. This is what makes spearman return None
# rather than a correlation built from list order.
check("avg_ranks all tied", avg_ranks([2.0, 2.0, 2.0]), [2.0, 2.0, 2.0])
check("avg_ranks empty", avg_ranks([]), [])

# The doctrine table has REAL ties — XLP and XLV are both 3.2 in Quad 4. If ties
# ever silently became order-dependent, the yaml's key order would leak into the
# correlation. This is the regression guard for that.
_t = avg_ranks([-0.7, 3.2, 3.2, -0.7, 1.0])
check("avg_ranks two separate ties", _t, [1.5, 4.5, 4.5, 1.5, 3.0])

# ── spearman ────────────────────────────────────────────────────────────────
approx("spearman perfect", spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
approx("spearman inverse", spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)
# Rank correlation ignores magnitude entirely — that is the whole point, since
# the doctrine numbers are quarterly averages and the realized side is not.
approx("spearman is monotone-invariant",
       spearman([1, 2, 3, 4], [1, 100, 10000, 1e9]), 1.0)
check("spearman too few points", spearman([1, 2], [3, 4]), None)
check("spearman flat x", spearman([5, 5, 5, 5], [1, 2, 3, 4]), None)
check("spearman flat y", spearman([1, 2, 3, 4], [7, 7, 7, 7]), None)
# Truncating to the shorter list would return exactly -1.00 here — a caller-side
# alignment bug rendered as a perfect correlation. Refuse instead.
check("spearman refuses mismatched lengths",
      spearman([1, 2, 3, 4, 5, 6], [6, 5, 4, 3, 2, 1, 99, 98]), None)

# ── rho_critical ────────────────────────────────────────────────────────────
approx("rho_critical n=31", rho_critical(31), 1.96 / (30 ** 0.5), 1e-12)
check("rho_critical n=2", rho_critical(2), None)
check("rho_critical n=0", rho_critical(0), None)
_c31 = rho_critical(31)
check("rho_critical shrinks with n", rho_critical(101) < _c31, True)

# ── pair ────────────────────────────────────────────────────────────────────
# Quad 3's column is deliberately NOT monotone in the same name order as Quad 4.
# An earlier version of this fixture had both columns increasing together, which
# made them rank-identical: every tape scored 1.00 on both and 'best' was decided
# by dict order. The fixture, not the code, was wrong — but a fixture that cannot
# distinguish two quads cannot test that the code distinguishes them.
_TABLE = {
    "AAA": [9.0, 5.0, 2.0, -5.0],
    "BBB": [7.0, 4.0, 6.0, -3.0],
    "CCC": [5.0, 3.0, -1.0, -1.0],
    "DDD": [3.0, 2.0, 5.0, 1.0],
    "EEE": [1.0, 1.0, 0.0, 3.0],
    "FFF": [0.0, 0.0, 4.0, 4.0],
    "GGG": [-1.0, -1.0, 1.0, 5.0],
    "HHH": [-3.0, -2.0, 3.0, 7.0],
}
_names, _vals = pair({"AAA": 0.01, "BBB": None, "ZZZ": 0.02}, _TABLE)
check("pair drops None returns", _names, ["AAA"])
check("pair drops names absent from table", "ZZZ" not in _names, True)
check("pair returns aligned values", _vals, [0.01])
# NaN is not None, so a None-only filter keeps it and it sorts into an arbitrary
# rank slot — producing a fully confident rho off an undefined return.
_nan = float("nan")
check("pair drops NaN", pair({"AAA": _nan, "BBB": 0.02}, _TABLE)[0], ["BBB"])
check("pair drops inf",
      pair({"AAA": float("inf"), "BBB": 0.02}, _TABLE)[0], ["BBB"])
check("pair drops -inf",
      pair({"AAA": float("-inf"), "BBB": 0.02}, _TABLE)[0], ["BBB"])
check("pair drops bool", pair({"AAA": True, "BBB": 0.02}, _TABLE)[0], ["BBB"])

# ── fit_all_quads ───────────────────────────────────────────────────────────
# Tape that exactly reproduces the Quad 4 ordering.
_q4_perfect = {t: v[3] / 100.0 for t, v in _TABLE.items()}
_fit = fit_all_quads(_q4_perfect, _TABLE)
check("fit names counted", _fit["names"], 8)
approx("fit rho=1 for the matching quad", _fit["rho"]["Quad 4"], 1.0)
check("fit picks the matching quad", _fit["best"], "Quad 4")
check("fit scores all four quads", sorted(_fit["rho"]), sorted(QUADS))
# Quad 1's ordering is the near-mirror of Quad 4's, so it should score negative.
check("fit rho negative for the opposite quad", _fit["rho"]["Quad 1"] < 0, True)

# All four rhos must come from the SAME name set, or 'best' is an artifact of
# which names happened to have data.
_thin = dict(_q4_perfect)
_thin.pop("AAA")
_fit_thin = fit_all_quads(_thin, _TABLE)
check("fit uses one name set for all quads", _fit_thin["names"], 7)

# Below MIN_NAMES the section refuses rather than printing a rho off 4 names.
_few = {"AAA": 0.01, "BBB": 0.02, "CCC": 0.03, "DDD": 0.04}
_fit_few = fit_all_quads(_few, _TABLE)
check("fit refuses under MIN_NAMES", _fit_few["names"] < MIN_NAMES, True)
check("fit under MIN_NAMES has no rho", set(_fit_few["rho"].values()), {None})
check("fit under MIN_NAMES has no best", _fit_few["best"], None)
check("fit under MIN_NAMES has no tied", _fit_few["tied"], [])
check("fit on empty realized", fit_all_quads({}, _TABLE)["best"], None)
check("fit carries the per-n floor", _fit["crit"], rho_critical(8))
# Names that fell out are recorded so the caller can NAME them rather than
# letting n shrink silently.
check("fit records dropped names", fit_all_quads(_thin, _TABLE)["dropped"],
      ["AAA"])

# A winner that does not clear its own floor is not a winner. On most days all
# four rho sit near zero and the argmax is whichever column caught the most
# noise; naming it would be an answer where there is none.
# This ordering was found by brute force over all 8! permutations as the one
# LEAST correlated with any of the four quad columns: max|rho| = 0.024 against a
# floor of 0.741. Not eyeballed — an earlier hand-picked "noise" fixture turned
# out to be the exact Quad 4 ordering, which is precisely the mistake this test
# exists to catch.
_noise = {t: v * 1e-4 for t, v in
          zip(sorted(_TABLE), (0, 3, 5, 7, 6, 4, 1, 2))}
_fit_noise = fit_all_quads(_noise, _TABLE)
_top_noise = max(r for r in _fit_noise["rho"].values() if r is not None)
check("noise fixture really is below the floor",
      _top_noise < _fit_noise["crit"], True)
check("no winner crowned below the floor", _fit_noise["best"], None)
check("no tie set below the floor", _fit_noise["tied"], [])

# ── ties between quads ──────────────────────────────────────────────────────
# Quad 1 and Quad 2 are both risk-on; their orderings here are identical, so a
# risk-on tape scores them the same. Crowning one would invent a distinction.
_TIE_TABLE = {t: [v[0], v[0] - 0.5, v[2], v[3]] for t, v in _TABLE.items()}
_riskon = {t: v[0] / 100.0 for t, v in _TIE_TABLE.items()}
_fit_tie = fit_all_quads(_riskon, _TIE_TABLE)
check("tie: both risk-on quads reported", sorted(_fit_tie["tied"]),
      ["Quad 1", "Quad 2"])
check("tie: best is still deterministic", _fit_tie["best"], "Quad 1")
check("tie: format shows both", "Q1≈Q2" in
      format_quad_tape([("1M", _fit_tie)], "Quad 2", [], "n/a"), True)
# A clear winner must NOT be reported as tied.
check("no false tie when one quad wins outright",
      fit_all_quads(_q4_perfect, _TABLE)["tied"], ["Quad 4"])
# TIE_EPS was 0.05 and had to be widened: sd(rho_Q1 - rho_Q2) under a random
# tape on the real 34-name table is 0.117, so 0.05 crowned a single winner 68%
# of the time on pure noise. Anything back under ~0.15 reopens that.
check("TIE_EPS is wide enough to mean something", TIE_EPS >= 0.15, True)

# ── rank_gaps ───────────────────────────────────────────────────────────────
# HHH is Quad 4's TOP name (+7.0) but bottom of the tape -> big positive gap.
_diverge = dict(_q4_perfect)
_diverge["HHH"] = -0.20
_gaps = rank_gaps(_diverge, _TABLE, "Quad 4")
_byT = {g["ticker"]: g for g in _gaps}
# B2: rank 1 = BEST. Under the old 1=worst form the biggest laggard printed as
# "1/34", which reads as "ranked number one" and means the exact opposite.
check("rank 1 is the doctrine's BEST name", _byT["HHH"]["exp_rank"], 1.0)
check("rank n is the doctrine's WORST name", _byT["AAA"]["exp_rank"], 8.0)
check("tape rank 1 is the best performer",
      min(_gaps, key=lambda g: g["act_rank"])["ticker"], "GGG")
check("ranks span 1..n",
      (min(g["exp_rank"] for g in _gaps), max(g["exp_rank"] for g in _gaps)),
      (1.0, 8.0))
# Sign convention is unchanged by the flip: positive still means the Quad likes
# it more than the tape does.
check("rank_gaps lag is positive", _byT["HHH"]["gap"] > 0, True)
check("HHH lag is the full width of the table", _byT["HHH"]["gap"], 7.0)
check("rank_gaps carries n", _gaps[0]["n"], 8)
check("rank_gaps covers every paired name", len(_gaps), 8)
check("rank_gaps gaps sum to zero",
      abs(sum(g["gap"] for g in _gaps)) < 1e-9, True)
# F3: sorted by |gap| descending, so the widest divergence in EITHER direction
# is row one — the old form sorted by signed gap and buried big negatives.
check("rank_gaps sorted by |gap| descending",
      [abs(g["gap"]) for g in _gaps] ==
      sorted([abs(g["gap"]) for g in _gaps], reverse=True), True)
check("rank_gaps widest gap is row one", _gaps[0]["ticker"], "HHH")
# A big NEGATIVE must outrank a small positive — the bug the |gap| sort fixes.
_bid = {t: v[3] / 100.0 for t, v in _TABLE.items()}
_bid["AAA"] = 0.50                      # Quad 4's WORST name, tape's best
check("a large negative gap sorts to the top",
      rank_gaps(_bid, _TABLE, "Quad 4")[0]["ticker"], "AAA")
check("that top row is negative",
      rank_gaps(_bid, _TABLE, "Quad 4")[0]["gap"] < 0, True)
# Ties must not depend on dict order.
check("rank_gaps ties broken by ticker",
      rank_gaps(_bid, _TABLE, "Quad 4") == rank_gaps(dict(reversed(
          list(_bid.items()))), _TABLE, "Quad 4"), True)
check("rank_gaps no quad -> empty", rank_gaps(_diverge, _TABLE, None), [])
check("rank_gaps bad quad -> empty", rank_gaps(_diverge, _TABLE, "Quad 7"), [])
check("rank_gaps under MIN_NAMES -> empty", rank_gaps(_few, _TABLE, "Quad 4"), [])

# ── headline (F7) ───────────────────────────────────────────────────────────
_head = headline(_gaps, "Quad 4")
check("headline names the widest gap", "HHH" in _head, True)
check("headline says which way it diverges", "LAGGING its billing" in _head, True)
check("headline on a bid-up name reads the other way",
      "BID ABOVE its billing" in headline(rank_gaps(_bid, _TABLE, "Quad 4"),
                                          "Quad 4"), True)
check("headline flags an unconfirmed quad",
      "[UNCONFIRMED QUAD]" in headline(_gaps, "Quad 4", stale=True), True)
check("headline with no gaps still returns a line",
      headline([], "Quad 4").startswith("HEADLINE:"), True)

# ── verdict ─────────────────────────────────────────────────────────────────
# verdict scores the HEADER Quad's own column — one test at the stated floor.
def _fit_of(**rho):
    return {"names": 31, "crit": rho_critical(31), "best": None, "tied": [],
            "rho": {q: rho.get(q.replace("Quad ", "q")) for q in QUADS}}


_CRIT31 = rho_critical(31)                       # 1.96/sqrt(30) = 0.358
check("verdict CONFIRM on the header column",
      verdict(_fit_of(q4=0.80, q2=0.10), "Quad 4"), "CONFIRM")
# The loudest state there is: the tape trading the INVERSE of the header Quad.
# The old argmax form could never produce this, because it only ever saw the
# maximum of the four columns, which is provably bounded above -0.34 here.
check("verdict DIVERGE means inverse of the header",
      verdict(_fit_of(q4=-0.80, q1=0.60), "Quad 4"), "DIVERGE")
check("verdict NOISE below the floor",
      verdict(_fit_of(q4=0.18), "Quad 4"), "NOISE")
check("verdict NOISE just under the floor",
      verdict(_fit_of(q4=_CRIT31 - 1e-9), "Quad 4"), "NOISE")
check("verdict CONFIRM exactly at the floor",
      verdict(_fit_of(q4=_CRIT31), "Quad 4"), "CONFIRM")
# THE regression that matters. `_quad_for` returns None on any DB error, on an
# empty quad_regime_history, and for dates before QUAD_CLEAN_START. The pack
# would print "QUAD: unavailable" and then DIVERGE against nothing on the next
# line — and the previous version of this file asserted that as correct.
check("verdict says no header, never DIVERGE, when the Quad is unknown",
      verdict(_fit_of(q1=0.90), None), "no header")
check("verdict says no header on an unparseable Quad",
      verdict(_fit_of(q1=0.90), "Quad 9"), "no header")
check("verdict says no header on an empty string",
      verdict(_fit_of(q1=0.90), ""), "no header")
# A header Quad that another column outranks is NOT divergence. Q1 and Q2
# rank-correlate at +0.77 on the real table, so the argmax flips between them on
# noise; the old form called that DIVERGE ~10% of the time on a correct header.
check("verdict ignores which quad won",
      verdict(_fit_of(q4=0.60, q2=0.66), "Quad 4"), "CONFIRM")
# Non-canonical Quad strings: rank_gaps normalised them and verdict did not, so
# the callouts rendered for Quad 4 while the verdict column read DIVERGE forever.
check("verdict normalises 'Q4'", verdict(_fit_of(q4=0.80), "Q4"), "CONFIRM")
check("verdict normalises '4'", verdict(_fit_of(q4=0.80), "4"), "CONFIRM")
check("verdict normalises int 4", verdict(_fit_of(q4=0.80), 4), "CONFIRM")
check("verdict n/a when the header column has no rho",
      verdict(_fit_of(q1=0.5), "Quad 4"), "n/a")
check("verdict n/a with no floor",
      verdict({"rho": {q: 0.9 for q in QUADS}, "crit": None}, "Quad 4"), "n/a")

# B1: a header carried forward from a prior month is not a claim about THIS
# month, so the verdict is suppressed. On 2026-08-02 the pack printed CONFIRM
# against a Quad 4 header when the confirmed monthly Quad was Quad 3 — correct
# arithmetic on the wrong question, which is worse than no answer.
check("stale header suppresses CONFIRM",
      verdict(_fit_of(q4=0.90), "Quad 4", stale=True), "AWAIT CONFIRM")
check("stale header suppresses DIVERGE",
      verdict(_fit_of(q4=-0.90), "Quad 4", stale=True), "AWAIT CONFIRM")
check("stale header suppresses NOISE too",
      verdict(_fit_of(q4=0.01), "Quad 4", stale=True), "AWAIT CONFIRM")
# Unknown beats stale: with no Quad at all there is nothing to await confirming.
check("unknown quad still reads no header when stale",
      verdict(_fit_of(q1=0.9), None, stale=True), "no header")
check("stale defaults to off", verdict(_fit_of(q4=0.90), "Quad 4"), "CONFIRM")

# ── formatting ──────────────────────────────────────────────────────────────
_fits = [("1W", fit_all_quads(_q4_perfect, _TABLE)),
         ("1M", fit_all_quads(_diverge, _TABLE))]
_txt = format_quad_tape(_fits, "Quad 4", _gaps, "1M")
check("format has the title", "QUAD vs TAPE" in _txt, True)
check("format names the header quad", "header Quad: Quad 4" in _txt, True)
check("format has a column per quad",
      all(q in _txt for q in QUADS), True)
check("format states ranks-only", "magnitudes are not comparable" in _txt, True)
check("format warns it is not a signal", "not a signal" in _txt, True)
check("format warns best fit is a soft read", "Read it" in _txt, True)
check("format shows the lagging name", "HHH" in _txt, True)
check("format labels the window", "1M window" in _txt, True)

# F3: every name prints, not a top-5. Three outliers over thirty flat rows is
# one sector's news; thirty names re-ordering together is a regime turning, and
# a top-5 cannot tell those apart.
check("format prints a row for every name",
      all(f"    {t:<6}" in _txt for t in _TABLE), True)
check("format states the rank convention", "rank 1 = BEST" in _txt, True)
check("format has a gap column", "gap" in _txt, True)
check("format summarises the shape", "shape:" in _txt, True)
check("format calls a concentrated move news",
      "read it as news, not regime" in _txt, True)
# Every name diverging by a third of the table = broad, not news.
_wide = {t: -v[3] / 100.0 for t, v in _TABLE.items()}
check("format calls a broad move a regime turn",
      "consistent with a regime turn" in
      format_quad_tape(_fits, "Quad 4", rank_gaps(_wide, _TABLE, "Quad 4"),
                       "1M"), True)
# A gap that rounds to zero must not print "-0" — a minus sign on a zero reads
# as a direction that isn't there. Half-integer ranks make this reachable.
check("format never prints minus zero", "     -0" not in _txt, True)

# Which number lands under which column. Mutation-checked: swapping exp_rank
# and act_rank in the printed row — and separately in the headline — used to
# leave every test green. "The row exists" is not "the row is right", and this
# is the one place the author already got a direction backwards once.
_row = next(l for l in _txt.split("\n") if l.strip().startswith("HHH"))
_cells = _row.split()
check("divergence row is tkr, doc, tape, gap, exp, act", len(_cells), 6)
check("doc rank column carries the DOCTRINE rank", _cells[1], "1/8")
check("tape rank column carries the TAPE rank", _cells[2], "8/8")
check("gap column is tape minus doc", _cells[3], "+7")
check("exp column is the quarterly expectation", _cells[4], "7.0%")
check("act column is the realized return", _cells[5], "-20.0%")
# Same pin on the headline.
_h = headline(_gaps, "Quad 4")
check("headline doc rank precedes tape rank",
      "doc rank 1/8 vs tape 8/8" in _h, True)

# A gap of zero must not claim a direction. Sorted by |gap|, so a zero at the
# top means the WIDEST gap is zero — the tape is in doctrine order — and the
# old code called that "bid above its billing" on a rho = +1.00 tape.
_perfect_gaps = rank_gaps(_q4_perfect, _TABLE, "Quad 4")
check("perfect tape has no gaps", max(abs(g["gap"]) for g in _perfect_gaps), 0.0)
_hp = headline(_perfect_gaps, "Quad 4")
check("headline on a perfect tape claims no direction",
      "billing" in _hp and "BID ABOVE" not in _hp and "LAGGING" not in _hp, True)
check("headline on a perfect tape says so", "no divergence" in _hp, True)
check("headline on a perfect tape never prints -0", "-0" not in _hp, True)
# ...and the shape line must not contradict the CONFIRM two lines above it.
_ptxt = format_quad_tape([("1M", _fit)], "Quad 4", _perfect_gaps, "1M")
check("shape line does not call a perfect tape 'news'",
      "read it as news" not in _ptxt, True)
check("shape line says the tape is in doctrine order",
      "the tape is in doctrine order" in _ptxt, True)

# Ties print consistently. Banker's rounding gave 4.5 -> 4 but 9.5 -> 10, so
# two structurally identical ties rendered differently.
from tools.quad_tape import _rank  # noqa: E402
check("_rank rounds .5 up at an even place", _rank(4.5), "5")
check("_rank rounds .5 up at an odd place", _rank(9.5), "10")
check("_rank leaves integers alone", (_rank(3.0), _rank(34.0)), ("3", "34"))

# B1 banner
_stale_txt = format_quad_tape(_fits, "Quad 4", _gaps, "1M", stale=True)
check("stale format shouts at the top",
      _stale_txt.split("\n")[1].strip().startswith("⚠⚠"), True)
check("stale format says verdicts are suppressed",
      "Verdicts are SUPPRESSED" in _stale_txt, True)
check("stale format still prints the rho table",
      "Quad 4" in _stale_txt and "floor" in _stale_txt, True)
check("stale format shows AWAIT CONFIRM in every row",
      _stale_txt.count("AWAIT CONFIRM"), len(_fits))
check("stale format never says CONFIRM alone",
      "  CONFIRM" not in _stale_txt.replace("AWAIT CONFIRM", ""), True)
check("non-stale format has no banner", "⚠⚠" not in _txt, True)

# The floor is PER ROW. A single footnote floor with rows judged at their own n
# put two contradictory numbers on one screen: rho=+0.59 labelled NOISE under a
# footnote claiming the floor was 0.34, because that row actually had n=10.
_mixed = format_quad_tape(
    [("1W", fit_all_quads(_q4_perfect, _TABLE)),
     ("MTD", fit_all_quads({t: v for t, v in list(_q4_perfect.items())[:8]},
                           _TABLE))],
    "Quad 4", [], "n/a")
check("format has a floor column", "floor" in _mixed, True)
check("format prints each row's own floor",
      f"{rho_critical(8):.2f}" in _mixed, True)
check("format has no single global floor claim",
      "NOISE = |rho| below" not in _mixed, True)

# Unknown quad: the fit table is still useful, the per-name detail is not
# computable, and the pack must say so rather than printing an empty block.
_txt_noq = format_quad_tape(_fits, None, [], "n/a")
check("format handles unknown quad", "header Quad: unknown" in _txt_noq, True)
check("format explains missing detail", "no per-name detail" in _txt_noq, True)
check("format never DIVERGEs on an unknown quad",
      "DIVERGE" not in _txt_noq.split("DIVERGE at rho")[0]
      + _txt_noq.split("short window.")[-1], True)
check("format shows no-header verdict", "no header" in _txt_noq, True)
# An unparseable Quad must read 'unknown' too, not echo the garbage as if valid.
check("format calls a bad quad unknown",
      "header Quad: unknown" in format_quad_tape(_fits, "Quad 9", [], "n/a"),
      True)
# 'none' rather than a crowned loser when nothing clears the floor.
check("format prints none when no quad clears",
      "none" in format_quad_tape([("1M", _fit_noise)], "Quad 4", [], "n/a"),
      True)

# A window with too few names must SAY it was skipped, not vanish.
_txt_few = format_quad_tape([("1W", fit_all_quads(_few, _TABLE))], "Quad 4",
                            [], "n/a")
check("format says a thin window was skipped", "skipped" in _txt_few, True)

# ── real doctrine table ─────────────────────────────────────────────────────
_real = load_table()
check("load_table returns rows", len(_real) >= 30, True)
check("load_table rows are 4 wide",
      all(len(v) == 4 for v in _real.values()), True)
check("load_table values are floats",
      all(isinstance(x, float) for v in _real.values() for x in v), True)
check("load_table upper-cases keys",
      all(t == t.upper() for t in _real), True)
check("load_table has the known anchors",
      {"SPY", "TLT", "GLD", "SPHB", "XLP"} <= set(_real), True)
check("doctrine_tickers matches the table", set(doctrine_tickers()), set(_real))
check("doctrine_tickers is sorted", doctrine_tickers() == sorted(doctrine_tickers()),
      True)

# The doctrine's own Quad 4 column must score +1 against itself. If someone
# edits the yaml into a shape this module reads differently, this breaks.
_self = {t: v[3] for t, v in _real.items()}
approx("real table self-scores +1 on Quad 4",
       fit_all_quads(_self, _real)["rho"]["Quad 4"], 1.0)
check("real table self-picks Quad 4", fit_all_quads(_self, _real)["best"],
      "Quad 4")

# GBTC's +157.2 in Quad 3 is 17x the next-largest number in the table. Under a
# magnitude-weighted method it would decide the answer by itself; as a rank it
# is worth one place. Dropping it must not move the self-score.
_no_gbtc = {t: v for t, v in _real.items() if t != "GBTC"}
approx("GBTC outlier does not drive the score",
       fit_all_quads({t: v[2] for t, v in _no_gbtc.items()},
                     _no_gbtc)["rho"]["Quad 3"], 1.0)

# ── load_table rejects malformed rows ───────────────────────────────────────
# Fixture file, not the live doctrine — these shapes must never reach the ranker.
_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "_tmp_quad_tape_fixture.yaml")
with open(_YAML, "w", encoding="utf-8") as _fh:
    _fh.write(
        "expected_returns:\n"
        "  GOOD: [1.0, 2.0, 3.0, 4.0]\n"
        "  ok2: [1, 2, 3, 4]\n"
        "  STR: '1234'\n"            # iterates per CHARACTER into [1,2,3,4]
        "  BOOLS: [true, false, true, true]\n"   # -> [1.0, 0.0, 1.0, 1.0]
        "  SHORT: [1.0, 2.0, 3.0]\n"
        "  LONG: [1.0, 2.0, 3.0, 4.0, 5.0]\n"
        "  TEXT: [1.0, 2.0, 3.0, x]\n"
        "  MAP: {a: 1, b: 2, c: 3, d: 4}\n"
        "  NADA: null\n"
        "  INF: [1.0, 2.0, 3.0, .inf]\n")
try:
    _bad = load_table(_YAML)
    check("load_table keeps well-formed rows", sorted(_bad), ["GOOD", "OK2"])
    check("load_table rejects a 4-char string", "STR" not in _bad, True)
    check("load_table rejects bools", "BOOLS" not in _bad, True)
    check("load_table rejects short rows", "SHORT" not in _bad, True)
    check("load_table rejects long rows", "LONG" not in _bad, True)
    check("load_table rejects non-numeric entries", "TEXT" not in _bad, True)
    check("load_table rejects mappings", "MAP" not in _bad, True)
    check("load_table rejects null", "NADA" not in _bad, True)
    check("load_table rejects infinities", "INF" not in _bad, True)

    with open(_YAML, "w", encoding="utf-8") as _fh:
        _fh.write("position_sizing_caps: {}\n")
    check("load_table on a yaml with no expected_returns", load_table(_YAML), {})
finally:
    os.remove(_YAML)


# ── quad_tape_block end-to-end (fixture bars, no network) ───────────────────
_dates = []
_closes = {}
for _t, _v in _real.items():
    # 30 flat days then one move whose SIZE is the Quad 4 expected return, so
    # the ordering of realized returns reproduces the Quad 4 ordering exactly.
    _closes[_t] = {"closes": [100.0] * 30 + [100.0 * (1 + _v[3] / 100.0)],
                   "dates": []}
_block = quad_tape_block(_closes, "Quad 4",
                         [("1M", lambda c, d: (c[-1] / c[-22] - 1))])
check("block scores the fixture tape as Quad 4", "CONFIRM" in _block, True)
check("block reports doctrine coverage", "QUAD vs TAPE" in _block, True)

# The per-name callouts must come from 1M, not from whichever window was listed
# first. On the 2nd of a month MTD holds one trading day; naming five divergent
# tickers off one session is manufactured alarm. MTD is given first here, and
# 1M must still win.
check("DETAIL_PREF leads with 1M", DETAIL_PREF[0], "1M")
_w = [("MTD", lambda c, d: (c[-1] / c[-2] - 1)),
      ("1M", lambda c, d: (c[-1] / c[-22] - 1))]
_block_pref = quad_tape_block(_closes, "Quad 4", _w)
check("block draws detail from 1M not the first window",
      "1M window" in _block_pref and "MTD window" not in _block_pref, True)
# Both windows still get SCORED — preference governs the callouts only.
check("block still scores every window",
      _block_pref.count("\nMTD ") == 1 and _block_pref.count("\n1M ") == 1, True)
# A caller passing windows outside DETAIL_PREF still gets a callout block.
_block_odd = quad_tape_block(_closes, "Quad 4",
                             [("5D", lambda c, d: (c[-1] / c[-22] - 1))])
check("block falls back to an unlisted window", "5D window" in _block_odd, True)

# Missing prices must be NAMED, not silently reduce the sample.
_partial = {k: v for k, v in list(_closes.items())[:20]}
_block_p = quad_tape_block(_partial, "Quad 4",
                           [("1M", lambda c, d: (c[-1] / c[-22] - 1))])
check("block names the unpriced doctrine tickers",
      "no price data for" in _block_p, True)

# A name that HAS bars but too few for the window passes the "no price data"
# filter, then returns None from the window and drops out of the ranking. Before
# this was reported, n changed row to row with nothing said about which name
# left — the sample shrank invisibly.
_short = {t: dict(v) for t, v in _closes.items()}
_short["SPY"] = {"closes": [100.0] * 5, "dates": []}
_block_s = quad_tape_block(_short, "Quad 4",
                           [("1M", lambda c, d: (c[-1] / c[-22] - 1)
                             if len(c) > 21 else None)])
check("block names a priced-but-too-short name",
      "no usable return over this window" in _block_s and "SPY" in _block_s,
      True)
check("block does not double-report it as unpriced",
      "no price data for" not in _block_s, True)

# No prices at all: a reason, never a blank section.
_block_none = quad_tape_block({}, "Quad 4",
                              [("1M", lambda c, d: (c[-1] / c[-22] - 1))])
check("block with zero bars still prints", "QUAD vs TAPE" in _block_none, True)
check("block with zero bars says skipped", "skipped" in _block_none, True)

# A window fn that raises must not take the pack down.
def _boom(c, d):
    raise ValueError("window exploded")


_block_boom = quad_tape_block(_closes, "Quad 4", [("1M", _boom)])
check("block survives a raising window fn",
      _block_boom.startswith("QUAD vs TAPE: unavailable"), True)

print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {', '.join(FAILED)}")
    sys.exit(1)
print("all quad_tape tests passed")
