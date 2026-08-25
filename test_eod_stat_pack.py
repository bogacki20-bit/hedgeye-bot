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
    assert [r[0] for r in CORR_ROWS] == ["SPX", "Nasdaq", "R2000", "20y+ UST",
                                         "Oil", "Gold", "Copper", "HY", "Bitcoin"]
    assert [a[0] for a in CORR_ANCHORS] == ["USD", "SPX", "20y+ UST", "Oil"]
    assert CORR_WINDOWS == [15, 30, 90, 120, 180]

    # H1: TLT appeared as a ROW labelled "20y UST" and an ANCHOR labelled "10y"
    # in the same pack, so one series read as two different instruments. Any
    # symbol used in both places must carry the same label in both.
    by_sym = {}
    for label, sym in list(CORR_ROWS) + list(CORR_ANCHORS):
        by_sym.setdefault(sym, set()).add(label)
    clashes = {s: ls for s, ls in by_sym.items() if len(ls) > 1}
    assert not clashes, clashes


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


def test_quad_tape_windows_are_wired_and_labelled_honestly():
    """The 1W/1M/MTD/QTD lambdas the pack actually passes to quad_tape.

    test_quad_tape.py exercises the section with hand-rolled lambdas, so this
    wiring — the part a reader would most want verified — had no coverage at all.
    """
    import datetime as _dt
    from tools.eod_stat_pack import QUAD_TAPE_WINDOWS

    labels = [lbl for lbl, _ in QUAD_TAPE_WINDOWS]
    assert labels == ["1W", "1M", "MTD", "QTD"], labels

    # One close per calendar day. 2026-08-16: these windows are now indexed by
    # CALENDAR DATE, not by row position. yfinance returns a varying number of
    # rows for the same request (SPHB 627 then 630 in one process), so a row
    # offset landed on a different DATE run to run and the pack was
    # non-deterministic. With one bar per calendar day the date offset maps
    # straight onto an index:
    #     1W = 7 calendar days back  -> closes[-8]   (was 5 rows -> closes[-6])
    #     1M = 28 calendar days back -> closes[-29]  (was 21 rows -> closes[-22])
    # 28 days is also Hedgeye's "4 Wks Ago", the level their MoM column
    # compares against.
    dates = [_dt.date(2026, 3, 1) + _dt.timedelta(days=i) for i in range(180)]
    closes = [100.0 + i for i in range(180)]
    got = {lbl: fn(closes, dates) for lbl, fn in QUAD_TAPE_WINDOWS}

    assert abs(got["1W"] - (closes[-1] / closes[-8] - 1)) < 1e-12, got["1W"]
    assert abs(got["1M"] - (closes[-1] / closes[-29] - 1)) < 1e-12, got["1M"]

    # MTD/QTD off the last close BEFORE the boundary, not the first close in it.
    last = dates[-1]                                    # 2026-08-27
    m_base = max(i for i, d in enumerate(dates) if d.month < last.month)
    assert abs(got["MTD"] - (closes[-1] / closes[m_base] - 1)) < 1e-12
    q_base = max(i for i, d in enumerate(dates) if (d.month - 1) // 3 < 2)
    assert abs(got["QTD"] - (closes[-1] / closes[q_base] - 1)) < 1e-12
    assert got["QTD"] > got["MTD"] > 0, got

    # A series too short for a window yields None, never an IndexError and never
    # a silently-wrong number off whatever element the negative index landed on.
    assert all(fn([100.0, 101.0], dates[:2]) is None
               for lbl, fn in QUAD_TAPE_WINDOWS if lbl in ("1W", "1M"))
    assert all(fn([], []) is None for _, fn in QUAD_TAPE_WINDOWS)


def test_header_returns_the_quad_alongside_its_lines():
    """_header() went from -> list to -> (lines, quad) so QUAD vs TAPE scores
    against the SAME value the header prints. Nothing covered the signature, so
    a caller unpacking it as a list would have failed only in production.

    No DB here: the DB block raises, which is the interesting path — mq must be
    None, NOT a stale or defaulted Quad."""
    from tools.eod_stat_pack import _header
    lines, quad, stale = _header()
    assert isinstance(lines, list) and lines, lines
    assert lines[0].startswith("EOD STAT PACK"), lines[0]
    assert quad is None or str(quad).startswith("Quad"), quad
    assert isinstance(stale, bool), stale
    # And the section must not manufacture a verdict from that None.
    from tools.quad_tape import verdict
    assert verdict({"rho": {"Quad 4": 0.9}, "crit": 0.3}, None) == "no header"


def test_the_august_rollover_that_shipped_a_wrong_header():
    """B1 end-to-end, replaying 2026-08-02 exactly.

    The pack printed "QUAD: monthly=Quad 4 quarterly=Quad 4 (last confirm
    2026-07-31)" when the confirmed monthly Quad was Quad 3, then scored QUAD
    vs TAPE against Quad 4 and returned CONFIRM. Correct arithmetic, wrong
    question — which is worse than no answer, because it reads as agreement.
    """
    import datetime as _dt
    from tools.quad_regime import quad_staleness
    from tools.quad_tape import quad_tape_block, load_table

    st = quad_staleness(_dt.datetime(2026, 7, 31, 16, 0), _dt.date(2026, 8, 2))
    assert st["monthly_stale"] is True, st
    assert st["quarterly_stale"] is False, st      # Q3 quarterly WAS current

    # Feed a tape that genuinely fits Quad 4 — i.e. the case where the old code
    # was most confident and most wrong.
    table = load_table()
    bars = {t: {"closes": [100.0] * 30 + [100.0 * (1 + v[3] / 100.0)],
                "dates": []} for t, v in table.items()}
    win = [("1M", lambda c, d: c[-1] / c[-22] - 1)]

    fresh = quad_tape_block(bars, "Quad 4", win, stale=False)
    assert "CONFIRM" in fresh and "AWAIT" not in fresh, fresh[:400]

    stale = quad_tape_block(bars, "Quad 4", win, stale=True)
    assert "HEADER QUAD IS UNCONFIRMED" in stale, stale[:400]
    # The rho table survives — the numbers were never the problem.
    assert "floor" in stale and "Quad 4" in stale, stale[:400]

    # Read the VERDICT CELL out of the data row rather than substring-hunting
    # the whole block: "UNCONFIRMED" contains "CONFIRM", and prose in the legend
    # legitimately names every verdict word. Only the cell is the claim.
    import re as _re
    KNOWN = {"CONFIRM", "DIVERGE", "NOISE", "AWAIT CONFIRM", "no header", "n/a"}

    def _verdict_cell(block):
        row = next(l for l in block.split("\n") if l.startswith("1M "))
        hits = [f for f in _re.split(r"\s{2,}", row.strip()) if f in KNOWN]
        assert len(hits) == 1, (hits, row)
        return hits[0]

    assert _verdict_cell(stale) == "AWAIT CONFIRM", repr(_verdict_cell(stale))
    assert _verdict_cell(fresh) == "CONFIRM", repr(_verdict_cell(fresh))


def _stub_db(monkey_rows, effective_at):
    """Fake db_pg + ps_flow._quad_for so _header() can be driven without a DB."""
    import contextlib
    import sys
    import types

    class _Cur:
        def __init__(self): self._r = None
        def execute(self, sql, *a): self._r = (effective_at,)
        def fetchone(self): return self._r
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        def cursor(self): return _Cur()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    fake = types.ModuleType("db_pg")
    fake.get_conn = lambda: _Conn()
    ps = types.ModuleType("tools.ps_flow")
    ps._quad_for = lambda cur, d: monkey_rows

    @contextlib.contextmanager
    def _ctx():
        old = {k: sys.modules.get(k) for k in ("db_pg", "tools.ps_flow")}
        sys.modules["db_pg"], sys.modules["tools.ps_flow"] = fake, ps
        try:
            yield
        finally:
            for k, v in old.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
    return _ctx()


def test_header_marks_a_prior_month_confirmation_stale():
    """The B1 WIRING, not just the helper.

    Mutation-checked: setting `stale = False` in _header, or passing
    stale=False into quad_tape_block, previously left BOTH suites green. The
    guard could be fully disconnected and nothing noticed. These drive
    _header() itself.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo
    from tools.eod_stat_pack import _header

    # Exactly the 8/2 row: a July confirmation stored as a UTC timestamp.
    july = _dt.datetime(2026, 7, 31, 20, 30,
                        tzinfo=ZoneInfo("America/New_York")) \
        .astimezone(_dt.timezone.utc)
    assert july.date() == _dt.date(2026, 8, 1)   # the trap: UTC says August

    with _stub_db(("Quad 4", "Quad 4"), july):
        lines, quad, stale = _header()
    assert quad == "Quad 4", quad                # carried forward UNCHANGED
    blob = "\n".join(lines)
    # Only stale if we are actually past July — the guard is date-dependent, so
    # assert the relationship rather than a hardcoded verdict.
    from tools.quad_regime import quad_staleness, today_market
    expect = quad_staleness(july, today_market())["monthly_stale"]
    assert stale is expect, (stale, expect, today_market())
    if expect:
        assert "STALE" in blob and "carried forward unchanged" in blob, blob
        assert "2026-07-31" in blob, blob         # ET date, not the UTC 08-01
    else:
        assert "STALE" not in blob, blob


def test_header_fails_closed_when_the_db_blows_up_mid_read():
    """A real Quad must never escape paired with stale=False.

    _header used to assign mq inside the try and initialise stale=False, so an
    exception AFTER the _quad_for call left a live Quad marked fresh: the header
    printed 'QUAD: unavailable' and the next section printed CONFIRM against it.
    That is the same failure the whole guard exists to prevent, reintroduced by
    the guard's own error path.
    """
    import sys
    import types
    from tools.eod_stat_pack import _header

    class _Boom:
        def cursor(self): raise RuntimeError("connection reset by peer")
        def __enter__(self): return self
        def __exit__(self, *a): return False

    fake = types.ModuleType("db_pg")
    fake.get_conn = lambda: _Boom()
    ps = types.ModuleType("tools.ps_flow")
    ps._quad_for = lambda cur, d: ("Quad 4", "Quad 4")

    old = {k: sys.modules.get(k) for k in ("db_pg", "tools.ps_flow")}
    sys.modules["db_pg"], sys.modules["tools.ps_flow"] = fake, ps
    try:
        lines, quad, stale = _header()
    finally:
        for k, v in old.items():
            sys.modules[k] = v if v is not None else sys.modules.pop(k, None)

    assert quad is None, quad
    assert stale is True, stale
    assert any("QUAD: unavailable" in ln for ln in lines), lines

    # The case that actually mattered: the Quad read SUCCEEDS and the failure
    # comes afterwards, on the effective_at query. The old code had already
    # written a live Quad into `mq` by then, with stale still False from its
    # initialiser — so a real Quad escaped marked fresh. Failing on cursor()
    # (above) never exercised that; this does.
    class _LateBoom:
        def execute(self, sql, *a): raise RuntimeError("server closed connection")
        def fetchone(self): return (None,)
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _LateConn:
        def cursor(self): return _LateBoom()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    fake.get_conn = lambda: _LateConn()
    sys.modules["db_pg"], sys.modules["tools.ps_flow"] = fake, ps
    try:
        lines2, quad2, stale2 = _header()
    finally:
        for k, v in old.items():
            sys.modules[k] = v if v is not None else sys.modules.pop(k, None)

    assert quad2 is None, f"a live Quad escaped a failed read: {quad2}"
    assert stale2 is True, stale2
    assert any("QUAD: unavailable" in ln for ln in lines2), lines2


def test_build_eod_pack_threads_staleness_into_the_section():
    """The last link: _header -> build_eod_pack -> quad_tape_block.

    Asserted on the SOURCE because running build_eod_pack needs network. A
    literal `stale=False` at the call site is exactly the mutation that survived
    review, so pin the identifier.
    """
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "tools", "eod_stat_pack.py"), encoding="utf-8").read()
    # 2026-08-17: _header now takes the RESOLVED SESSION so the vol block stops
    # anchoring on the wall clock (see vol_regime.regime_line). The call is
    # therefore _header(_lcs()), not _header(). The GUARD here is that the
    # staleness flag is UNPACKED rather than hardcoded -- pin that, not the
    # argument list, or every future argument change looks like a regression.
    assert "parts, header_quad, quad_stale = _header(" in src, \
        "build_eod_pack must unpack the staleness flag from _header"
    assert "_header()" not in src.replace("def _header()", ""), \
        "_header must be called WITH the resolved session, not the wall clock"
    assert "stale=quad_stale" in src, \
        "quad_tape_block must receive _header's flag, not a literal"


# ───────────────── the macro grind (D1-D6, 2026-08-24) ─────────────────────

from tools.eod_stat_pack import (COMMODITIES, CRYPTO, DOLLAR_FX,   # noqa: E402
                                 DURATION_CREDIT, GROUP_COLS, INTERNATIONAL,
                                 MACRO_GROUPS, SPLIT_AT, SUB_INDUSTRY,
                                 format_group_block, full_returns_row,
                                 relative_row, split_pack)


def _block_asserts(title, symbols, bench):
    """A missing symbol renders n/a (never 0.0), never disappears, and the
    coverage footer counts only symbols with at least one real number."""
    rows = [(s, {}, None) for s in symbols]           # nothing has bars
    rows[0] = (symbols[0], {"1D": 0.012}, {c: None for c in GROUP_COLS}
               if bench else None)                    # exactly one covered
    out = format_group_block(title, rows, bench)
    for s in symbols:
        assert s in out, f"{title}: {s} vanished from its own block"
    line0 = next(ln for ln in out.split("\n") if ln.startswith(symbols[1]))
    assert "n/a" in line0 and "+0.0%" not in line0, \
        f"{title}: a missing symbol must read n/a, never 0.0: {line0!r}"
    assert f"coverage: 1/{len(symbols)} symbols" in out, out.split("\n")[-1]


def test_commodities_block_missing_symbol_is_na():
    _block_asserts("COMMODITIES", COMMODITIES, "DBC")


def test_crypto_block_missing_symbol_is_na():
    _block_asserts("CRYPTO", CRYPTO, None)


def test_dollar_fx_block_missing_symbol_is_na():
    _block_asserts("DOLLAR + FX", DOLLAR_FX, "UUP")


def test_international_block_missing_symbol_is_na():
    _block_asserts("INTERNATIONAL", INTERNATIONAL, "SPY")


def test_duration_credit_block_missing_symbol_is_na():
    _block_asserts("DURATION + CREDIT", DURATION_CREDIT, None)


def test_sub_industry_block_missing_symbol_is_na():
    _block_asserts("SUB-INDUSTRY + THEME", SUB_INDUSTRY, "SPY")


def test_whole_block_missing_still_renders():
    """An entire ticker list with no bars must still produce a block (every
    row n/a, coverage 0/N) — the pack assembles around it rather than dying."""
    for title, syms, bench, _ in MACRO_GROUPS:
        out = format_group_block(title, [(s, {}, None) for s in syms], bench)
        assert f"coverage: 0/{len(syms)} symbols" in out, title
        assert "0.0" not in out, f"{title}: phantom zeros in an empty block"


def test_full_returns_row_has_the_group_columns():
    row = full_returns_row([], [])
    assert set(GROUP_COLS) <= set(row), row.keys()
    assert all(row[c] is None for c in GROUP_COLS), \
        "no bars must mean None everywhere, never 0.0"


def test_relative_row_drops_a_leg_rather_than_guessing():
    ab = {c: 0.02 for c in GROUP_COLS}
    bench = dict(ab, **{"1W": None})
    rel = relative_row(ab, bench)
    assert rel["1D"] == 0.0 and rel["1W"] is None


def test_split_pack_splits_at_block_boundaries_and_loses_nothing():
    blocks = [f"BLOCK {i}\n" + ("x" * 9_000) for i in range(6)]
    body = "\n\n".join(blocks)
    parts = split_pack(body, cap=20_000)
    assert len(parts) >= 2
    assert all(len(p) <= 20_000 for p in parts)
    assert "\n\n".join(parts) == body, "a split must lose nothing"
    for p in parts:
        assert p.startswith("BLOCK"), "every part must start on a boundary"
    assert split_pack(body, cap=len(body) + 1) == [body], \
        "a pack under the cap must ship as one file"


def test_dirty_tree_stamp_true_dirty_false_clean():
    """079 regression: a dirty tree records True (with the tracked count), a
    clean tree records False, and an unanswerable git records None/None —
    NULL is 'could not have known', never a guessed False."""
    from tools.eod_stat_pack import tree_state_from_porcelain as ts
    assert ts("", "") == (False, 0), "clean tree -> (False, 0)"
    assert ts(" M tools/pm_parse.py\n?? scratch.py\n",
              " M tools/pm_parse.py\n") == (True, 1)
    # untracked-only dirt is still dirt (untracked .py can be load-bearing),
    # but the tracked count says zero tracked modifications
    assert ts("?? tools/rs_corr.py\n", "") == (True, 0)
    assert ts(None, None) == (None, None), "git unavailable -> NULL, not False"
    assert ts(" M a.py\n M b.py\n M c.py\n", " M a.py\n M b.py\n M c.py\n") \
        == (True, 3)


def test_provenance_resolution_order_is_honest_at_every_step():
    """082 (A3/A4): each resolution branch returns a (sha, built_by,
    sha_source) triple describing the SAME process, and dirty_tree attaches
    ONLY to a local-git sha — NULL everywhere else, never a guess."""
    from tools.eod_stat_pack import apply_dirty_rule, resolve_provenance as rp

    # 1. local git wins, named machine
    p = rp("aaa111", "bbb222", "ccc333", "WINBOX")
    assert (p["sha"], p["built_by"], p["sha_source"]) == \
        ("aaa111", "WINBOX", "local-git")
    # 2. railway env
    p = rp(None, "bbb222", "ccc333", "WINBOX")
    assert (p["sha"], p["built_by"], p["sha_source"]) == \
        ("bbb222", "railway", "railway-env")
    # 3. bot_state is ANOTHER process's stamp — machine must read unknown
    p = rp(None, None, "ccc333", "WINBOX")
    assert (p["sha"], p["built_by"], p["sha_source"]) == \
        ("ccc333", "unknown", "bot_state")
    # 4. nothing
    p = rp(None, None, None, "WINBOX")
    assert (p["sha"], p["built_by"], p["sha_source"]) == \
        (None, "WINBOX", "unknown")
    assert rp(None, None, None, None)["built_by"] == "unknown"

    # A4: dirty only rides a local sha
    dirty = (True, 14)
    assert apply_dirty_rule(rp("aaa", None, None, "H"), dirty)["dirty_tree"] \
        is True
    for p in (rp(None, "bbb", None, "H"), rp(None, None, "ccc", "H"),
              rp(None, None, None, "H")):
        out = apply_dirty_rule(p, dirty)
        assert out["dirty_tree"] is None and out["dirty_tracked_n"] is None, \
            f"dirty must be NULL for sha_source={p['sha_source']}"


def test_persist_false_never_touches_the_ledger_and_default_is_true():
    """B (2026-08-25): build_eod_pack(persist=False) must not call
    _persist_pack at all — the test suite used to file three packs into the
    production ledger per sweep. The default stays True."""
    import inspect as _ins

    import tools.eod_stat_pack as esp
    import tools.trading_calendar as tc

    sig = _ins.signature(esp.build_eod_pack)
    assert sig.parameters["persist"].default is True

    calls = []
    saved = (esp._header, esp._bars_from_store, esp._persist_pack,
             tc.resolve_session_date, tc.validate_bar_date,
             tc.duplicate_final_bar, tc.last_completed_session)
    try:
        esp._header = lambda asof=None: (["HDR"], None, True)
        esp._bars_from_store = lambda a, s: {
            sym: {"closes": [1.0], "dates": [dt.date(2026, 8, 22)]}
            for sym in s}
        esp._persist_pack = lambda *a, **k: calls.append(a)
        tc.resolve_session_date = lambda bars: (dt.date(2026, 8, 22), [])
        tc.validate_bar_date = lambda d: (False, "test: forced block")
        tc.duplicate_final_bar = lambda bars: (False, "")
        tc.last_completed_session = lambda: dt.date(2026, 8, 21)
        out = esp.build_eod_pack(persist=False)     # blocked path, no persist
        assert "EOD PACK BLOCKED" in out
        assert calls == [], "persist=False must not call _persist_pack"
        esp.build_eod_pack()                        # default True DOES persist
        assert len(calls) == 1
    finally:
        (esp._header, esp._bars_from_store, esp._persist_pack,
         tc.resolve_session_date, tc.validate_bar_date,
         tc.duplicate_final_bar, tc.last_completed_session) = saved


def test_blocked_pack_persists_exactly_once():
    """D8 regression: the blocked path used to have _persist_pack AFTER a
    return (unreachable, `blocked` undefined) — a blocked run was never
    archived, which is exactly the unfalsifiability the 8/16 Sunday-bar bug
    had. The banner must persist exactly once, with valid=False."""
    import tools.eod_stat_pack as esp
    import tools.trading_calendar as tc

    calls = []
    saved = (esp._header, esp._bars_from_store, esp._persist_pack,
             tc.resolve_session_date, tc.validate_bar_date,
             tc.duplicate_final_bar, tc.last_completed_session)
    try:
        esp._header = lambda asof=None: (["HDR"], None, True)
        esp._bars_from_store = lambda a, s: {
            sym: {"closes": [1.0], "dates": [dt.date(2026, 8, 22)]}
            for sym in s}
        esp._persist_pack = lambda body, lb, valid, reason, ok, tot, built: \
            calls.append({"valid": valid, "reason": reason, "body": body})
        tc.resolve_session_date = lambda bars: (dt.date(2026, 8, 22), [])
        tc.validate_bar_date = lambda d: (False, "test: Saturday bar")
        tc.duplicate_final_bar = lambda bars: (False, "")
        tc.last_completed_session = lambda: dt.date(2026, 8, 21)
        out = esp.build_eod_pack()
    finally:
        (esp._header, esp._bars_from_store, esp._persist_pack,
         tc.resolve_session_date, tc.validate_bar_date,
         tc.duplicate_final_bar, tc.last_completed_session) = saved

    assert "EOD PACK BLOCKED" in out
    assert len(calls) == 1, f"blocked pack must persist exactly once: {calls}"
    assert calls[0]["valid"] is False
    assert "Saturday bar" in calls[0]["reason"]
    assert calls[0]["body"] == out, "the archived body must be the banner"


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
