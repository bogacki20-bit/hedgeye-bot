"""Trade / Trend lines: 34-day and 89-day EMAs, Keith's number wins (Kris 11/2)."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.level_lines import MIN_BARS, completed_bars, ema_lines, parse_trend_tail, pick_level  # noqa: E402


def test_flat_prices_give_flat_lines():
    trade, trend = ema_lines([50.0] * MIN_BARS)
    assert abs(trade - 50) < 1e-9 and abs(trend - 50) < 1e-9


def test_too_little_history_gives_nothing():
    assert ema_lines([50.0] * (MIN_BARS - 1)) is None


def test_trade_reacts_faster_than_trend():
    trade, trend = ema_lines([100.0] * 200 + [110.0] * 20)
    assert 100 < trend < trade < 110


def test_matches_the_recursive_formula():
    closes = [100.0 + i for i in range(MIN_BARS)]
    a, e = 2 / 35, closes[0]
    for c in closes[1:]:
        e = a * c + (1 - a) * e
    assert abs(ema_lines(closes)[0] - e) < 1e-9


def test_todays_bar_only_counts_after_the_close():
    d = dt.date(2026, 11, 2)
    dates = [dt.date(2026, 10, 30), d]
    assert completed_bars(dates, dt.datetime(2026, 11, 2, 15, 0)) == 1
    assert completed_bars(dates, dt.datetime(2026, 11, 2, 16, 30)) == 2
    assert completed_bars(dates[:1], dt.datetime(2026, 11, 2, 15, 0)) == 1


def test_keith_wins_then_email_then_ema():
    assert pick_level("trade", 97.0, None, 96.7, True)[:2] == (97.0, "Keith Alpha Code")
    assert pick_level("trend", None, 287.0, 284.0, False)[:2] == (287.0, "Keith Trend & Tail email")
    assert pick_level("trend", None, None, 284.0, False)[:2] == (284.0, "89-day EMA")
    assert pick_level("trade", None, 287.0, 280.0, False)[:2] == (280.0, "34-day EMA")
    assert pick_level("trade", None, None, None, False)[0] is None


def test_parses_the_trend_and_tail_table():
    cells = ["Ticker", "IWM", "▼", "279.33", "287.00", "Below", "▼", "272.00", "Above", "BNO", "▲", "71.39",
             "55.00", "Above", "49.00", "Above", "x"]
    rows = parse_trend_tail("".join(f"<td>{c}</td>" for c in cells))
    assert rows == [{"t": "IWM", "trend": 287.0, "tail": 272.0}, {"t": "BNO", "trend": 55.0, "tail": 49.0}]
