"""quad_tape.py — is the tape actually trading the Quad the header says?

The EOD header prints a Quad. Nothing in the pack ever checked whether the
market agrees. On 2026-08-02 the pack showed a bear steepener (10y +24bp on the
month vs 2y +9bp) with credit spreads BENIGN, under a Quad 4 header — and Quad 4
is precisely the regime where the long end is supposed to rally and credit is
supposed to widen. Two of the pack's own numbers contradicted its own headline
and the pack had no way to say so.

WHAT THIS COMPARES
------------------
config/hedgeye_doctrine.yaml carries `expected_returns`: 34 liquid ETFs x 4
Quads, Hedgeye's own average return per Quad. That is the doctrine's prediction
of RELATIVE ORDERING — in Quad 4, TLT and XLP beat SPHB and IWM.

So: rank the 34 names by doctrine expected return in each Quad, rank the same
names by what they ACTUALLY returned over a window, and take the Spearman rank
correlation. High positive = the tape is trading that Quad.

Rank correlation, not magnitude correlation, and that is not a shortcut — it is
the only honest comparison available. The doctrine numbers are average
QUARTERLY returns from a multi-decade backtest. A realized 1-week move is a
different unit entirely; regressing one on the other would produce a number with
a decimal point and no meaning. Ranks are scale-free, so ordering is compared and
magnitude never is. This module will not print a magnitude agreement figure.

GBTC is the reason ranks matter twice over: its Quad 3 expected return is
+157.2 against a table where nothing else clears +9. Under any magnitude-weighted
method that one row would set the answer. As a rank it is worth exactly one
place, like every other name.

WHAT IT DOES NOT DO
-------------------
It does not override the Quad. The Quad comes from Hedgeye via the parsers and
quad_regime_history; this is a monitor, not a detector. A DIVERGE line is a
prompt to look, not a signal to trade. Divergence has at least three ordinary
causes that are not "the Quad is wrong": the transition is in progress and the
tape leads, the window is too short to have signal, or a single sector's news is
dragging the ranks. The significance floor below exists so the first thing the
operator sees is whether the number clears noise at all.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

log = logging.getLogger("quad_tape")

QUADS = ["Quad 1", "Quad 2", "Quad 3", "Quad 4"]

# Below this many paired names the rank correlation is not worth printing.
MIN_NAMES = 8

# How many names to name on each side of the divergence detail.
TOP_N = 5

# Two Quads whose rho differs by less than this are shown tied for best fit
# rather than one being crowned.
#
# 0.20, not the 0.05 this started at. Measured on the real 34-name table with a
# uniformly random tape: sd(rho_Q1 - rho_Q2) = 0.117. At 0.05 a single winner
# got crowned 68% of the time on pure noise — the guard almost never fired when
# it was most needed. 0.20 is ~1.7sd, so a crowned winner means something.
#
# The reason a guard is needed at all: the four doctrine columns are NOT
# independent. Their pairwise rank correlations on the real table are
#   Q1-Q2 +0.77   Q1-Q4 -0.59   Q2-Q4 -0.60   Q3 weakly related to all
# and the eigenvalue shares of the four columns are [.61 .22 .11 .06] — one
# dominant risk-on/risk-off axis plus a weak commodity axis, not four separable
# regimes. Best fit is therefore a soft read, and since 2026-08-02 it does not
# drive the verdict. See verdict().
TIE_EPS = 0.20

# Which window the per-name detail is drawn from, in order of preference.
# 1M leads deliberately. MTD looks like the natural choice — it is the window
# that tests the MONTHLY Quad — but on the 2nd of a month MTD contains one
# trading day, and naming five "divergent" tickers off a single session is how
# a monitor manufactures alarm. 1M is a fixed 21-day lookback and is the same
# length every day of the year. MTD and QTD still get scored in the fit table;
# they just do not drive the callouts.
DETAIL_PREF = ("1M", "QTD", "MTD", "1W")


# ═══════════════════════ pure logic (no I/O, fixture-tested) ════════════════

def quad_index(quad) -> int | None:
    """'Quad 4' / 'quad4' / 'Q4' / '4' / 4 -> 3. None on anything else."""
    if quad is None:
        return None
    s = str(quad).strip().lower().replace("quad", "").replace("q", "").strip()
    return int(s) - 1 if s in ("1", "2", "3", "4") else None


def avg_ranks(values) -> list[float]:
    """Ranks, 1 = smallest, ties share the average of the places they occupy.

    Ties are not a corner case here. The doctrine table has real ones — XLB and
    XLY are both -0.7 in Quad 3, XLP and XLV are both 3.2 in Quad 4 — and giving
    tied values different ranks by list order would inject the yaml's key order
    into the correlation as if it were data.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        share = (i + j) / 2.0 + 1.0          # 1-based average of places i..j
        for k in range(i, j + 1):
            out[order[k]] = share
        i = j + 1
    return out


