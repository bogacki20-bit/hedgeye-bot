"""Rule scanner: the proactive alerts with no LLM calls (Kris, 11/6/2026).

The old proactive_scanner asked Haiku about every ticker each cycle (~$25-30/day). This
applies docs/playbook/RULE_CARD.md directly in Python, over Hedgeye's inventory plus the
book and data/stalk/desk_watch.csv, and sends one Telegram message per cycle with only new
alerts (each ticker+action fires once a day; exits fire again at the 3:45 confirm).

  Book longs   below Trend -> OUT · lower high + below Trade -> TO MIN · rp > 1 -> TRIM
  Book shorts  above Trend -> COVER ALL · above Trade -> COVER HALF ·
               +3% / rp <= .25 / range low -> COVER 1/3 (1-share = all)
  New / add long   SS or PM long side (bench included since 11/21, tagged "PM bench only"), price above Trade and Trend, 0 <= rp <= .35, red day,
                   volume falling and under the 20-day average, no earnings within 2 days
  New short        SS or PM short side (or a watch short), below Trade and Trend, 3+ green
                   days, rp >= .65, green today, volume falling, no earnings within 2 days

Our own lines (Vola Trade, 89-day EMA) get the 2% buffer; Keith's numbers don't.

    py -m tools.rule_scanner --dry-run      # print, don't send or record
    py -m tools.rule_scanner                # send new alerts to Telegram
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WATCH = os.path.join(ROOT, "data", "stalk", "desk_watch.csv")
STATE = os.path.join(ROOT, "data", "rule_scanner_state.json")
CASH = {"SPAXX", "FDRXX", "BUXX", "CORE"}
TV_KEY = {"USD": "USD_SEMI"}
LONG_PM = ("active_long", "top_idea_long")
SHORT_PM = ("active_short", "top_idea_short")
LONG_BENCH = ("long_bench",)       # Position Monitor bench (Kris 11/21: read the whole Position Monitor).
SHORT_BENCH = ("short_bench",)     # The weakest source: it only sets a side when nothing else lists the name.
BUF = 0.02
STOP_LONG = 0.05        # card: -5% from cost = close
STOP_SHORT = 0.04       # card: 4% against = cover half
WARN = 0.03             # early warning only, not a card rule (Kris 11/21)
INVERSE_1X = {"NVDD", "METD", "ORCS", "PSQ", "SH", "RWM", "MSFD", "TSLQ", "SKYD"}


def _vol_frac(now: dt.datetime) -> float:
    """Share of a full session's volume traded by now (same curve as desk_rot)."""
    if now.weekday() >= 5 or now.hour * 60 + now.minute < 570:
        return 1.0
    m = max(1, now.hour * 60 + now.minute - 570)
    return min(1.0, 0.25 * min(1, m / 60) + (0.75 * (m - 60) / 330 if m > 60 else 0))


def _fv(x: float) -> str:
    return f"{x / 1e6:.1f}M" if x >= 1e5 else f"{x / 1e3:.0f}k"


def _keith(src: str) -> bool:
    return src.startswith("Keith")


