"""Fixture tests for TRANCHE v2 + v4.1 pure logic (no DB) — recalibrated
default tiers (fi 10 / core 4 / eq 2 / sat 1, shorts→sat), fill buckets,
TARGET/CASHEQ parsing, compact-vs-verbose fill formatting, multi-account
aggregation.
    python test_position_targets.py
"""
from tools.position_targets import (default_target, fill_bucket,
                                    parse_target_command, fmt_fill_ctx,
                                    aggregate_split, account_code)


# ── default tiers (v4.1 FIX 1) ──

def test_default_fixed_income_fund():
    assert default_target("ISHARES 20+ YEAR TREASURY BOND ETF") == (10.0, "dflt-fi")


def test_default_fidelity_abbreviations_route_fi():
    # Fidelity abbreviates: TREAS/TRS/BD — the 7/11 dry-run misses
    assert default_target("ISHARES 20 PLUS YR TREAS BD ETF") == (10.0, "dflt-fi")
    assert default_target("PIMCO 25+ YR ZERO CPN US TRS INDEX ETF") == (10.0, "dflt-fi")
    assert default_target("SIMPLIFY AGGREGATE BD FD") == (10.0, "dflt-fi")


def test_default_core_theme_etfs():
    assert default_target("HEALTH CARE SELECT SECTOR SPDR FUND") == (4.0, "dflt-core")
    assert default_target("US GLOBAL JETS ETF") == (4.0, "dflt-core")
    assert default_target("VANGUARD HIGH DIVIDEND YIELD ETF") == (4.0, "dflt-core")


def test_default_single_name_is_2():
    assert default_target("UNITEDHEALTH GROUP INC") == (2.0, "dflt-eq")
    assert default_target(None) == (2.0, "dflt-eq")


def test_default_satellites_are_1():
    # inverse/levered · commodity · single-country MSCI · crypto wrappers
    assert default_target("PROSHARES ULTRASHORT EURO") == (1.0, "dflt-sat")
    assert default_target("DIREXION DAILY S&P OIL GAS EXP BEAR 2X SHARES") == (1.0, "dflt-sat")
    assert default_target("ABRDN PHYSICAL PALLADIUM SHARES ETF") == (1.0, "dflt-sat")
    assert default_target("ISHARES MSCI HONG KONG ETF") == (1.0, "dflt-sat")
    assert default_target("PROSHARES ULTRASHORT BITCOIN ETF") == (1.0, "dflt-sat")


def test_default_gold_ticker_lesson():
    # plain stock stays eq no matter its words — fund naming is REQUIRED
    assert default_target("BARRICK GOLD CORP") == (2.0, "dflt-eq")


def test_default_short_exposure_routes_sat():
    assert default_target("HEALTH CARE SELECT SECTOR SPDR FUND",
                          side="short") == (1.0, "dflt-sat")
    assert default_target("UNITEDHEALTH GROUP INC", side="short") == (1.0, "dflt-sat")


def test_default_fi_beats_sat_keywords():
    # an ultra-short-DURATION bond fund is fi, not a satellite
    assert default_target("ISHARES ULTRASHORT DURATION BOND ETF")[1] == "dflt-fi"


# ── fill buckets ──

def test_fill_buckets():
    assert fill_bucket(0) == "STARTER"
    assert fill_bucket(39.9) == "STARTER"
    assert fill_bucket(40) == "BUILDING"
    assert fill_bucket(79.9) == "BUILDING"
    assert fill_bucket(80) == "FULL"
    assert fill_bucket(110) == "FULL"
    assert fill_bucket(110.1) == "OVER"
    assert fill_bucket(None) == "?"


# ── TARGET command parsing ──

def test_parse_list_and_non_command():
    assert parse_target_command("TARGET") == {"op": "list"}
    assert parse_target_command("TARGET LIST") == {"op": "list"}
    assert parse_target_command("SCREEN energy longs") is None
    assert parse_target_command("") is None