def spearman(xs, ys) -> float | None:
    """Rank correlation. None when undefined — too few points, either side
    entirely tied (a flat side has no ordering to correlate), or the two sides
    being different lengths.

    Length mismatch returns None rather than truncating to the shorter. Truncating
    turns a caller-side alignment bug into a CONFIDENT answer: spearman([1..6],
    [6,5,4,3,2,1,99,98]) would return exactly -1.00, which is the worst possible
    way for two misaligned name lists to fail.
    """
    if len(xs) != len(ys):
        log.warning("quad_tape: spearman got %d vs %d values — refusing",
                    len(xs), len(ys))
        return None
    n = len(xs)
    if n < 3:
        return None
    rx, ry = avg_ranks(list(xs)), avg_ranks(list(ys))
    mx, my = sum(rx) / n, sum(ry) / n
    sxx = sum((v - mx) ** 2 for v in rx)
    syy = sum((v - my) ** 2 for v in ry)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return sxy / math.sqrt(sxx * syy)


def rho_critical(n) -> float | None:
    """|rho| that clears p<0.05 two-sided, normal approximation (1.96/sqrt(n-1)).

    Approximate on purpose. The exact Spearman distribution at n=31 would give a
    third decimal that this comparison does not earn — the inputs are a backtest
    average against one arbitrary window. The point of printing it is to stop a
    rho of +0.18 being read as agreement.
    """
    return 1.96 / math.sqrt(n - 1) if n and n > 2 else None


def _usable(v) -> bool:
    """A return we can rank. Rejects None, NaN and +/-inf.

    NaN matters: it is not None, so a None-only filter keeps it, and it then
    sorts into whatever slot the sort happens to put it in. The output is a fully
    confident rho built on a name whose return is undefined."""
    return v is not None and isinstance(v, (int, float)) \
        and not isinstance(v, bool) and math.isfinite(v)


def pair(realized, table) -> tuple[list, list]:
    """Names present in BOTH the doctrine table and the realized dict, with a
    usable return. Returns (names, realized_values) sorted for determinism."""
    names = sorted(t for t in table if _usable(realized.get(t)))
    return names, [float(realized[t]) for t in names]


def fit_all_quads(realized, table) -> dict:
    """{'names': n, 'rho': {'Quad 1': .., ...}, 'best': 'Quad 2'|None,
        'tied': ['Quad 1','Quad 2']}

    All four rho values are computed over the SAME name set, so they are
    comparable to each other. Scoring each Quad on whichever names happened to
    have data for it would make the winner an artifact of coverage.

    `tied` is every Quad within TIE_EPS of the top rho, `best` included. Callers
    should judge agreement against `tied`, not `best` — see TIE_EPS.
    """
    names, acts = pair(realized, table)
    out = {"names": len(names), "rho": {q: None for q in QUADS},
           "best": None, "tied": [], "crit": rho_critical(len(names)),
           "dropped": sorted(set(table) - set(names))}
    if len(names) < MIN_NAMES:
        return out
    for i, q in enumerate(QUADS):
        out["rho"][q] = spearman([table[t][i] for t in names], acts)
    scored = [(q, r) for q, r in out["rho"].items() if r is not None]
    if scored:
        top = max(r for _, r in scored)
        # Only crown a winner that actually clears the noise floor. On most days
        # all four rho sit near zero and the argmax is whichever column caught
        # the most noise; naming it would be an answer where there is none.
        if out["crit"] is not None and top >= out["crit"]:
            out["best"] = next(q for q, r in scored if r == top)
            out["tied"] = [q for q, r in scored if top - r <= TIE_EPS]
    return out


