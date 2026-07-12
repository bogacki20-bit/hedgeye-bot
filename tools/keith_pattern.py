"""
keith_pattern.py — the Keith add-pattern detector (sprint P3, operator
spec 7/11): bullish TREND entry -> sells off -> holds TRADE support ->
(Keith adds). Detect the first three stages across the bot inventory to
front-run Portfolio Solutions adds. BOT layer: pure Python state machine,
no LLM anywhere.

Stage machine per ticker over daily rows (date, price, range_low,
range_high, trend):

  ENTRY     trend transitions to BULLISH (transition only — a standing
            bullish trend is not an entry; first-sighting seeds silently,
            same rule as every other transition detector in this repo).
  PULLBACK  after entry, rp (close vs the CURRENT day's range) falls to
            <= pullback_rp (default 0.35) having first printed >= armed_rp
            (default 0.55) post-entry — a real retreat, not a name that
            IPO'd at its lows.
  TEST      a day with rp <= test_rp (default 0.15): close sitting ON the
            TRADE support zone. (Closes only — MFR has no intraday lows;
            an intraday flush that closes back up shows as a TEST+HOLD in
            one bar, which is exactly the operator's 'held support'.)
  HOLD      the next day closes UP from the test close with trend still
            BULLISH -> SETUP FIRES. This is the front-run signal.
  RESET     trend leaves BULLISH at any point (thesis gone), or price
            CLOSES below range_low by > break_eps (support lost, not held).

Support source: the caller builds rows with range_low = Hedgeye RR
buy_trade when the name is in that day's RR email, else the MFR fractal
range low — the same authority order the rest of the bot uses.

Backtest harness: _keith_backtest.py matches fired setups against
ps_flow_events adds (the 220-event corpus) BEFORE any live wiring —
verify first, alert later.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# defaults — operator-tunable, printed by every consumer
ARMED_RP = 0.55        # post-entry rp must reach this before a pullback counts
PULLBACK_RP = 0.35     # ...then fall to/below this
TEST_RP = 0.15         # close in the support zone
BREAK_EPS = 0.01       # close below range_low by >1% of range = support LOST


def _rp(price, lo, hi):
    if price is None or lo is None or hi is None or hi <= lo:
        return None
    return (float(price) - float(lo)) / (float(hi) - float(lo))


@dataclass
class _State:
    stage: str = "IDLE"          # IDLE -> ENTERED -> ARMED -> PULLED -> TESTED
    entry_date: object = None
    test_date: object = None
    test_price: float = None
    prev_trend: str = None
    seen: bool = False           # first sighting seeds silently
    log: list = field(default_factory=list)


def detect(rows, armed_rp=ARMED_RP, pullback_rp=PULLBACK_RP,
           test_rp=TEST_RP, break_eps=BREAK_EPS,
           entry_on_standing: bool = False):
    """rows: ascending [(date, price, range_low, range_high, trend)].
    Returns (setups, state): setups = [{date, entry_date, test_date,
    price, stage_path}] — one per completed ENTRY->...->HOLD sequence;
    state = final stage (for live 'what is brewing' introspection).

    entry_on_standing=True (LOOSE mode): a name that is ALREADY bullish at
    first sighting counts as entered — needed while MFR trend history is
    too shallow to contain the actual flip (2026-07 backtest: strict mode
    starved, 8 setups across 606 names). Tests the pullback-holds-support
    core of the pattern independent of the entry transition.
    Pure function, fixture-tested."""
    st = _State()
    setups = []
    for d, price, lo, hi, trend in rows:
        rp = _rp(price, lo, hi)
        bull = trend == "BULLISH"

        # RESET on thesis loss or support break
        if st.stage != "IDLE":
            if not bull:
                st.log.append((d, f"reset: trend {trend or 'none'}"))
                st = _State(prev_trend=trend, seen=True, log=st.log)
                continue
            if (rp is not None and lo is not None and hi is not None
                    and hi > lo
                    and float(price) < float(lo) - break_eps * (float(hi) - float(lo))):
                st.log.append((d, "reset: closed below support (not held)"))
                st = _State(prev_trend=trend, seen=True, log=st.log)
                continue

        # ENTRY — transition into BULLISH from a KNOWN non-bullish state
        # (loose mode also accepts a standing bull at any IDLE point)
        if st.stage == "IDLE":
            if bull and ((st.seen and st.prev_trend not in (None, "BULLISH"))
                         or entry_on_standing):
                st.stage, st.entry_date = "ENTERED", d
                st.log.append((d, "entry: trend -> BULLISH"
                               if st.seen else "entry: standing BULLISH (loose)"))
        elif rp is not None:
            if st.stage == "ENTERED" and rp >= armed_rp:
                st.stage = "ARMED"
                st.log.append((d, f"armed: rp {rp:.2f} >= {armed_rp:g}"))
            elif st.stage == "ARMED" and rp <= pullback_rp:
                st.stage = "PULLED"
                st.log.append((d, f"pullback: rp {rp:.2f} <= {pullback_rp:g}"))
            if st.stage == "PULLED" and rp <= test_rp:
                st.stage, st.test_date = "TESTED", d
                st.test_price = float(price)
                st.log.append((d, f"test: rp {rp:.2f} on support"))
            elif st.stage == "TESTED":
                if float(price) > st.test_price:
                    setups.append({"date": d, "entry_date": st.entry_date,
                                   "test_date": st.test_date,
                                   "price": float(price),
                                   "stage_path": list(st.log)
                                   + [(d, "HOLD: closed up off support — SETUP")]})
                    # re-arm: stay in the name; a later deeper test can fire again
                    st.stage = "ARMED"
                    st.log = []
                elif rp > test_rp:
                    st.stage = "PULLED"      # drifted off support without a hold

        st.prev_trend, st.seen = trend, True
    return setups, st.stage


def fmt_setup(ticker, s) -> str:
    return (f"⚡KEITH-SETUP {ticker}: held TRADE support {s['test_date']} "
            f"and closed up {s['date']} (bullish TREND since "
            f"{s['entry_date']}) — the add-pattern precondition. "
            f"Your rules decide.")


# ═══════════════════ DB assembly + command + weekly run ═════════════════════
# UNVALIDATED (2026-07-12 backtest): the evaluable PS-add sample was Quad-4
# macro-ETF rotation — not this pattern's use case — and SS history is a
# week old. So: NO automatic per-setup alerts. On-demand KEITH command +
# ONE hardwired Friday-EOD progress report (operator spec) while the
# corpora deepen. Validation counters ride along every week.

import logging as _logging
_log = _logging.getLogger("keith_pattern")

SENTINEL = "KEITH"
_WEEKLY_KEY = "keith_weekly_lastrun"
_TREND_MAP = {"trendBullish": "BULLISH", "trendBearish": "BEARISH",
              "trendNeutral": "NEUTRAL"}


def build_series(cur) -> dict:
    """{ticker: [(date, price, lo, hi, trend), ...]} — MFR daily rows with
    the Hedgeye RR overlay (trend + buy/sell_trade authoritative where the
    name is in that day's RR email). Shared by command/weekly/backtest."""
    cur.execute("""SELECT ticker, snapshot_date, price::float,
                          range_low::float, range_high::float, trend_signal
                   FROM mfr_snapshots WHERE price IS NOT NULL
                   ORDER BY ticker, snapshot_date""")
    series: dict = {}
    for t, d, px, lo, hi, ts in cur.fetchall():
        series.setdefault(t, []).append([d, px, lo, hi, _TREND_MAP.get(ts)])
    cur.execute("""SELECT ticker, signal_date, trend,
                          buy_trade::float, sell_trade::float
                   FROM hedgeye_risk_ranges""")
    rr = {(t, d): v for t, d, *v in cur.fetchall()}
    for t, rows in series.items():
        for row in rows:
            hit = rr.get((t, row[0]))
            if hit:
                tr, bt, st_ = hit
                if tr:
                    row[4] = tr.strip().upper()
                if bt is not None and st_ is not None and st_ > bt:
                    row[2], row[3] = bt, st_
    return {t: [tuple(r) for r in rows] for t, rows in series.items()}


def _ss_now(cur) -> set:
    try:
        cur.execute("SELECT ticker FROM ss_roster_history "
                    "WHERE removed_on IS NULL")
        return {r[0] for r in cur.fetchall()}
    except Exception as e:
        _log.warning("keith: SS roster unavailable: %s", e)
        return set()


SS_DROP_DAYS = 14      # a recent SS drop = invalidation tell (operator 7/12)


def _ss_recent_drops(cur, days: int = SS_DROP_DAYS) -> dict:
    """{ticker: last_drop_date} for SS drops in the last N days — Keith
    pulling a name is the pattern-thesis invalidation tell."""
    try:
        cur.execute("""SELECT ticker, max(event_date) FROM ss_flow_events
                       WHERE event = 'drop'
                         AND event_date >= CURRENT_DATE - %s
                       GROUP BY ticker""", (days,))
        return dict(cur.fetchall())
    except Exception as e:
        _log.warning("keith: ss drops unavailable: %s", e)
        return {}


def ss_tag(t: str, ss_now: set, drops: dict) -> str:
    """Render-tag (pure, tested): ·SS on roster · ✗SSdrop@date recently
    dropped (invalidation — FLAGGED, not hidden, per doctrine)."""
    if t in drops:
        return f"{t}✗SSdrop@{drops[t]}"
    if t in ss_now:
        return f"{t}·SS"
    return t


def _scan(series, loose: bool):
    """(fired {t: setups}, brewing [(t, stage)], latest_date)."""
    fired, brewing, last = {}, [], []
    for t, rows in series.items():
        setups, stage = detect(rows, entry_on_standing=loose)
        if setups:
            fired[t] = setups
        if stage in ("TESTED", "PULLED"):
            brewing.append((t, stage))
        if rows:
            last.append(rows[-1][0])
    return fired, brewing, (max(last) if last else None)


def snapshot(loose: bool = True, recent_days: int = 3) -> str:
    """On-demand KEITH reply: setups fired in the last N sessions +
    brewing names, ·SS tagged, loud UNVALIDATED label. Read-only."""
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        series = build_series(cur)
        ss = _ss_now(cur)
        drops = _ss_recent_drops(cur)
    fired, brewing, latest = _scan(series, loose)

    recent = sorted(f"{ss_tag(t, ss, drops)}@{s['date']}"
                    for t, ss_ in fired.items() for s in ss_
                    if latest and (latest - s["date"]).days <= recent_days)
    brew = sorted(f"{ss_tag(t, ss, drops)}({st_[0]})"
                  for t, st_ in brewing)                       # (T)/(P)
    mode = "loose" if loose else "strict"
    lines = [f"⚡KEITH add-pattern [{mode} · UNVALIDATED — paper signal, "
             f"corpora too young] as of {latest}",
             "fired last 3 sessions: " + (" ".join(recent) or "none"),
             f"brewing ({len(brew)}; T=on support, P=pulled back): "
             + (" ".join(brew[:30]) or "none")
             + (f" +{len(brew) - 30} more" if len(brew) > 30 else ""),
             "pattern: bullish TREND · pullback · HELD trade support · "
             "closed up. ·SS = on Signal Strength now · ✗SSdrop = dropped "
             "from SS ≤14d (invalidation tell). Your rules decide."]
    return "\n".join(lines)


def weekly_report() -> str:
    """Friday-EOD progress report (operator spec 7/12): this week's fired
    setups + brewing + the validation counters vs PS/SS adds — so the
    paper-trade record builds itself while the corpora deepen."""
    from datetime import timedelta
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        series = build_series(cur)
        ss = _ss_now(cur)
        drops = _ss_recent_drops(cur)
        cur.execute("SELECT ticker, event_date FROM ps_flow_events "
                    "WHERE event='add'")
        ps_adds = cur.fetchall()
        cur.execute("SELECT ticker, event_date FROM ss_flow_events "
                    "WHERE event='add'")
        ss_adds = cur.fetchall()
    fired, brewing, latest = _scan(series, loose=True)
    week = [(t, s) for t, ss_ in fired.items() for s in ss_
            if latest and (latest - s["date"]).days <= 7]

    def hits(adds, lead):
        h = cov = 0
        for t, ed in adds:
            rows = series.get(t)
            if not rows or rows[0][0] > ed - timedelta(days=lead):
                continue
            cov += 1
            h += any(0 <= (ed - s["date"]).days <= lead
                     for s in fired.get(t, []))
        return h, cov
    ps_h, ps_c = hits(ps_adds, 7)
    ss_h, ss_c = hits(ss_adds, 14)

    lines = [f"⚡KEITH WEEKLY [{latest}] — paper-trade progress "
             f"(UNVALIDATED until recall proves out)",
             f"fired this week ({len(week)}): "
             + (" ".join(sorted(f"{ss_tag(t, ss, drops)}@{s['date']}"
                                for t, s in week)) or "none"),
             f"validation: PS-add recall {ps_h}/{ps_c} (7d lead) · "
             f"SS-add recall {ss_h}/{ss_c} (14d lead) — evaluable only; "
             f"corpora deepen weekly",
             f"brewing into next week: {len(brewing)} names (KEITH for the "
             f"list)"]
    return "\n".join(lines)


def run_weekly(force: bool = False) -> str:
    """Hardwired Friday-EOD run (called from main.py's nightly job when the
    ET weekday is Friday). Once per date via bot_state; sends Telegram +
    stores report_rows kind='keith_weekly'."""
    import db_pg
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        today = datetime.utcnow().date().isoformat()
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key=%s", (_WEEKLY_KEY,))
        r = cur.fetchone()
        if not force and r and r[0] == today:
            return "skip:ran-today"
        cur.execute("INSERT INTO bot_state (key,value,updated_at) "
                    "VALUES (%s,%s,NOW()) ON CONFLICT (key) DO UPDATE "
                    "SET value=EXCLUDED.value, updated_at=NOW()",
                    (_WEEKLY_KEY, today))
        c.commit()
    body = weekly_report()
    try:
        from tools.report import store_report
        store_report(body, "keith_weekly")
    except Exception as e:
        _log.warning("keith weekly store failed: %s", e)
    try:
        from notifier import send_telegram
        send_telegram("KEITH weekly", body)
    except Exception as e:
        _log.warning("keith weekly telegram failed: %s", e)
        return f"error:{e}"
    return "sent"


def handle_keith_command(text):
    """Telegram: KEITH (loose snapshot) · KEITH STRICT · KEITH WEEKLY
    (force the Friday report now). None to decline."""
    if not text:
        return None
    up = text.strip().upper()
    if up == SENTINEL:
        return snapshot(loose=True)
    if up == f"{SENTINEL} STRICT":
        return snapshot(loose=False)
    if up == f"{SENTINEL} WEEKLY":
        return weekly_report()
    return None