def load_universe():
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""select account_name, sum(market_value) from book_positions
                       where snapshot_date=(select max(snapshot_date) from book_positions) group by 1""")
        acct = {a: float(v) for a, v in cur.fetchall()}
        cur.execute("""select symbol, account_name, sum(quantity), sum(cost_basis) from book_positions
                       where snapshot_date=(select max(snapshot_date) from book_positions) and quantity<>0
                       group by 1,2""")
        book = [(s, a, float(q), float(cb or 0)) for s, a, q, cb in cur.fetchall()
                if s and s.isalpha() and len(s) <= 5 and s not in CASH]
        cur.execute("""select distinct on (ticker, side) ticker, side, removed_on from ss_roster_history
                       order by ticker, side, added_on desc""")
        ss_rows = cur.fetchall()
        ss = {(t, s) for t, s, r in ss_rows if r is None}
        ss_removed = {(t, s) for t, s, r in ss_rows if r is not None}
        cur.execute("select ticker, hedgeye_bucket_0629 from ticker_tags where hedgeye_bucket_0629 is not null")
        pm = dict(cur.fetchall())
        cur.execute("""select distinct on (ticker) ticker, bias from hedgeye_etf_pro_ranges
                       where week_of = (select max(week_of) from hedgeye_etf_pro_ranges) order by ticker, week_of desc""")
        etf = dict(cur.fetchall())
    watch = {}
    if os.path.exists(WATCH):
        with open(WATCH, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for t in r["ticker"].split():
                    watch[t] = r["side"]
    for t, side in extra_inventory().items():      # desk_watch.csv wins on a conflict
        if (t, side) in ss_removed:                # card: removed from Signal Strength = off the stalk
            continue
        watch.setdefault(t, side)
    return acct, book, ss, pm, etf, watch


# Risk Range rows that are indexes, yields, FX or futures, not tickers you can trade.
# "USD" there is the dollar index; in the book USD is ProShares Ultra Semis, so it is skipped too.
RR_SKIP = {"SPX", "COMPQ", "RUT", "VIX", "USD", "WTIC", "BRENT", "GOLD", "SILVER", "COPPER", "NATGAS",
           "BITCOIN", "NIKK", "DAX", "SSEC", "KOSPI", "UST2Y", "UST10Y", "UST30Y"}
LEDGER = os.path.join(ROOT, "data", "stalk", "ledger.csv")
CRYPTO_PRODUCTS = ("crypto_quant", "digital_assets_position_monitor")


def _tradable(t: str) -> bool:
    return bool(t) and t.isalpha() and t.isupper() and len(t) <= 5 and t not in RR_SKIP and t not in CASH


def extra_inventory(today: dt.date | None = None) -> dict:
    """The rest of Hedgeye's inventory (Kris 11/20: "the whole universe"): ticker -> 'long' | 'short'.

    Later sources fill in only names the earlier ones don't have:
    1. the latest daily Risk Range table (BULLISH = long, BEARISH = short);
    2. Crypto Quant and the Digital Assets Position Monitor calls of the last 14 days;
    3. the sector-call ledger (data/stalk/ledger.csv), last 30 days.
    Every source is optional: a failure in one never stops the scan.
    """
    today = today or dt.date.today()
    out: dict = {}
    try:
        import db_pg
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("""select ticker, trend from hedgeye_risk_ranges
                           where signal_date = (select max(signal_date) from hedgeye_risk_ranges)""")
            for t, trend in cur.fetchall():
                side = {"BULLISH": "long", "BEARISH": "short"}.get((trend or "").upper())
                if side and _tradable(t):
                    out.setdefault(t, side)
            cur.execute("""select distinct on (name) name, direction from research_calls
                           where product = any(%s) and signal_date >= %s
                           order by name, signal_date desc, id desc""",
                        (list(CRYPTO_PRODUCTS), today - dt.timedelta(days=14)))
            for t, direction in cur.fetchall():
                side = {"long": "long", "bullish": "long", "short": "short", "bearish": "short"}.get(direction)
                if side and _tradable(t):
                    out.setdefault(t, side)
    except Exception as e:                                           # noqa: BLE001
        print(f"extra_inventory: database sources skipped ({type(e).__name__})", file=sys.stderr)
    try:
        latest: dict = {}
        with open(LEDGER, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    d = dt.date.fromisoformat(r["date"])
                except (KeyError, ValueError):
                    continue
                if (today - d).days > 30:
                    continue
                call = (r.get("call") or "").lower()
                lg, sh = "long" in call, "short" in call
                side = "long" if lg and not sh and "bearish" not in call else "short" if sh and not lg else None
                t = (r.get("ticker") or "").strip()
                if side and _tradable(t) and d >= latest.get(t, (dt.date.min, None))[0]:
                    latest[t] = (d, side)
        for t, (_, side) in latest.items():
            out.setdefault(t, side)
    except OSError:
        pass
    return out


def sides(t, ss, pm, etf, watch):
    """Which side Hedgeye's inventory has this ticker on: 'long', 'short', 'both' or None."""
    lg = (t, "long") in ss or pm.get(t) in LONG_PM or etf.get(t) == "long" or watch.get(t) == "long"
    sh = (t, "short") in ss or pm.get(t) in SHORT_PM or etf.get(t) == "short" or watch.get(t) == "short"
    if not lg and not sh:
        return "long" if pm.get(t) in LONG_BENCH else "short" if pm.get(t) in SHORT_BENCH else None
    return "both" if lg and sh else "long" if lg else "short" if sh else None


def bench_only(t, ss, pm, etf, watch) -> bool:
    """True when the Position Monitor bench is the only place Hedgeye's inventory lists this ticker."""
    return (pm.get(t) in LONG_BENCH + SHORT_BENCH and t not in etf and t not in watch
            and (t, "long") not in ss and (t, "short") not in ss)