def rank_gaps(realized, table, quad) -> list[dict]:
    """Per-name doctrine rank vs realized rank for one Quad.

    RANK 1 = BEST (flipped 2026-08-02, B2). It used to be 1 = worst, so the
    biggest laggard in the table printed as "GBTC 1/34" — which every reader on
    earth parses as "ranked number one" and is the exact opposite of what it
    meant. Best-is-1 is the convention everywhere else, so it is the convention
    here.

    gap = act_rank - exp_rank under best-is-1, so the SIGN is unchanged from the
    old formula and still reads the same way:
      gap > 0  doctrine ranks it better than the tape does — the Quad says own
               it and it is lagging.
      gap < 0  the tape is bidding something the Quad ranks poorly.
    Returned sorted by |gap| descending (F3), so the largest divergence in
    either direction is row one.
    """
    qi = quad_index(quad)
    if qi is None:
        return []
    names, acts = pair(realized, table)
    if len(names) < MIN_NAMES:
        return []
    n = len(names)
    exps = [table[t][qi] for t in names]
    # avg_ranks is 1 = smallest. Best-is-1 is its mirror: n + 1 - r. Mirroring
    # keeps tie handling intact (a tie at 30.5 of 34 mirrors to 4.5, still tied).
    er = [n + 1 - r for r in avg_ranks(exps)]
    ar = [n + 1 - r for r in avg_ranks(acts)]
    rows = [{"ticker": t, "exp": exps[i], "act": acts[i],
             "exp_rank": er[i], "act_rank": ar[i], "gap": ar[i] - er[i],
             "n": n}
            for i, t in enumerate(names)]
    return sorted(rows, key=lambda r: (-abs(r["gap"]), r["ticker"]))


def headline(gaps, header_quad, stale=False) -> str:
    """F7: the one line that goes at the top — the single largest doctrine-vs-
    tape gap. Derived line for speed; the full table below it is the audit
    trail, and today's stale-header bug is the case for why both must print."""
    if not gaps:
        return "HEADLINE: no divergence detail (Quad unknown or too few names)."
    g = gaps[0]
    q = header_quad if quad_index(header_quad) is not None else "the Quad"
    tag = " [UNCONFIRMED QUAD]" if stale else ""
    # gaps are sorted by |gap|, so a zero here means the WIDEST gap in the table
    # is zero — the tape is in perfect doctrine order. Claiming a direction on
    # that ("bid above its billing" on a name that matched exactly) is a
    # fabricated read, and it fired on a tape that scored rho = +1.00.
    if round(g["gap"]) == 0:
        return (f"HEADLINE{tag}: no divergence — every name is within half a "
                f"rank of its {q} billing. The tape is trading {q} in order.")
    d = "LAGGING its billing" if g["gap"] > 0 else "BID ABOVE its billing"
    return (f"HEADLINE{tag}: {g['ticker']} is the widest {q} gap — doc rank "
            f"{_rank(g['exp_rank'])}/{g['n']} vs tape {_rank(g['act_rank'])}/"
            f"{g['n']} ({_gap(g['gap'], 0)}), {d} ({_pct(g['act'], 0).strip()} "
            f"actual vs {g['exp']:+.1f}%/qtr expected).")


