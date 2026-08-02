"""test_quad_tape.py — QUAD vs TAPE (tools/quad_tape.py).

Pure logic only: no network, no DB, no yfinance. The one file-touching test
reads the committed config/hedgeye_doctrine.yaml, which is repo data, not I/O.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.quad_tape import (  # noqa: E402
    DETAIL_PREF, MIN_NAMES, QUADS, TIE_EPS, avg_ranks, doctrine_tickers,
    fit_all_quads, format_quad_tape, load_table, pair, quad_index,
    quad_tape_block, rank_gaps, rho_critical, spearman, verdict,
)

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
check("rank_gaps sorted worst-lag first", _gaps[0]["ticker"], "HHH")
check("rank_gaps lag is positive", _gaps[0]["gap"] > 0, True)
check("rank_gaps carries n", _gaps[0]["n"], 8)
check("rank_gaps covers every paired name", len(_gaps), 8)
check("rank_gaps gaps sum to zero",
      abs(sum(g["gap"] for g in _gaps)) < 1e-9, True)
# The mirror case: something the Quad ranks LOW that the tape is bidding.
check("rank_gaps names the bid-up laggard", _gaps[-1]["gap"] < 0, True)
check("rank_gaps no quad -> empty", rank_gaps(_diverge, _TABLE, None), [])
check("rank_gaps bad quad -> empty", rank_gaps(_diverge, _TABLE, "Quad 7"), [])
check("rank_gaps under MIN_NAMES -> empty", rank_gaps(_few, _TABLE, "Quad 4"), [])

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
      "(1M window" in _block_pref and "(MTD window" not in _block_pref, True)
# Both windows still get SCORED — preference governs the callouts only.
check("block still scores every window",
      _block_pref.count("\nMTD ") == 1 and _block_pref.count("\n1M ") == 1, True)
# A caller passing windows outside DETAIL_PREF still gets a callout block.
_block_odd = quad_tape_block(_closes, "Quad 4",
                             [("5D", lambda c, d: (c[-1] / c[-22] - 1))])
check("block falls back to an unlisted window", "(5D window" in _block_odd, True)

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
