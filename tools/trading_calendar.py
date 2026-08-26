"""NYSE trading calendar — pure, no dependencies, no network.

WHY THIS EXISTS (2026-08-16). The EOD stat pack printed
    (price data: 42/42 symbols - last bar 2026-08-16 - built 2026-08-16 10:27 ET)
and 2026-08-16 is a SUNDAY. No such bar exists; the last completed session was
Friday 2026-08-14. Every field labelled "1D" was therefore Thursday->Friday,
not what the header claimed.

That is the most dangerous class of bug in this system: it fails silently and
looks correct. The pack took its bar date from the last row of the frame
(`max(dates[-1])`) with no validation that the date is a session at all.

The defect is ENVIRONMENTAL, not versional -- verified 2026-08-16:
  * the deployed build at 14:27 UTC was ddc18a0, booted 14:06 UTC
  * `git diff ddc18a0 HEAD -- tools/eod_stat_pack.py` is EMPTY
  * calling the same _fetch_bars() locally returns 2026-08-14 correctly
Same code, different result, so the upstream frame differs by environment
(yfinance version or tz-localisation on Railway appending a partial "today"
row). This module cannot fix that. It makes the pack REFUSE to publish a bar
date that is not a real session, which is the part that is ours to control.

No new dependency: pandas_market_calendars / exchange_calendars are not
installed and adding one to the Railway image to answer "is this a weekday"
is not a trade worth making. NYSE holidays are rule-based and stable.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

# NYSE observes Juneteenth from 2022.
_JUNETEENTH_FROM = 2022


def _easter(year: int) -> date:
    """Anonymous Gregorian algorithm. Good Friday = Easter - 2 days."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (Mon=0) of a month. n=-1 for the last one."""
    if n > 0:
        d = date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + timedelta(days=offset + 7 * (n - 1))
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """NYSE shifts a Saturday holiday to the preceding Friday and a Sunday
    holiday to the following Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set:
    """Full-day NYSE closures for `year`. Half-days are NOT holidays: the
    market trades and a bar exists, which is all this module rules on."""
    out = set()
    # New Year's Day. NYSE does NOT observe it on Dec 31 when Jan 1 is a
    # Saturday -- the exception to the _observed rule.
    ny = date(year, 1, 1)
    if ny.weekday() == 6:
        out.add(date(year, 1, 2))
    elif ny.weekday() != 5:
        out.add(ny)
    out.add(_nth_weekday(year, 1, 0, 3))          # MLK, 3rd Monday Jan
    out.add(_nth_weekday(year, 2, 0, 3))          # Washington, 3rd Monday Feb
    out.add(_easter(year) - timedelta(days=2))    # Good Friday
    out.add(_nth_weekday(year, 5, 0, -1))         # Memorial, last Monday May
    if year >= _JUNETEENTH_FROM:
        out.add(_observed(date(year, 6, 19)))
    out.add(_observed(date(year, 7, 4)))          # Independence Day
    out.add(_nth_weekday(year, 9, 0, 1))          # Labor, 1st Monday Sep
    out.add(_nth_weekday(year, 11, 3, 4))         # Thanksgiving, 4th Thu Nov
    out.add(_observed(date(year, 12, 25)))        # Christmas
    return out


def is_trading_day(d: date) -> bool:
    """Weekday and not a full-day NYSE closure."""
    if d.weekday() >= 5:
        return False
    return d not in nyse_holidays(d.year)


def previous_trading_day(d: date) -> date:
    """The session strictly before `d`."""
    c = d - timedelta(days=1)
    while not is_trading_day(c):
        c -= timedelta(days=1)
    return c


def last_completed_session(now=None, close_hour_et: int = 16) -> date:
    """The most recent session that has FULLY SETTLED.

    Today counts only if today is a session AND the cash close has passed.
    A pre-market build at 05:30 ET therefore resolves to the prior session,
    which is exactly what the pack is supposed to report on.
    """
    now = now or _now_et()
    today = now.date()
    if is_trading_day(today) and now.hour >= close_hour_et:
        return today
    return previous_trading_day(today)


def _now_et() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.now()


def validate_bar_date(bar_date, now=None) -> tuple:
    """(ok, reason). The assertion the pack must pass before it prints anything.

    Fails when the bar date is missing, is not a session, is in the future, or
    lags the last completed session -- each with a reason naming the actual
    expected session, so the banner is diagnostic rather than just angry.
    """
    now = now or _now_et()
    today = now.date()
    expected = last_completed_session(now)
    if bar_date is None:
        return False, "no bar date resolved from the price frame"
    if isinstance(bar_date, datetime):
        bar_date = bar_date.date()
    if bar_date > today:
        return False, ("last bar %s is in the FUTURE (today %s)"
                       % (bar_date, today))
    if not is_trading_day(bar_date):
        why = ("a weekend" if bar_date.weekday() >= 5
               else "an NYSE holiday")
        return False, ("last bar %s is %s (%s) -- no such session exists; "
                       "the last completed session was %s"
                       % (bar_date, why, bar_date.strftime("%A"), expected))
    if bar_date < expected:
        return False, ("last bar %s LAGS the last completed session %s -- the "
                       "frame is stale by %d session(s)"
                       % (bar_date, expected,
                          _sessions_between(bar_date, expected)))
    return True, "last bar %s is the last completed session" % bar_date


def _sessions_between(a: date, b: date) -> int:
    n, c = 0, a
    while c < b:
        c += timedelta(days=1)
        if is_trading_day(c):
            n += 1
    return n


def resolve_session_date(bars: dict, now=None) -> tuple:
    """(session_date, offenders) — the EQUITY session the frame represents.

    ROOT CAUSE, found 2026-08-16. The pack resolved its bar date as
        max(b["dates"][-1] for b in bars.values())
    over ALL symbols. 41 of 42 reported Friday 2026-08-14. BTC-USD reported
    SUNDAY 2026-08-16, because Bitcoin trades 24/7 and genuinely has a weekend
    bar. max() therefore stamped a CRYPTO date on a header describing an equity
    session, and every "1D" field silently became Thursday->Friday.

    It was not tz-localisation, not a partial row, not a duplicated bar, and not
    a deployed-vs-HEAD mismatch. It is one 24/7 instrument inside a max() over
    exchange-traded ones, which is why it reproduces only when the crypto symbol
    is in the fetch -- a 5-symbol equity probe misses it entirely.

    Fix: consider only last-bar dates that ARE NYSE sessions, and take the max
    of those. Symbols sitting off that consensus are RETURNED, not dropped
    quietly -- a 24/7 instrument is legitimate and its data is still used for
    correlations; it simply must not define the session.

    SECOND ROOT CAUSE, found 2026-08-25. The session filter above screens out
    NON-SESSIONS but not the FUTURE. At 21:01 ET on Tue 2026-08-25 (already
    01:01 UTC on the 26th) the 24/7 crypto symbols had rolled to their
    2026-08-26 daily bar. 2026-08-26 is a Wednesday -- a perfectly valid NYSE
    session -- so it passed is_trading_day() and won the max(), and
    validate_bar_date then (correctly) blocked the pack. Same instrument
    class, new edge: a crypto bar can be a FUTURE session, not just a weekend.

    Fix: also exclude dates later than the last completed session. No
    hardcoded list of 24/7 tickers -- both incidents came from unmaintained
    assumptions about crypto, and a list is one more of those.
    """
    dates = {}
    for sym, b in (bars or {}).items():
        d = (b or {}).get("dates") or []
        if d:
            dates[sym] = d[-1]
    if not dates:
        return None, []
    sessions = [d for d in dates.values()
                if is_trading_day(d) and d <= last_completed_session(now)]
    if not sessions:
        return max(dates.values()), sorted(dates)
    resolved = max(sessions)
    offenders = sorted(s for s, d in dates.items() if d != resolved)
    return resolved, offenders


def duplicate_final_bar(bars: dict, min_symbols: int = 3) -> tuple:
    """(is_duplicate, detail) — is the last bar a forward-filled copy?

    A partial 'today' row appended on a non-session carries the PREVIOUS
    close, so close[-1] == close[-2] on every symbol at once. One symbol
    printing an unchanged close is ordinary; all of them is a duplicated row.
    """
    checked = dupes = 0
    for sym, b in (bars or {}).items():
        c = (b or {}).get("closes") or []
        if len(c) < 2:
            continue
        checked += 1
        if abs(c[-1] - c[-2]) < 1e-9:
            dupes += 1
    if checked < min_symbols:
        return False, "too few symbols (%d) to judge" % checked
    if dupes == checked:
        return True, ("all %d symbols have close[-1] == close[-2] -- the final "
                      "bar is a duplicate/forward-fill, not a new session"
                      % checked)
    return False, "%d/%d symbols unchanged (normal)" % (dupes, checked)