def verdict(fit, header_quad, stale=False) -> str:
    """One word for the fit row, judged on the HEADER Quad's own rho.

    Rewritten 2026-08-02 after review. It used to compare the header Quad against
    the argmax over all four columns, which was wrong three ways:

      1. An unknown header Quad returned DIVERGE. `_quad_for` returns None on any
         DB error, on an empty quad_regime_history, and for dates before
         QUAD_CLEAN_START — so the pack could print "QUAD: unavailable" and then
         DIVERGE against nothing on the very next line. Worst defect in the
         section, and a test had pinned it as correct.
      2. It tested a MAXIMUM over four columns against a single-comparison
         critical value. Measured false-alarm rate on a pure-noise tape was 8.4%,
         not the 5% the footnote claimed.
      3. Because Q1 and Q2 columns rank-correlate at +0.77, a header Quad with a
         genuinely significant fit (rho = +0.60) got called DIVERGE whenever the
         neighbouring column edged it by a hair.

    Scoring the header Quad's own column is ONE test, at the stated floor, and it
    answers the question the section actually asks. DIVERGE now means the tape is
    trading the INVERSE of the header — a real, reachable, loud state, which the
    old argmax form could never produce.
    """
    qi = quad_index(header_quad)
    if qi is None:
        return "no header"
    # B1: a header carried forward from a previous month is not a claim about
    # this month, so scoring against it answers the wrong question with full
    # confidence. Suppress the verdict, keep the rho table — the numbers are
    # still real, it is the QUESTION that is unconfirmed.
    if stale:
        return "AWAIT CONFIRM"
    rho, crit = (fit.get("rho") or {}).get(QUADS[qi]), fit.get("crit")
    if rho is None or crit is None:
        return "n/a"
    if rho >= crit:
        return "CONFIRM"
    if rho <= -crit:
        return "DIVERGE"
    return "NOISE"


# ═══════════════════════ formatting ═════════════════════════════════════════

def _pct(v, width=8) -> str:
    return "n/a".rjust(width) if v is None else f"{v * 100:+.1f}%".rjust(width)


def _rho(v, width=9) -> str:
    return "n/a".rjust(width) if v is None else f"{v:+.2f}".rjust(width)


def _gap(v, width=7) -> str:
    """Signed gap. Ranks carry .5 from ties, so a gap of -0.5 formats as '-0'
    under %.0f — a minus sign on a zero, which reads as a direction that isn't
    there. Anything rounding to zero prints a bare 0."""
    r = round(v)
    return ("0" if r == 0 else f"{r:+.0f}").rjust(width)


def _shape_read(big) -> str:
    """Plain-language read of how many names moved by a third of the table.

    The zero branch is not pedantry. `big <= 3` used to catch it, so a tape in
    PERFECT doctrine order — rho +1.00, verdict CONFIRM — printed "concentrated
    — read it as news, not regime" two lines under its own CONFIRM. It fired on
    100% of simulated confirming tapes and 0% of random ones: wrong on exactly
    the days the section agrees with the header.
    """
    if big == 0:
        return "none — the tape is in doctrine order"
    if big <= 3:
        return "concentrated — read it as news, not regime"
    return "broad — consistent with a regime turn"


def _rank(v) -> str:
    """A rank as an integer string.

    Averaged ranks carry .5 from ties, and Python rounds half to EVEN — so a
    tie at place 4.5 prints 4 while an identical tie at 9.5 prints 10. Two
    structurally identical situations rendered differently, which reads as a
    difference in the data. Round half up, always, so ties are consistent.
    """
    return str(int(math.floor(v + 0.5)))