def earnings_soon(t: str, days: int = 2) -> bool:
    import yfinance as yf
    try:
        cal = yf.Ticker(t).calendar or {}
        ds = cal.get("Earnings Date") or []
        today = dt.date.today()
        return any(0 <= (d - today).days <= days for d in ds if isinstance(d, dt.date))
    except Exception:
        return False


def is_etf(t: str) -> bool:
    import yfinance as yf
    try:
        return (yf.Ticker(t).info or {}).get("quoteType") == "ETF"
    except Exception:
        return False


def scan(now: dt.datetime | None = None) -> list[dict]:
    import db_pg
    import yfinance as yf
    from tools.level_lines import lookup
    from tools.range_structure import structure

    now = now or dt.datetime.now()
    acct, book, ss, pm, etf, watch = load_universe()
    held = {}
    for s, a, q, cb in book:
        held.setdefault(s, []).append((a, q, cb))
    inv = ({t for t, _ in ss} | {t for t, b in pm.items() if b in LONG_PM + SHORT_PM + LONG_BENCH + SHORT_BENCH}
           | set(etf) | set(watch))
    T = sorted(t for t in inv | set(held) if t.isalpha() and len(t) <= 5)     # US listings; skips EL.PA, ETHUSD
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""select distinct on (ticker) ticker, range_high, range_low from tv_levels
                       where ticker=any(%s) and range_high is not null and bar_date >= current_date - 7
                       order by ticker, bar_date desc""", ([TV_KEY.get(t, t) for t in T],))
        rng = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}
        # No TradingView range: use MFR's own range from its nightly snapshot (Kris 11/21). It is the same
        # range TradingView draws, so rp reads the same. Names with a TV_KEY alias are left to TradingView
        # ("USD" is 2x semis in the book and something else elsewhere).
        need = [t for t in T if t not in rng and t not in TV_KEY]
        cur.execute("""select distinct on (ticker) ticker, range_high, range_low from mfr_snapshots
                       where ticker=any(%s) and range_high is not null and range_low is not null
                         and range_high > range_low and snapshot_date >= current_date - 5
                       order by ticker, snapshot_date desc, fetched_at desc""", (need,))
        mfr_rng = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}
        rng.update(mfr_rng)
    # A bench-only name with no fresh range can't produce a line (rp is unknown), and each extra name costs
    # about a second of lookups, so leave those out. They come in once the capture desk has a range for them.
    T = [t for t in T if t in held or TV_KEY.get(t, t) in rng or not bench_only(t, ss, pm, etf, watch)]
    with db_pg.get_conn() as c, c.cursor() as cur:
        # MFR implied vs realized vol (Kris 11/20): context on every line, never a gate
        cur.execute("""select distinct on (ticker) ticker, iv, rv from mfr_snapshots
                       where ticker=any(%s) and iv is not null and rv is not null and rv > 0
                         and snapshot_date >= current_date - 5
                       order by ticker, snapshot_date desc, fetched_at desc""", (T,))
        ivrv = {r[0]: (float(r[1]), float(r[2])) for r in cur.fetchall()}
    lv = lookup(T)
    rs = structure(T)
    d = yf.download(T, period="3mo", interval="1d", progress=False, threads=True, group_by="ticker")
    fr = _vol_frac(now)
    closing = now.hour * 60 + now.minute >= 15 * 60 + 40
    out = []
    for t in T:
        try:
            h = d[t].dropna()
        except Exception:
            continue
        if len(h) < 25:
            continue
        cl = h.Close.values
        vo = h.Volume.values.astype(float)
        p = float(cl[-1])
        day = (cl[-1] / cl[-2] - 1) * 100
        a20 = vo[-21:-1].mean()
        if h.index[-1].date() == now.date():    # before the first print, the last bar is still yesterday's
            vo[-1] /= fr
        tr, tr_src = lv[t]["trade"][0], lv[t]["trade"][1]
        tn_l, tn_l_src = lv[t]["trend_long"][0], lv[t]["trend_long"][1]     # yellow cloud bottom
        tn_s, tn_s_src = lv[t]["trend_short"][0], lv[t]["trend_short"][1]   # yellow cloud top
        rh, rl = rng.get(TV_KEY.get(t, t), (None, None))
        rp = (p - rl) / (rh - rl) if rh and rl and rh > rl else None
        tb = 0 if _keith(tr_src) else BUF
        nb_l = BUF if tn_l_src.endswith("EMA") else 0       # the buffer is only for the EMA fallback
        nb_s = BUF if tn_s_src.endswith("EMA") else 0
        lower_high = rs.get(t, {}).get("lower_high", False)
        streak = 0
        for k in range(len(cl) - 1, 0, -1):
            if (cl[k] > cl[k - 1]) == (day > 0) and cl[k] != cl[k - 1]:
                streak += 1
            else:
                break
        # decreasing volume (Kris 11/6): the pullback/bounce days average under the 20-day and today is lighter than yesterday
        n = max(streak, 2)
        vol_falling = vo[-n:].mean() < a20 and vo[-1] < vo[-2]
        base = dict(ticker=t, px=p, day=day, rp=rp, trade=tr, trend=tn_l, rl=rl, rh=rh,
                    vol3="/".join(_fv(x) for x in vo[-3:]), avg=_fv(a20), streak=streak,
                    ivrv=ivrv.get(t))
        side = sides(t, ss, pm, etf, watch)
        src = "; PM bench only" if bench_only(t, ss, pm, etf, watch) else ""
        if t in mfr_rng:
            src += "; MFR range"

        for a, q, cb in held.get(t, []):
            mv = q * p
            pct = abs(mv) / acct.get(a, 1) * 100
            tag = "" if closing else " (if it closes here)"
            # price stops off cost (Kris 11/21; card: longs -5% = close, shorts 4% against = cover half).
            # A 1x inverse ETF held long is a short: 4% under its cost = 4% against.
            avg_c = abs(cb / q) if q and cb else None
            vs = (p / avg_c - 1) if avg_c else None             # long: negative = losing
            inv = q > 0 and t in INVERSE_1X
            stopped = False
            if vs is not None and q > 0 and not inv:
                if vs <= -STOP_LONG:
                    out.append(dict(base, acct=a, action="SELL ALL", exit=True,
                                    why=f"STOP: {vs * 100:+.1f}% from cost {avg_c:.2f} (stop {avg_c * (1 - STOP_LONG):.2f}); check the print is real"))
                    stopped = True
                elif vs <= -WARN:
                    out.append(dict(base, acct=a, action="WATCH", why=f"{vs * 100:+.1f}% from cost {avg_c:.2f}; "
                                    f"stop at {avg_c * (1 - STOP_LONG):.2f}, no adds"))
            elif vs is not None and (q < 0 or inv):
                against = vs if q < 0 else -vs                  # short: price up = against; inverse ETF: its price down
                lvl = avg_c * (1 + STOP_SHORT) if q < 0 else avg_c * (1 - STOP_SHORT)
                if against >= STOP_SHORT:
                    half = f"{max(1, math.ceil(abs(q) / 2)):g}" if q < 0 else f"{abs(q) / 2:.3f} sh"
                    out.append(dict(base, acct=a, action=f"{'COVER' if q < 0 else 'SELL'} {half}", exit=True,
                                    why=f"STOP: {against * 100:.1f}% against (entry {avg_c:.2f}, line {lvl:.2f}) = cover half"))
                    stopped = True
                elif against >= WARN:
                    out.append(dict(base, acct=a, action="WATCH", why=f"{against * 100:.1f}% against (entry {avg_c:.2f}); "
                                    f"cover half at {lvl:.2f}, no adds"))
            if stopped:
                continue
            if q > 0:
                if tn_l and p < tn_l * (1 - nb_l):
                    out.append(dict(base, acct=a, action="SELL ALL", why=f"below Trend {tn_l:.2f}{tag}", exit=True))
                elif lower_high and tr and p < tr * (1 - tb):
                    floor = (2 if is_etf(t) else 1) / 100 * acct[a]      # class MIN; at MIN it's held to Trend
                    if mv > floor * 1.15:
                        out.append(dict(base, acct=a, action=f"SELL {(mv - floor) / p:.1f} sh TO MIN",
                                        why=f"lower high + below Trade {tr:.2f}{tag}", exit=True))
                elif rp is not None and rp > 1 and mv > (2 if is_etf(t) else 1) / 100 * acct[a] * 1.15:
                    out.append(dict(base, acct=a, action=f"TRIM ~${0.005 * acct[a]:.0f}", why="rp > 1"))
            else:
                qa = abs(q)
                avg = cb / qa if qa else p
                pnl = (avg / p - 1) * 100
                third = 1 if qa < 2 else max(1, math.floor(qa / 3))
                if tn_s and p > tn_s * (1 + nb_s):
                    out.append(dict(base, acct=a, action=f"COVER ALL {qa:g}", why=f"above Trend {tn_s:.2f}{tag}", exit=True))
                elif tr and p > tr * (1 + tb):
                    out.append(dict(base, acct=a, action=f"COVER {max(1, math.ceil(qa / 2)):g}", why=f"above Trade {tr:.2f}{tag}", exit=True))
                elif pnl >= 3 or (rp is not None and rp <= 0.25) or (rl and p <= rl):
                    n = qa if qa < 2 else third
                    out.append(dict(base, acct=a, action=f"COVER {n:g}", why=f"+{pnl:.0f}% / rp {rp if rp is None else round(rp, 2)}"))

        if side in ("long",) and rp is not None and tr and tn_l:
            if p > tn_l and p >= tr * (1 - tb) and 0 <= rp <= 0.35 and day < 0 and vol_falling:
                longs = [x for x in held.get(t, []) if x[1] > 0]
                a = longs[0][0] if longs else "Rollover IRA"
                size = 0.005 * acct[a] if longs else 0.01 * acct[a]
                out.append(dict(base, acct=a, action=f"{'ADD' if longs else 'BUY'} ~${size:.0f}",
                                why=f"rp {rp:.2f}, red, vol falling; out under {tn_l:.2f}{src}", entry=True))
        if side in ("short",) and rp is not None and tr and tn_s and not any(x[1] < 0 for x in held.get(t, [])):
            if p < tn_s and p < tr and streak >= 3 and day > 0 and rp >= 0.65 and vol_falling:
                a = "Individual"
                out.append(dict(base, acct=a, action=f"SHORT ~${0.006 * acct[a]:.0f}",
                                why=f"day {streak} bounce, rp {rp:.2f}, vol falling; stop {tr:.2f}{src}", entry=True))
    for o in out:
        if o.get("entry") and earnings_soon(o["ticker"]):
            o["skip"] = "earnings within 2 days"
    return [o for o in out if not o.get("skip")]


def _key(o, closing):
    return f"{o['acct']}|{o['ticker']}|{o['action'].split()[0]}|{'close' if o.get('exit') and closing else 'day'}"


def fresh(alerts, now, record):
    today = now.date().isoformat()
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    seen = set(st.get(today, []))
    closing = now.hour * 60 + now.minute >= 15 * 60 + 40
    new = [o for o in alerts if _key(o, closing) not in seen]
    if record and new:
        st = {today: sorted(seen | {_key(o, closing) for o in new})}
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    return new


def fmt(alerts, now):
    lines = [f"as of {now:%H:%M}"]
    for o in sorted(alerts, key=lambda o: (not o.get("exit"), o["acct"], o["ticker"])):
        rp = "-" if o["rp"] is None else f"{o['rp']:.2f}"
        iv = ""
        if o.get("ivrv"):
            i, r = o["ivrv"]
            iv = f" | IV {i * 100:.0f} vs RV {r * 100:.0f} ({(i / r - 1) * 100:+.0f}%)"
        lines.append(f"{o['acct'].split()[0]}: {o['action']} {o['ticker']} @ {o['px']:.2f} | rp {rp} | "
                     f"vol {o['vol3']} (avg {o['avg']}){iv} | {o['why']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print every alert; don't send or record")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    now = dt.datetime.now()
    if not args.dry_run:
        from parser_tms_deck import load_telegram_env
        from tools.trading_calendar import is_trading_day
        if not is_trading_day(now.date()):
            print(f"{now:%H:%M} market closed")
            return 0
        load_telegram_env()     # the laptop .env has no Telegram settings
    alerts = scan(now)
    new = alerts if args.dry_run else fresh(alerts, now, record=False)
    if not new:
        print(f"{now:%H:%M} no new alerts ({len(alerts)} standing)")
        return 0
    msg = fmt(new, now)
    print(msg)
    if args.dry_run:
        return 0
    from notifier import send_telegram
    if not send_telegram("Rule scanner", msg):
        print("Telegram send failed; alerts stay unrecorded and retry next cycle")
        return 1
    fresh(new, now, record=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
