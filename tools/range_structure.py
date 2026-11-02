"""Risk-range structure (Master The Market, ch. 4): higher/lower highs and lows of the daily range.

Compares today's MFR range (mfr_snapshots) with the range LOOKBACK sessions ago.
  HH/HL  higher high + higher low   -> best dips to buy (with bullish TRADE + TREND)
  LH/HL  lower high + higher low    -> weaker setup, size smaller
  LH/*   any lower high             -> start reducing size (longs), before the trade line breaks
A move smaller than TOL of the level counts as flat ("=").
"""
import sys; sys.path.insert(0, r'C:\Users\bogac\hedgeye-bot')
import db_pg, datetime as dt

LOOKBACK = 5      # sessions
TOL = 0.002       # 0.2%


def _cmp(now, then):
    if now > then * (1 + TOL): return "H"
    if now < then * (1 - TOL): return "L"
    return "="


def structure(tickers):
    """{ticker: {'hi': 'HH'|'LH'|'=H', 'lo': 'HL'|'LL'|'=L', 'tag': 'HH/HL', 'lower_high': bool}}"""
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""select ticker, snapshot_date, range_low, range_high from mfr_snapshots
                       where ticker = any(%s) and snapshot_date > current_date - 30
                         and range_low is not null and range_high is not null
                       order by ticker, snapshot_date desc""", (list(tickers),))
        rows = cur.fetchall()
    # weekend snapshots carry Friday's post-close range: fold them onto Friday, newest row wins
    hist = {}
    for t, d, lo, hi in rows:
        sess = d - dt.timedelta(days=max(0, d.isoweekday() - 5))
        h = hist.setdefault(t, [])
        if not h or h[-1][0] != sess:
            h.append((sess, float(lo), float(hi)))
    out = {}
    for t, h in hist.items():
        if len(h) <= LOOKBACK: continue
        _, lo0, hi0 = h[0]; _, lo1, hi1 = h[LOOKBACK]
        hs, ls = _cmp(hi0, hi1), _cmp(lo0, lo1)
        tag = f"{'HH' if hs == 'H' else 'LH' if hs == 'L' else '=H'}/{'HL' if ls == 'H' else 'LL' if ls == 'L' else '=L'}"
        out[t] = dict(tag=tag, lower_high=hs == "L", higher_high=hs == "H", higher_low=ls == "H", lower_low=ls == "L")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ts = sys.argv[1:] or ["SPY", "QQQ", "IWM"]
    for t, s in sorted(structure(ts).items()): print(f"{t:6} {s['tag']}")
