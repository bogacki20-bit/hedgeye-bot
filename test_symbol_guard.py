"""test_symbol_guard.py — the 2026-08-23 write-time symbol validation.

The malformed-ticker fix has two layers: tools/symbol_guard.py (this file's
subject) gates every parser at WRITE time; the enrollment gate remains as
defense-in-depth at print time. Three malformation classes, each with its
own section here:
  1. suffix splitting   (RPI.L -> RPI + L)
  2. OCR/prose word-tokens and near-misses (MORRIS, WIDEST, BUXXX, APPL)
  3. raw option contract strings stored as underlyings

Run:  python test_symbol_guard.py
Offline: known_universe and resolve_live are monkeypatched per test.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools import symbol_guard as sg                       # noqa: E402

# The live universe's real suffixed citizens — the acceptance list.
SUFFIXED = ["RPI.L", "005930.KS", "1913.HK", "2331.HK", "ADS.DE", "ES_F"]


def _with(known, live, fn):
    """Run fn() with known_universe and resolve_live stubbed."""
    ok, ol = sg.known_universe, sg.resolve_live
    sg.known_universe = lambda refresh=False: set(known)
    sg.resolve_live = live
    try:
        return fn()
    finally:
        sg.known_universe, sg.resolve_live = ok, ol


# ───────────────────────── 1. suffix handling ───────────────────────────────

def test_acceptance_suffixed_symbols_are_plausible_intact():
    for t in SUFFIXED + ["FESB_F", "FXXP_F", "FESX_F", "VOLV-B.ST", "BRK-B",
                         "BF.B", "^VIX", "^RVX"]:
        assert sg.plausible(t), t


def test_signal_strength_regex_no_longer_splits_suffixed_names():
    """The original TICKER_RE (\\b[A-Z]{1,5}\\b) returned ['RPI', 'L'] for
    'RPI.L' — the '.' is a word boundary. The suffix is part of the symbol."""
    from parser_signal_strength import TICKER_RE
    for t in ("RPI.L", "005930.KS", "1913.HK", "ADS.DE"):
        got = [m.group(1) for m in TICKER_RE.finditer(f"Added: {t}, AAPL")]
        assert t in got, f"{t} split into {got}"
        assert "L" not in got and "KS" not in got and "HK" not in got \
            and "DE" not in got or t.split(".")[-1] not in got, got


def test_signal_strength_block_regex_admits_dots():
    """ADDED_RE's capture class must include '.'/'-' or the block capture
    terminates mid-symbol before TICKER_RE ever sees the suffix."""
    from parser_signal_strength import ADDED_RE
    m = ADDED_RE.search("Added: RPI.L, MSFT Removed: X")
    assert m and "RPI.L" in m.group(1), m.group(1) if m else None


def test_portsol_regexes_are_suffix_aware():
    from parser_portfolio_solutions import TICKER_TOKEN_RE, TICKER_LIST_RE
    for rx in (TICKER_TOKEN_RE, TICKER_LIST_RE):
        got = [m.group(1) for m in rx.finditer("ADS.DE and OIH")]
        assert "ADS.DE" in got, got


# ───────────────────────── 2. OCR / word-tokens ─────────────────────────────

def test_word_tokens_unknown_and_unresolvable_are_dropped():
    kept, dropped = _with(
        {"AAPL", "PM"}, lambda t: False,
        lambda: sg.validate_for_storage(
            ["AAPL", "MORRIS", "WIDEST", "PM", "BUXXX"], "test"))
    assert kept == ["AAPL", "PM"]
    assert dropped == ["MORRIS", "WIDEST", "BUXXX"]


def test_near_miss_cannot_silently_become_the_real_ticker():
    """APPL must never be corrected to AAPL — there is deliberately no fuzzy
    matching anywhere. It is either known (it isn't), resolvable (it isn't),
    or dropped."""
    kept, dropped = _with({"AAPL"}, lambda t: False,
                          lambda: sg.validate_for_storage(["APPL"], "test"))
    assert kept == [] and dropped == ["APPL"]


def test_new_real_symbol_is_accepted_via_live_quote():
    kept, dropped = _with(set(), lambda t: True,
                          lambda: sg.validate_for_storage(["NEWCO"], "test"))
    assert kept == ["NEWCO"] and dropped == []


def test_probe_outage_fails_open_never_drops_on_it():
    """A yfinance outage is not evidence against a token — a real-time Keith
    signal must not be lost to a rate limit. The enrollment print-gate still
    stands downstream."""
    kept, dropped = _with(set(), lambda t: None,
                          lambda: sg.validate_for_storage(["MAYBE"], "test"))
    assert kept == ["MAYBE"] and dropped == []


def test_membership_beats_shape_for_macro_instruments():
    """USD/YEN is slash-separated (shape-fail) but legitimately lives in
    hedgeye_risk_ranges — membership must admit it so mention recording for
    macro instruments keeps working."""
    kept, dropped = _with({"USD/YEN"}, lambda t: False,
                          lambda: sg.validate_for_storage(["USD/YEN"], "test"))
    assert kept == ["USD/YEN"]


def test_numeric_only_tokens_are_never_plausible():
    for t in ("2513", "100.00", "1913", ""):
        assert not sg.plausible(t), t


def test_pm_parse_regex_requires_a_letter():
    from tools.pm_parse import _TICKER_RE
    assert _TICKER_RE.match("AAPL") and _TICKER_RE.match("005930.KS")
    assert _TICKER_RE.match("BF-B") and _TICKER_RE.match("RI.PA")
    assert not _TICKER_RE.match("2513")
    assert not _TICKER_RE.match("100.00")


def test_mag7_is_a_deliberate_pseudo_instrument():
    """parser_momo normalizes MAG -> MAG7 on purpose; the guard must admit
    it without a probe (it resolves nowhere)."""
    kept, _ = _with(sg._alias_names(), lambda t: False,
                    lambda: sg.validate_for_storage(["MAG7"], "momo"))
    assert kept == ["MAG7"]


# ───────────────────────── 3. option contract strings ───────────────────────

def test_option_strings_rejected_even_when_membership_holds_them():
    """A raw contract stored as a book underlying must never self-validate
    through the membership union."""
    kept, dropped = _with({"-XLV260717C230"}, lambda t: True,
                          lambda: sg.validate_for_storage(
                              ["-XLV260717C230", "XLV260717C230.5"], "test"))
    assert kept == []
    assert len(dropped) == 2


def test_ingest_fidelity_rejects_unparseable_option_positions():
    """Positions path DECISION: reject the row outright (these accounts hold
    no options by rule), with a loud anomaly that blocks CLI auto-commit."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ingest_fidelity.py"), encoding="utf-8").read()
    seg = src.split("def parse_positions")[1].split("def ")[1 - 1]
    seg = src.split("def parse_positions")[1]
    head = seg[:seg.find("def parse_activity")]
    assert "row rejected" in head
    assert "underlying, asset = sym, \"option\"" not in head, \
        "positions path still stores the raw contract string as underlying"


def test_ingest_fidelity_activity_keeps_row_but_nulls_underlying():
    """Activity path DECISION: the row is a REAL cash movement — keep it,
    null the underlying, keep the raw form in `symbol` for audit."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "ingest_fidelity.py"), encoding="utf-8").read()
    act = src.split("def parse_activity")[1]
    assert "underlying nulled" in act
    assert "underlying = sym\n                    anomalies.append" not in act


# ───────────────────────── wiring assertions ────────────────────────────────

def test_every_offending_parser_routes_through_the_guard():
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in ("parser_retail.py", "parser_momo.py",
                "parser_keiths_signals.py", "parser_rta.py",
                "parser_portfolio_solutions.py", "parser_signal_strength.py",
                "tools/pm_parse.py", "ticker_inventory.py"):
        src = open(os.path.join(here, rel), encoding="utf-8").read()
        assert "symbol_guard" in src, f"{rel} not wired to the write gate"


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