def format_quad_tape(fits, header_quad, gaps, gap_window, stale=False) -> str:
    """fits: [(window_label, fit_dict)] from fit_all_quads."""
    hq = header_quad if quad_index(header_quad) is not None else "unknown"
    out = ["QUAD vs TAPE — does the market agree with the header?"]
    if stale:
        # Loud, at the top, before any number. On 2026-08-02 this section
        # printed CONFIRM against a Quad 4 header when the confirmed monthly
        # Quad was Quad 3 — a correct calculation of the wrong question.
        out += [f"  ⚠⚠ HEADER QUAD IS UNCONFIRMED FOR THIS MONTH ({hq} carried "
                f"forward).",
                "     Verdicts are SUPPRESSED. The rho table below is still "
                "real — it is the",
                "     question that is unconfirmed, not the arithmetic. Set the "
                "Quad with the",
                "     QUAD: command and re-run before reading anything here as "
                "agreement."]
    out += [f"  header Quad: {hq}   |   doctrine: hedgeye_doctrine.yaml "
            f"expected_returns",
            "  Spearman rank correlation, realized vs each Quad's expected "
            "ordering.",
            "  Ranks only — the doctrine numbers are average QUARTERLY returns, "
            "so",
            "  magnitudes are not comparable and are never compared.",
            "",
            f"{'window':<8}{'names':>6}" + "".join(q.rjust(9) for q in QUADS)
            + f"{'floor':>8}{'header':>15}{'best fit':>11}"]

    # The floor is printed PER ROW because it depends on that row's n. A single
    # footnote floor plus rows judged at their own n put two contradictory
    # numbers on one screen: a row could read rho=+0.59 NOISE under a footnote
    # claiming the floor was 0.34, because that row actually had n=10.
    for label, f in fits:
        n = f["names"]
        if n < MIN_NAMES:
            out.append(f"{label:<8}{n:>6}   only {n} names with a usable "
                       f"return (need {MIN_NAMES}) — skipped")
            continue
        tied = f.get("tied") or []
        # "Q1≈Q2" when the top two are within TIE_EPS; "none" when nothing
        # clears the floor, which is most days and is an honest answer.
        shown = ("≈".join(q.replace("Quad ", "Q") for q in tied) if len(tied) > 1
                 else (f["best"] or "none"))
        crit = f.get("crit")
        out.append(f"{label:<8}{n:>6}"
                   + "".join(_rho(f["rho"][q]) for q in QUADS)
                   + ("n/a".rjust(8) if crit is None else f"{crit:.2f}".rjust(8))
                   + verdict(f, header_quad, stale).rjust(15)
                   + f"{shown:>11}")

    out.append(f"  header = the {hq} column alone vs the tape: CONFIRM at "
               f"rho >= floor,")
    out.append("  DIVERGE at rho <= -floor (the tape trading its inverse), else "
               "NOISE.")
    out.append("  best fit = highest of the four, blank unless it clears the "
               "floor. Read it")
    out.append("  softly: it is a max over four columns that are themselves "
               "correlated")
    out.append("  (Q1-Q2 +0.77, Q2-Q4 -0.60), so it clears by chance ~8% of "
               "days, not 5%.")
    out.append("  A DIVERGE is a prompt to look, not a signal. The tape can "
               "lead a Quad")
    out.append("  transition, and one sector's news can drag ~30 ranks on a "
               "short window.")

    if not gaps:
        out.append("")
        out.append("  (no per-name detail — Quad unknown or too few names "
                   "priced)")
        return "\n".join(out)

    # F3: every name, one table, sorted by |gap|. The old form printed the top 5
    # each way, which shows the extremes and hides the SHAPE — and the shape is
    # the actual question. Thirty-four names re-ordering together is a regime
    # turning; three outliers on top of thirty flat rows is one sector's news.
    # You cannot tell those apart from a top-5.
    n = gaps[0]["n"]
    hq_short = hq if hq != "unknown" else "the Quad"
    lag = sum(1 for g in gaps if g["gap"] > 0)
    big = sum(1 for g in gaps if abs(g["gap"]) >= n / 3)
    out += ["",
            f"  DIVERGENCE — all {n} names, {gap_window} window, rank 1 = BEST",
            f"  gap = tape rank − doc rank.  POSITIVE = {hq_short} says own it "
            f"and it is LAGGING;",
            f"  NEGATIVE = the tape is BIDDING it above its {hq_short} billing."
            f"  Sorted by |gap|.",
            f"  shape: {lag} of {n} names lag their doctrine rank; {big} moved "
            f"by",
            f"  a third of the table or more ({_shape_read(big)}).",
            f"    {'tkr':<6}{'doc rank':>10}{'tape rank':>11}{'gap':>7}"
            f"{'exp/qtr':>10}{'actual':>10}"]
    for g in gaps:
        out.append(f"    {g['ticker']:<6}"
                   + f"{_rank(g['exp_rank']) + '/' + str(n):>9}"
                   + f"{_rank(g['act_rank']) + '/' + str(n):>10}"
                   + _gap(g["gap"])
                   + f"{g['exp']:>9.1f}%" + _pct(g["act"], 10))
    return "\n".join(out)


# ═══════════════════════ I/O edges ══════════════════════════════════════════

DOCTRINE_PATH = (Path(__file__).resolve().parent.parent
                 / "config" / "hedgeye_doctrine.yaml")


