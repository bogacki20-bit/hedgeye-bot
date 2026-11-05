"""Research tilt and ETF Pro update emails (Kris 11/5/2026)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from parser_etf_pro import parse_update_body  # noqa: E402
from tools.research_tilt import idea_side, read_held, rta_signal  # noqa: E402

UPDATE = ("<p>How to Use ETF Pro Plus</p><p>We are ADDING Long : Utilities Sector (XLU) Long Treasury Bond (TLT)</p>"
          "<p>We are ADDING Short : Euro (FXE)</p><p>We are REMOVING Short : Global Private Equity (PSP) - (55.18 - 60.5)</p>"
          "<p>We are REMOVING Long : Brent Oil (BNO) - (65.8 - 73.33)</p><p>How to Use ETF Pro Plus Updates: Addition (XYZ)</p>")


def test_update_email_names_adds_and_removes():
    rows = {r["ticker"]: r for r in parse_update_body(UPDATE)}
    assert sorted(rows) == ["BNO", "FXE", "PSP", "TLT", "XLU"]
    assert rows["XLU"]["action"] == "add" and rows["XLU"]["bias"] == "long" and rows["XLU"]["range_low"] is None
    assert rows["FXE"]["action"] == "add" and rows["FXE"]["bias"] == "short"
    assert rows["BNO"]["action"] == "remove" and rows["BNO"]["range_high"] == 73.33


def test_update_parser_ignores_a_weekly_body():
    assert parse_update_body("<td>XLU 11/01/2026 43.00 40.00 45.00</td>") == []


def test_rta_cover_and_booked_gain_close_a_side():
    assert rta_signal("short", "BUY", True) == (0, "closing short")
    assert rta_signal("long", "SELL", False) == (0, "closing long")
    assert rta_signal("short", "SELL", False) == (-1, "opening")
    assert rta_signal("long", "BUY", False) == (1, "opening")


def test_held_name_is_against_when_anything_points_the_other_way():
    agree, oppose = {"src": "SS", "dir": 1}, {"src": "RTA", "dir": -1}
    assert read_held(1, [agree]) == "with"
    assert read_held(1, [agree, oppose]) == "against"
    assert read_held(-1, [oppose]) == "with"
    assert read_held(1, []) == ""


def test_dropped_from_a_list_or_closed_counts_against_that_side_only():
    off_long = {"src": "SS-off", "dir": 0, "closes": 1}
    assert read_held(1, [off_long]) == "against"
    assert read_held(-1, [off_long]) == ""


def test_ideas_need_agreement_and_count_separate_sources():
    assert idea_side([{"src": "SS", "dir": -1}, {"src": "ETFPro", "dir": -1}, {"src": "SS", "dir": -1}]) == (-1, 2)
    assert idea_side([{"src": "SS", "dir": -1}, {"src": "RTA", "dir": 1}]) == (0, 0)
    assert idea_side([{"src": "RTA", "dir": 0}]) == (0, 0)
