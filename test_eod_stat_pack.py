"""test_eod_stat_pack.py — EOD stat pack Phase 1 (spec: 2026-07-27, DESIGN LOCKED).

Pure logic only: no network, no DB, no yfinance.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.eod_stat_pack import (CORR_ANCHORS, CORR_ROWS, CORR_WINDOWS,  # noqa: E402
                                 _fred_key, key_shape_problem, mtd_return,
                                 qtd_return,
                                 rolling_corr_stats, sector_row,
                                 FACTORS, RET_WINDOWS, corr_over,
                                 curve_2_10, daily_returns, fmt_corr,
                                 fmt_pct, format_correlations,
                                 format_factor_board, format_sectors,
                                 handle_eod_command, level_changes, pearson,
                                 pct_return, returns_row, spread_row,
                                 ytd_return)


# ───────────────────────── returns ──────────────────────────────────────────

def test_pct_return_and_its_absences():
    cl = [100, 101, 102, 103, 104, 105]
    assert abs(pct_return(cl, 1) - (105 / 104 - 1)) < 1e-12
    assert pct_return(cl, 99) is None, "too short must be None, not 0.0"
    assert pct_return([0, 0, 5], 2) is None, "zero base must not divide"
    assert pct_return([], 1) is None and pct_return(None, 1) is None


def test_ytd_uses_dates_not_a_day_count():
    """A fixed day count is wrong every January — in the first week of the year
    'YTD' is three sessions, not 252."""
    dates = [dt.date(2025, 12, 30), dt.date(2025, 12, 31),
             dt.date(2026, 1, 2), dt.date(2026, 1, 3)]
    assert abs(ytd_return([90, 100, 110, 121], dates) - 0.21) < 1e-9
    # no prior-year close in the window -> unknown, not a guess
    assert ytd_return([100, 110], dates[2:]) is None
    assert ytd_return([], []) is None


def test_returns_row_covers_the_specced_windows():
    want = {lbl for lbl, _ in RET_WINDOWS} | {"YTD"}
    got = set(returns_row([100] * 300, [dt.date(2026, 1, 1)] * 300))
    assert got == want, got
    assert want == {"1D", "1W", "1M", "3M", "6M", "YTD"}


def test_spread_drops_a_leg_rather_than_guessing():
    assert spread_row({"1D": 0.02}, {"1D": 0.01})["1D"] == 0.01
    assert spread_row({"1D": 0.02}, {"1D": None})["1D"] is None
    assert spread_row({}, {})["1D"] is None


# ───────────────────────── correlation ──────────────────────────────────────

def test_pearson_perfect_and_undefined():
    a, b = [1, 2, 3, 4, 5, 6, 7, 8], [2, 4, 6, 8, 10, 12, 14, 16]
    assert abs(pearson(daily_returns(a), daily_returns(b)) - 1.0) < 1e-9
    assert pearson([0, 0, 0, 0], [1, 2, 3, 4]) is None, "zero variance"
    assert pearson([1, 2], [1, 2]) is None, "fewer than 3 points"


def test_corr_is_negative_for_mirrored_series():
    """Mirrored daily returns, not constant compounding — a constant-return
    series has zero variance and its correlation is genuinely undefined."""
    import random
    random.seed(11)
    steps = [random.uniform(-0.02, 0.02) for _ in range(60)]
    up, dn = [100.0], [100.0]
    for r in steps:
        up.append(up[-1] * (1 + r))
        dn.append(dn[-1] * (1 - r))       # exact mirror
    assert corr_over(up, dn, 30) < -0.99


def test_corr_undefined_on_a_constant_return_series():
    flat = [100 * (1.01 ** i) for i in range(40)]
    other = [100 * (1.02 ** i) for i in range(40)]
    assert corr_over(flat, other, 30) is None, \
        "zero variance must be None, not a spurious +1.00"


def test_corr_window_shorter_than_data_is_honoured():
    import random
    random.seed(7)
    a = [100.0]
    b = [100.0]
    for _ in range(200):
        a.append(a[-1] * (1 + random.uniform(-0.02, 0.02)))
        b.append(b[-1] * (1 + random.uniform(-0.02, 0.02)))
    short, long_ = corr_over(a, b, 15), corr_over(a, b, 180)
    assert short is not None and long_ is not None
    assert short != long_, "different windows must use different samples"


def test_corr_returns_none_when_a_leg_is_missing():
    assert corr_over(None, [1, 2, 3, 4], 30) is None
    assert corr_over([1, 2, 3, 4], [], 30) is None


# ───────────────────────── FRED levels ──────────────────────────────────────

def test_level_changes_are_basis_points_not_percent_returns():
    """4.10 -> 4.20 is +10bp. Reporting it as +2.4% reads fine and means
    nothing."""
    obs = [("d1", 4.10), ("d2", 4.20)]
    d = level_changes(obs)
    assert d["last"] == 4.20
    assert abs(d["1D"] - 10.0) < 1e-9
    assert d["1W"] is None, "not enough history -> None, not 0"


def test_curve_aligns_on_date_not_position():
    """The two series have different holiday gaps; zipping by index compares
    different days and produces a plausible wrong number."""
    two = [("2026-01-02", 4.00), ("2026-01-05", 4.10)]
    ten = [("2026-01-02", 4.50), ("2026-01-03", 9.99), ("2026-01-05", 4.40)]
    c = curve_2_10(two, ten)
    assert abs(c["last"] - 0.30) < 1e-9, c        # 4.40-4.10, not 4.40-4.00
    assert abs(c["1D"] - (-20.0)) < 1e-9, c
    assert curve_2_10([], ten) == {}


# ───────────────────────── formatting ───────────────────────────────────────

def test_absent_values_never_render_as_flat():
    assert "n/a" in fmt_pct(None) and "n/a" in fmt_corr(None)
    assert fmt_pct(0.0).strip() == "+0.0%", "a real zero is not the same as n/a"


def test_signs_are_explicit_since_there_is_no_colour():
    assert fmt_pct(0.012).strip().startswith("+")
    assert fmt_pct(-0.012).strip().startswith("-")
    assert fmt_corr(0.5).strip().startswith("+")


def test_factor_board_shows_spread_and_both_legs():
    """Operator asked for BOTH — a spread alone hides which leg moved."""
    r = {k: 0.01 for k in ["1D", "1W", "1M", "3M", "6M", "YTD"]}
    out = format_factor_board([("Beta", "SPHB", "SPLV", "High Beta − Low Vol",
                               r, r, r)])
    lines = out.splitlines()
    assert any("SPHB/SPLV" in ln for ln in lines)
    assert any(ln.strip().startswith("SPHB") for ln in lines)
    assert any(ln.strip().startswith("SPLV") for ln in lines)


def test_sectors_sorted_by_relative_money_in_first():
    mk = lambda v: {k: v for k in ["1D", "1W", "1M", "3M", "6M", "YTD"]}  # noqa: E731
    out = format_sectors([("XLE", mk(0.0), mk(-0.05)),
                          ("XLK", mk(0.0), mk(0.05))])
    body = [ln for ln in out.splitlines() if ln.startswith("XL")]
    assert body[0].startswith("XLK"), body


def test_correlation_block_omits_self_pairs_and_labels_anchors():
    blocks = [("SPX", "SPY", [("Oil (USO)", {w: 0.5 for w in CORR_WINDOWS})])]
    out = format_correlations(blocks)
    assert "vs SPX (SPY)" in out
    for w in CORR_WINDOWS:
        assert f"{w}D" in out


# ───────────────────────── spec conformance + wiring ────────────────────────

def test_the_eight_specced_factors_are_present():
    names = [f[0] for f in FACTORS]
    assert names == ["Beta", "Momentum", "Style", "Size", "Quality",
                     "Yield", "Low Vol", "Crowding"], names


def test_correlation_rows_and_anchors_match_the_spec():
    assert [r[0] for r in CORR_ROWS] == ["SPX", "Nasdaq", "R2000", "20y UST",
                                         "Oil", "Gold", "Copper", "HY", "Bitcoin"]
    assert [a[0] for a in CORR_ANCHORS] == ["USD", "SPX", "10y", "Oil"]
    assert CORR_WINDOWS == [15, 30, 90, 120, 180]


def test_command_claims_only_its_own_sentinels():
    for other in ("REPORT", "WEEKEND", "SCREEN", "MFR BACKLOG", ""):
        assert handle_eod_command(other) is None, other
    assert handle_eod_command(None) is None


def test_dispatch_chain_is_wired():
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "telegram_handler.py"), encoding="utf-8").read()
    assert "handle_eod_command" in src
    assert '("eod", _eod)' in src



# ───────────────── Hedgeye deck conformance (HE_TMS_RR_MC p38/p41/p42) ──────

def test_sector_windows_match_the_deck_not_the_factor_board():
    """Deck p38/p39 use 1-Day / MTD / QTD / YTD and carry price. The factor
    board (p41) uses 1D/1W/1M/3M/6M/YTD. Two different pages, two different
    window sets — matching Hedgeye beats internal symmetry."""
    ds = [dt.date(2025, 12, 31), dt.date(2026, 6, 30), dt.date(2026, 7, 1)]
    r = sector_row([100, 200, 220], ds)
    assert set(r) == {"price", "1D", "MTD", "QTD", "YTD"}, set(r)
    assert r["price"] == 220


def test_mtd_and_qtd_use_the_prior_period_close():
    ds = [dt.date(2026, 6, 29), dt.date(2026, 6, 30),
          dt.date(2026, 7, 1), dt.date(2026, 7, 2)]
    cl = [95, 100, 105, 110]
    assert abs(mtd_return(cl, ds) - 0.10) < 1e-9      # base = 6/30 close
    assert abs(qtd_return(cl, ds) - 0.10) < 1e-9      # Q2 -> Q3, same base
    # no prior period in the window -> unknown, not a guess
    assert mtd_return([100, 110], ds[2:]) is None


def test_qtd_boundary_is_the_quarter_not_the_month():
    ds = [dt.date(2026, 3, 31), dt.date(2026, 4, 1), dt.date(2026, 5, 1)]
    cl = [100, 110, 120]
    assert abs(qtd_return(cl, ds) - 0.20) < 1e-9, "base must be the 3/31 close"
    assert abs(mtd_return(cl, ds) - (120 / 110 - 1)) < 1e-9, "MTD base = 4/1"


def test_rolling_corr_stats_summarise_a_year_of_30d_readings():
    """Deck p42's right panel. A single 30D reading says where correlation is;
    this says whether that is normal."""
    import random
    random.seed(21)
    a, b = [100.0], [100.0]
    for _ in range(400):
        r = random.uniform(-.02, .02)
        a.append(a[-1] * (1 + r))
        b.append(b[-1] * (1 + r * 0.6 + random.uniform(-.01, .01)))
    st = rolling_corr_stats(a, b)
    assert st["n"] == 252, st["n"]
    assert -1 <= st["low"] <= st["high"] <= 1
    assert abs(st["pct_pos"] + st["pct_neg"] - 1.0) < 1e-9


def test_rolling_corr_stats_absent_when_history_is_short():
    assert rolling_corr_stats([100, 101, 102], [100, 101, 102]) == {}


# ───────────────────────── FRED key diagnosis ───────────────────────────────

def test_key_shape_problems_are_named_not_guessed():
    """2026-08-02: four-for-four HTTP 400 with no reason. FRED keys are 32
    lower-case alphanumerics; anything else fails every series identically,
    which reads as 'the section is broken' rather than 'one variable is wrong'."""
    good = "a" * 32
    assert key_shape_problem(good) is None
    assert "quotes" in key_shape_problem('"' + good + '"')
    assert "whitespace" in key_shape_problem(" " + good + " ")
    assert "lower-case" in key_shape_problem("A" * 32)
    assert "32" in key_shape_problem("abc")
    assert "URL" in key_shape_problem("https://api.stlouisfed.org/?k=1")
    assert key_shape_problem("") == "empty"
    assert key_shape_problem(None) == "empty"
    assert "non-alphanumeric" in key_shape_problem("a" * 31 + "!")



def test_padded_env_key_is_stripped_and_reported():
    """2026-08-02 live: FRED_API_KEY on Railway was " aca4...71cb " -- 34 chars,
    one space each side, every series 400'd, and copying the value out of the
    Railway UI reproduced it clean. Being strict about that buys nothing: a
    leading space is never intentional. Accept it, and say so, so the variable
    still gets fixed."""
    import os as _os
    prev = _os.environ.get("FRED_API_KEY")
    try:
        _os.environ["FRED_API_KEY"] = " " + "a" * 32 + " "
        k, n, padded = _fred_key()
        assert len(k) == 32 and padded is True and n == "FRED_API_KEY"
        assert key_shape_problem(k) is None, "stripped key must pass the shape check"

        _os.environ["FRED_API_KEY"] = "a" * 32
        assert _fred_key()[2] is False, "a clean value must not be flagged"

        _os.environ["FRED_API_KEY"] = "   "
        assert _fred_key() == (None, None, False), "whitespace-only is no key"
    finally:
        if prev is None:
            _os.environ.pop("FRED_API_KEY", None)
        else:
            _os.environ["FRED_API_KEY"] = prev


def test_mfr_token_is_stripped_too():
    """Same padding on MFR_API_TOKEN would take down the watchlist read, the
    fan-out and the enrollment backlog at once — and would look exactly like the
    auth failure the watchlist guard exists for."""
    import importlib
    import os as _os
    prev = _os.environ.get("MFR_API_TOKEN")
    try:
        _os.environ["MFR_API_TOKEN"] = "  tok123  "
        import mfr_client
        importlib.reload(mfr_client)
        assert mfr_client._resolve_token() == "tok123"
    finally:
        if prev is None:
            _os.environ.pop("MFR_API_TOKEN", None)
        else:
            _os.environ["MFR_API_TOKEN"] = prev


if __name__ == "__main__":
    import inspect
    fails = 0
    for name, fn in sorted(inspect.getmembers(sys.modules["__main__"],
                                              inspect.isfunction)):
        if name.startswith("test_"):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
            except Exception as e:
                fails += 1
                print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