def load_table(path=None) -> dict:
    """{ticker: [q1, q2, q3, q4]} from config/hedgeye_doctrine.yaml.

    Rows without exactly 4 numeric entries are DROPPED and counted, not padded.
    A padded row would score a Quad against a zero it never predicted.

    `path` exists so the malformed-row handling can be tested against fixtures
    without editing the live doctrine file.
    """
    import yaml
    path = path or DOCTRINE_PATH
    with open(path, encoding="utf-8") as fh:
        raw = (yaml.safe_load(fh) or {}).get("expected_returns") or {}
    out, bad = {}, []
    for t, row in raw.items():
        # list/tuple only, and bools rejected. Without the type gate a yaml
        # value of "1234" iterates per CHARACTER into [1.0,2.0,3.0,4.0] and is
        # accepted as a valid row, and [true,false,true,true] becomes
        # [1.0,0.0,1.0,1.0]. Both would score a Quad against fabricated numbers.
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            bad.append(t)
            continue
        try:
            vals = [float(v) for v in row
                    if not isinstance(v, bool)]
        except (TypeError, ValueError):
            bad.append(t)
            continue
        if len(vals) != 4 or not all(math.isfinite(v) for v in vals):
            bad.append(t)
            continue
        out[str(t).upper()] = vals
    if bad:
        log.warning("quad_tape: dropped malformed expected_returns rows: %s",
                    ", ".join(sorted(bad)))
    return out


def doctrine_tickers() -> list[str]:
    """Symbols the EOD pack must fetch for this section. Called by
    build_eod_pack so the fetch set and the table can never drift apart."""
    try:
        return sorted(load_table())
    except Exception as e:
        log.warning("quad_tape: cannot load doctrine table: %s", e)
        return []


def quad_tape_block(bars, header_quad, windows, stale=False) -> str:
    """bars: {sym: {'closes','dates'}}. windows: [(label, fn(closes,dates))].

    Guarded the same way every other EOD section is: a failure prints its reason
    in place. An absent section reads as 'nothing to report', which is a
    different claim from 'this broke'.
    """
    try:
        table = load_table()
    except Exception as e:
        return f"QUAD vs TAPE: unavailable (doctrine table unreadable: {e})"
    if not table:
        return ("QUAD vs TAPE: unavailable — hedgeye_doctrine.yaml has no "
                "usable expected_returns rows.")

    try:
        fits, by_label = [], {}
        for label, fn in windows:
            realized = {}
            for t in table:
                b = bars.get(t) or {}
                if b.get("closes"):
                    realized[t] = fn(b["closes"], b.get("dates") or [])
            fits.append((label, fit_all_quads(realized, table)))
            by_label[label] = realized

        # Detail window: DETAIL_PREF order first, then whatever is left, so a
        # caller passing custom windows still gets a callout block.
        order = ([w for w in DETAIL_PREF if w in by_label]
                 + [w for w in by_label if w not in DETAIL_PREF])
        gap_window, gaps = None, []
        for label in order:
            rows = rank_gaps(by_label[label], table, header_quad)
            if rows:
                gap_window, gaps = label, rows
                break

        head = headline(gaps, header_quad, stale)

        # F7: derived headline on top for speed, full raw table always below it
        # as the audit trail. Never one instead of the other — the 8/2 stale
        # header is the case study for why a summary alone is not enough.
        block = (head + "\n\n"
                 + format_quad_tape(fits, header_quad, gaps,
                                    gap_window or "n/a", stale))

        # Two DIFFERENT reasons a doctrine name can be absent from a row's n,
        # both named. Reporting only the first lets the second one shrink the
        # sample invisibly: a ticker with 5 closes has price data, so it passes
        # the `no price data` filter, then returns None from a 21-day window and
        # drops out of the ranking with nothing said. n silently changes row to
        # row and the reader has no way to know which name left.
        unpriced = sorted(t for t in table
                          if not (bars.get(t) or {}).get("closes"))
        if unpriced:
            block += (f"\n  no price data for {len(unpriced)}/{len(table)} "
                      f"doctrine names: {', '.join(unpriced)}")
        for label, f in fits:
            short = [t for t in (f.get("dropped") or []) if t not in unpriced]
            if short:
                block += (f"\n  {label}: {len(short)} priced name(s) had no "
                          f"usable return over this window (too short a series, "
                          f"or a bad print): {', '.join(short)}")
        return block
    except Exception as e:
        log.error("quad_tape failed: %s", e, exc_info=True)
        return f"QUAD vs TAPE: unavailable ({e})"
