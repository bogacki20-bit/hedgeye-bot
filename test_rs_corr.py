"""
test_rs_corr.py – pure-math tests for tools/rs_corr.py.
No DB, no network. Run with: python -m pytest test_rs_corr.py -v
or:                           python test_rs_corr.py
"""
import math, sys, traceback

# ── import target ─────────────────────────────────────────────────────────────
from tools.rs_corr import (
    log_returns, pearson, corr_pair, pair_key,
    avg_pairwise, rs_trend, CORR_MIN_N,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    status = PASS if cond else FAIL
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))


# ── 1. pearson hits ±1.0 ─────────────────────────────────────────────────────
def test_pearson_perfect():
    xs = list(range(1, CORR_MIN_N + 5))
    ys = [x * 2.0 for x in xs]          # perfect positive
    c, n = pearson(xs, ys)
    check("pearson perfect positive ~ 1.0", abs(c - 1.0) < 1e-10, f"c={c}")

    ys_neg = [-y for y in ys]            # perfect negative
    c2, _ = pearson(xs, ys_neg)
    check("pearson perfect negative ~ -1.0", abs(c2 + 1.0) < 1e-10, f"c={c2}")


# ── 2. min-bars guard returns None + n_obs ────────────────────────────────────
def test_pearson_min_n():
    xs = list(range(CORR_MIN_N - 1))
    ys = list(range(CORR_MIN_N - 1))
    c, n = pearson(xs, ys)
    check("pearson below min-n returns None", c is None, f"c={c}")
    check("pearson below min-n returns n_obs", n == CORR_MIN_N - 1, f"n={n}")


# ── 3. zero variance returns None ─────────────────────────────────────────────
def test_pearson_zero_variance():
    xs = [1.0] * (CORR_MIN_N + 5)       # flat = zero variance
    ys = list(range(CORR_MIN_N + 5))
    c, _ = pearson(xs, ys)
    check("pearson zero variance returns None", c is None, f"c={c}")


# ── 4. pair ordering ──────────────────────────────────────────────────────────
def test_pair_key():
    a, b = pair_key("XLY", "XLB")
    check("pair_key enforces a<=b (XLY,XLB -> XLB,XLY)", a == "XLB" and b == "XLY",
          f"got ({a},{b})")
    a2, b2 = pair_key("XLB", "XLY")
    check("pair_key already ordered is unchanged", a2 == "XLB" and b2 == "XLY",
          f"got ({a2},{b2})")


# ── 5. avg_pairwise over identical series = 1.0 ───────────────────────────────
def test_avg_pairwise_perfect():
    """Three identical return series -> every pair corr = 1 -> avg = 1."""
    dates = list(range(CORR_MIN_N + 10))
    rets  = {d: math.log(1 + 0.001 * d + 1e-9) for d in dates}
    returns_by = {"A": rets, "B": rets, "C": rets}
    avg, n_pairs = avg_pairwise(returns_by, ["A", "B", "C"], CORR_MIN_N + 5)
    check("avg_pairwise identical series ~ 1.0", avg is not None and abs(avg - 1.0) < 1e-9,
          f"avg={avg}")
    check("avg_pairwise n_pairs = 3", n_pairs == 3, f"n_pairs={n_pairs}")


# ── 6. rs_trend direction ─────────────────────────────────────────────────────
def test_rs_trend():
    import datetime
    base = datetime.date(2024, 1, 1)
    # A rising faster than B -> ratio goes up
    a_closes = [(base.replace(day=i+1), 100 + i * 2) for i in range(25)]
    b_closes = [(base.replace(day=i+1), 100 + i * 1) for i in range(25)]
    lvl, chg, dir_ = rs_trend(a_closes, b_closes, 20)
    check("rs_trend rising when A > B", dir_ == "rising", f"dir={dir_} chg={chg}")
    check("rs_trend level > 0", lvl is not None and lvl > 0, f"lvl={lvl}")

    # A and B move identically -> ratio flat
    same = [(base.replace(day=i+1), 100 + i) for i in range(25)]
    lvl2, chg2, dir2 = rs_trend(same, same, 20)
    check("rs_trend flat when A == B", dir2 == "flat", f"dir={dir2} chg={chg2}")


# ── 7. log_returns drops non-positive ─────────────────────────────────────────
def test_log_returns():
    import datetime
    base = datetime.date(2024, 1, 1)
    closes = [
        (base.replace(day=1), 100.0),
        (base.replace(day=2), 0.0),     # zero — should be skipped
        (base.replace(day=3), 110.0),
        (base.replace(day=4), 105.0),
    ]
    rets = log_returns(closes)
    check("log_returns skips zero close", base.replace(day=2) not in rets,
          f"keys={list(rets.keys())}")
    check("log_returns has day4 entry", base.replace(day=4) in rets,
          f"keys={list(rets.keys())}")


# ── runner ────────────────────────────────────────────────────────────────────
def run_all() -> bool:
    tests = [
        test_pearson_perfect,
        test_pearson_min_n,
        test_pearson_zero_variance,
        test_pair_key,
        test_avg_pairwise_perfect,
        test_rs_trend,
        test_log_returns,
    ]
    for t in tests:
        print(f"\n{t.__name__}")
        try:
            t()
        except Exception:
            traceback.print_exc()
            _results.append((t.__name__, False, "exception"))

    passed = sum(1 for _, ok, _ in _results if ok)
    total  = len(_results)
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed")
    return passed == total


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