def test_parse_set_defaults_to_ind():
    q = parse_target_command("TARGET FXH 4.0")
    assert q == {"op": "set", "ticker": "FXH", "pct": 4.0, "account": "IND",
                 "note": None}


def test_parse_set_with_account_and_note():
    q = parse_target_command("TARGET tlt 8 RIRA core duration sleeve")
    assert q["ticker"] == "TLT" and q["pct"] == 8.0 and q["account"] == "RIRA"
    assert q["note"] == "core duration sleeve"


def test_parse_set_pct_bounds_and_junk():
    assert "error" in parse_target_command("TARGET FXH 0")
    assert "error" in parse_target_command("TARGET FXH 26")
    assert "error" in parse_target_command("TARGET FXH abc")


def test_parse_del():
    assert parse_target_command("TARGET DEL FXH") == {
        "op": "del", "ticker": "FXH", "account": "IND"}
    assert parse_target_command("TARGET DEL FXH ROTH")["account"] == "ROTH"
    assert "error" in parse_target_command("TARGET DEL FXH XXXX")
    assert "error" in parse_target_command("TARGET DEL")


def test_parse_casheq():
    assert parse_target_command("TARGET CASHEQ BUXX") == {
        "op": "casheq", "ticker": "BUXX"}
    assert parse_target_command("TARGET NOCASHEQ buxx") == {
        "op": "nocasheq", "ticker": "BUXX"}
    assert "error" in parse_target_command("TARGET CASHEQ")


def test_parse_pct_percent_sign_tolerated():
    assert parse_target_command("TARGET SHY 10%")["pct"] == 10.0


# ── formatting (FIX 4: one computation, two renders) ──

def test_fmt_compact_default_hides_tgt():
    s = fmt_fill_ctx(3.1, 52.0, 4.0, "dflt-core", "IND", 6.0)
    assert s == ",3.1%acct,52%,IND,+6.0%pl"          # tgt/src hidden


def test_fmt_compact_shows_tgt_only_when_explicit():
    assert fmt_fill_ctx(3.1, 52.0, 4.0, None, "IND", 6.0) == \
        ",3.1%acct,52%→4.0%tgt,IND,+6.0%pl"          # explicit -> shown
    # v4.1 FIX 3: OVER on a default no longer prints the target in compact
    assert fmt_fill_ctx(9.4, 157.0, 6.0, "dflt-core", "IND", -1.6) == \
        ",9.4%acct,157%,IND,-1.6%pl"


def test_fmt_verbose_always_full():
    s = fmt_fill_ctx(3.1, 52.0, 4.0, "dflt-core", "IND", 6.0, verbose=True)
    assert s == ",3.1%acct,52%fill→4.0%tgt·dflt-core,IND,+6.0%pl"


def test_fmt_missing_prints_question_marks():
    assert fmt_fill_ctx(None, None, None, "x", None, None) == \
        ",?%acct,?%,?,?%pl"


# ── multi-account aggregation (FIX 3) ──

def test_aggregate_split_math():
    # ZROZ: $2k in a $40k RIRA @10% tgt, $1k in a $60k IND @10% tgt
    # exposure = 3k/100k = 3.0% · tgt dollars = 4k+6k=10k · fill = 30%
    pct, fill = aggregate_split([(2000, 40000, 10.0), (1000, 60000, 10.0)])
    assert abs(pct - 3.0) < 1e-9
    assert abs(fill - 30.0) < 1e-9


def test_aggregate_split_handles_missing_totals():
    pct, fill = aggregate_split([(2000, 0, 10.0)])
    assert pct is None and fill is None


# ── account mapping ──

def test_account_codes():
    assert account_code("X96383748") == "IND"
    assert account_code("244859926") == "RIRA"
    assert account_code("245734604") == "ROTH"
    assert account_code("999999") == "999999"     # unknown passes through loud
    assert account_code(None) == "?"


if __name__ == "__main__":
    import sys, inspect
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
    print("ALL PASS" if not fails else f"{fails} FAILURE(S)")
    sys.exit(1 if fails else 0)
