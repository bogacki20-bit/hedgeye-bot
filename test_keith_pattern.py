"""Fixture tests for the Keith add-pattern state machine (pure, no DB).
    python test_keith_pattern.py
"""
from tools.keith_pattern import detect, ss_tag


def r(d, price, trend, lo=95.0, hi=110.0):
    return (d, price, lo, hi, trend)


FULL = [r(1, 100, "BEARISH"),      # known non-bull (seed)
        r(2, 105, "BULLISH"),      # ENTRY (transition)
        r(3, 104, "BULLISH"),      # rp .60 -> ARMED
        r(4, 99, "BULLISH"),       # rp .27 -> PULLED
        r(5, 96, "BULLISH"),       # rp .07 -> TESTED (on support)
        r(6, 98, "BULLISH")]       # closes UP off support -> SETUP


def test_full_sequence_fires():
    setups, stage = detect(FULL)
    assert len(setups) == 1
    s = setups[0]
    assert s["date"] == 6 and s["entry_date"] == 2 and s["test_date"] == 5
    assert stage == "ARMED"                     # re-armed for a later test


def test_first_sighting_bullish_seeds_silently():
    rows = [r(1, 105, "BULLISH"), r(2, 104, "BULLISH"), r(3, 99, "BULLISH"),
            r(4, 96, "BULLISH"), r(5, 98, "BULLISH")]
    setups, stage = detect(rows)
    assert setups == [] and stage == "IDLE"     # standing bull != entry


def test_loose_mode_accepts_standing_bull():
    rows = [r(1, 105, "BULLISH"), r(2, 104, "BULLISH"), r(3, 99, "BULLISH"),
            r(4, 96, "BULLISH"), r(5, 98, "BULLISH")]
    setups, _ = detect(rows, entry_on_standing=True)
    assert len(setups) == 1 and setups[0]["test_date"] == 4


def test_trend_flip_resets_thesis():
    rows = FULL[:5] + [r(6, 96, "BEARISH"), r(7, 99, "BULLISH"),
                       r(8, 104, "BULLISH")]
    setups, _ = detect(rows)
    assert setups == []                         # flip killed it pre-hold
    # ...but the re-entry at d7 is a fresh ENTRY (transition from BEARISH)


def test_support_break_is_not_a_hold():
    rows = FULL[:5] + [r(6, 93, "BULLISH"),     # CLOSED below support
                       r(7, 98, "BULLISH")]
    setups, _ = detect(rows)
    assert setups == []                         # lost, not held


def test_no_pullback_no_setup():
    rows = [r(1, 100, "BEARISH"), r(2, 105, "BULLISH"), r(3, 106, "BULLISH"),
            r(4, 107, "BULLISH"), r(5, 108, "BULLISH")]
    setups, stage = detect(rows)
    assert setups == [] and stage == "ARMED"    # strong entry, still armed


def test_deep_flush_day_arms_pulls_and_tests_in_one_bar():
    rows = [r(1, 100, "BEARISH"), r(2, 105, "BULLISH"), r(3, 104, "BULLISH"),
            r(4, 96, "BULLISH"),                # .07: PULLED + TESTED same bar
            r(5, 99, "BULLISH")]                # hold -> SETUP
    setups, _ = detect(rows)
    assert len(setups) == 1 and setups[0]["test_date"] == 4


def test_rearm_allows_second_setup():
    rows = FULL + [r(7, 105, "BULLISH"),        # rp .67 (already ARMED)
                   r(8, 99, "BULLISH"),         # PULLED
                   r(9, 96, "BULLISH"),         # TESTED
                   r(10, 97, "BULLISH")]        # HOLD -> second SETUP
    setups, _ = detect(rows)
    assert len(setups) == 2 and setups[1]["date"] == 10


def test_ss_tags_drop_beats_roster():
    # a recent SS drop is the invalidation tell — flagged, never hidden
    assert ss_tag("BROS", {"BROS"}, {}) == "BROS·SS"
    assert ss_tag("EXP", set(), {"EXP": "2026-07-08"}) == "EXP✗SSdrop@2026-07-08"
    assert ss_tag("EXP", {"EXP"}, {"EXP": "2026-07-08"}) == \
        "EXP✗SSdrop@2026-07-08"                    # drop outranks roster
    assert ss_tag("KO", set(), {}) == "KO"


def test_missing_range_days_never_crash():
    rows = [r(1, 100, "BEARISH"), r(2, 105, "BULLISH"),
            (3, 104.0, None, None, "BULLISH"),  # DARK day mid-sequence
            r(4, 96, "BULLISH"), r(5, 98, "BULLISH")]
    setups, _ = detect(rows)                    # no armed (dark day skipped rp)
    assert isinstance(setups, list)


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
