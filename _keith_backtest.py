"""_keith_backtest.py — READ-ONLY: validate the Keith add-pattern detector
against the ps_flow_events corpus BEFORE any live wiring (verify first,
alert later — same discipline as every detector in this repo).

Per ticker: daily series from mfr_snapshots (price + fractal range +
trend), OVERLAID with Hedgeye RR (buy_trade/sell_trade/trend) on days the
name is in the RR email — RR is authoritative. Run the state machine,
then:
  1. RECALL:    how many PS adds had a fired setup within LEAD_DAYS before?
  2. PRECISION: how many fired setups were followed by a PS add?
  3. NOW:       names currently TESTED/PULLED (brewing) + setups fired in
                the last 3 sessions — today's actionable front-run list.
    python _keith_backtest.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import timedelta
import db_pg
from tools.keith_pattern import (detect, ARMED_RP, PULLBACK_RP, TEST_RP,
                                 BREAK_EPS)

LEAD_DAYS = 7
_TREND = {"trendBullish": "BULLISH", "trendBearish": "BEARISH",
          "trendNeutral": "NEUTRAL"}

with db_pg.get_conn() as c, c.cursor() as cur:
    cur.execute("""SELECT ticker, snapshot_date, price::float,
                          range_low::float, range_high::float, trend_signal
                   FROM mfr_snapshots
                   WHERE price IS NOT NULL
                   ORDER BY ticker, snapshot_date""")
    series: dict = {}
    for t, d, px, lo, hi, ts in cur.fetchall():
        series.setdefault(t, []).append([d, px, lo, hi, _TREND.get(ts)])
    cur.execute("""SELECT ticker, signal_date, trend,
                          buy_trade::float, sell_trade::float
                   FROM hedgeye_risk_ranges""")
    rr = {(t, d): (tr, bt, st_) for t, d, tr, bt, st_ in cur.fetchall()}
    cur.execute("""SELECT ticker, event_date FROM ps_flow_events
                   WHERE event = 'add' ORDER BY event_date""")
    adds = cur.fetchall()
    # operator insight 7/12: SS shows the same pattern mid-breakout —
    # SS adds are a SECOND corpus (longer lead), SS drops the invalidation
    cur.execute("""SELECT ticker, event_date, event FROM ss_flow_events
                   ORDER BY event_date""")
    ss_events = cur.fetchall()
    cur.execute("SELECT ticker FROM ss_roster_history WHERE removed_on IS NULL")
    ss_now = {r_[0] for r_ in cur.fetchall()}

# RR overlay (authoritative where present)
for t, rows in series.items():
    for row in rows:
        hit = rr.get((t, row[0]))
        if hit:
            tr, bt, st_ = hit
            if tr:
                row[4] = tr.strip().upper()
            if bt is not None and st_ is not None and st_ > bt:
                row[2], row[3] = bt, st_

add_by_t: dict = {}
for t, ed in adds:
    add_by_t.setdefault(t, []).append(ed)
ss_add_by_t: dict = {}
for t, ed, ev in ss_events:
    if ev == "add":
        ss_add_by_t.setdefault(t, []).append(ed)
SS_LEAD = 14                      # setup fires at support; SS add mid-breakout


def run_mode(label, loose):
    all_setups, brewing, last_dates = {}, [], []
    for t, rows in series.items():
        setups, stage = detect([tuple(r) for r in rows],
                               entry_on_standing=loose)
        if setups:
            all_setups[t] = setups
        if rows:
            last_dates.append(rows[-1][0])
        if stage in ("TESTED", "PULLED"):
            brewing.append((t, stage))
    latest = max(last_dates) if last_dates else None
    recent = [(t, s) for t, ss in all_setups.items() for s in ss
              if latest and (latest - s["date"]).days <= 3]

    n_setups = sum(len(v) for v in all_setups.values())
    print(f"\n─── {label} ───")
    print(f"setups fired: {n_setups} across {len(all_setups)} names")
    hits, covered, missed = 0, 0, []
    for t, ed in adds:
        rows = series.get(t)
        if not rows or rows[0][0] > ed - timedelta(days=LEAD_DAYS):
            continue
        covered += 1
        ok = any(0 <= (ed - s["date"]).days <= LEAD_DAYS
                 for s in all_setups.get(t, []))
        hits += ok
        if not ok:
            missed.append(f"{t}@{ed}")
    print(f"RECALL: {hits}/{covered} evaluable PS adds had a setup within "
          f"{LEAD_DAYS}d before")
    sig_hits = sum(1 for t, ss in all_setups.items() for s in ss
                   if any(0 <= (ed - s["date"]).days <= LEAD_DAYS
                          for ed in add_by_t.get(t, [])))
    print(f"PRECISION: {sig_hits}/{n_setups} setups followed by a PS add "
          f"within {LEAD_DAYS}d")

    # second corpus: SS adds (mid-breakout — longer lead window)
    ss_hits, ss_covered, ss_sig = 0, 0, 0
    for t, ed, ev in ss_events:
        if ev != "add":
            continue
        rows = series.get(t)
        if not rows or rows[0][0] > ed - timedelta(days=SS_LEAD):
            continue
        ss_covered += 1
        ss_hits += any(0 <= (ed - s["date"]).days <= SS_LEAD
                       for s in all_setups.get(t, []))
    ss_sig = sum(1 for t, ss in all_setups.items() for s in ss
                 if any(0 <= (ed - s["date"]).days <= SS_LEAD
                        for ed in ss_add_by_t.get(t, [])))
    print(f"SS-ADD RECALL: {ss_hits}/{ss_covered} evaluable SS adds had a "
          f"setup within {SS_LEAD}d before · setups->SS-add: "
          f"{ss_sig}/{n_setups}")

    if missed and len(missed) <= 15:
        print("adds w/o setup: " + " ".join(missed))

    def _tag(t):
        return f"{t}·SS" if t in ss_now else t
    print(f"NOW (as of {latest}): brewing "
          + (" ".join(f"{_tag(t)}({st_})" for t, st_ in sorted(brewing)[:20])
             or "none")
          + (f" +{len(brewing) - 20} more" if len(brewing) > 20 else ""))
    print("  fired last 3 sessions: "
          + (" ".join(sorted(f"{_tag(t)}@{s['date']}" for t, s in recent))
             or "none") + "   [·SS = on Signal Strength now]")


print(f"params: armed>={ARMED_RP:g} pullback<={PULLBACK_RP:g} "
      f"test<={TEST_RP:g} break_eps={BREAK_EPS:g} lead={LEAD_DAYS}d")
print(f"history: {len(series)} tickers · PS adds in corpus: {len(adds)} "
      f"(evaluable subset shown per mode)")
run_mode("STRICT (entry = trend transition — needs deep history)", False)
run_mode("LOOSE (standing BULLISH counts as entered — shallow-history mode)",
         True)
