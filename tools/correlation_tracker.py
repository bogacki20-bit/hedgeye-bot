"""Correlation tracking + regime-shift detector.

NOTE: Task 12's detailed spec was truncated in the build prompt (slide
images overwrote the text after "migrations/011_"). This is
reconstructed from the title and the codebase conventions â€” review
before relying on the thresholds.

For a fixed set of classic macro pairs it computes the Pearson
correlation of daily returns over 20- and 60-day windows (yfinance
closes), persists each to correlation_snapshots, and compares the 20d
correlation to the most recent prior snapshot. A REGIME SHIFT fires
(Telegram + alerts_fired boundary='regime_shift' + regime_shifts row)
when the correlation flips sign or moves by more than the magnitude
threshold.

CLI:
    python -m tools.correlation_tracker            # compute + persist + alert
    python -m tools.correlation_tracker --dry-run  # compute + print only
"""

from __future__ import annotations

import sys
import logging
import datetime as dt

log = logging.getLogger(__name__)

# Classic macro relationships Hedgeye watches for regime reads.
_PAIRS = [
    ("SPY", "TLT"),   # equities vs long bonds
    ("SPY", "UUP"),   # equities vs US dollar
    ("SPY", "GLD"),   # equities vs gold
    ("SPY", "USO"),   # equities vs crude
    ("TLT", "GLD"),   # bonds vs gold
    ("SPY", "GBTC"),  # equities vs bitcoin
    ("XLK", "XLU"),   # risk-on vs risk-off rotation
]
_WINDOWS = (20, 60)
_MAGNITUDE_THRESHOLD = 0.40   # |Î”corr| over the 20d window to flag


def _returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        p0, p1 = closes[i - 1], closes[i]
        if p0:
            out.append(p1 / p0 - 1.0)
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 5:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx ** 0.5 * vy ** 0.5)


def _history(ticker: str, days: int) -> list[float]:
    """Daily closes for the last `days` sessions via yfinance."""
    try:
        import yfinance as yf
        period = f"{max(days + 10, 90)}d"
        df = yf.Ticker(ticker).history(period=period, interval="1d")
        if df is None or df.empty or "Close" not in df:
            return []
        return [float(v) for v in df["Close"].tolist() if v == v]
    except Exception as e:
        log.warning("history fetch failed for %s: %s", ticker, e)
        return []


def _prev_corr(cur, a: str, b: str, w: int, today: dt.date):
    try:
        cur.execute(
            """
            SELECT correlation FROM correlation_snapshots
             WHERE ticker_a=%s AND ticker_b=%s AND window_days=%s
               AND snapshot_date < %s
             ORDER BY snapshot_date DESC LIMIT 1
            """,
            (a, b, w, today),
        )
        r = cur.fetchone()
        return float(r[0]) if r and r[0] is not None else None
    except Exception as e:
        log.debug("prev corr lookup failed %s-%s/%s: %s", a, b, w, e)
        return None


def run(dry_run: bool = False) -> int:
    try:
        import db_pg
    except Exception as e:
        print(f"ERROR: db_pg unavailable: {e}", file=sys.stderr)
        return 2

    today = dt.datetime.now(dt.timezone.utc).date()   # UTC: session-dated row
    price_cache: dict[str, list[float]] = {}

    def closes(t: str) -> list[float]:
        if t not in price_cache:
            price_cache[t] = _history(t, max(_WINDOWS))
        return price_cache[t]

    computed: list[dict] = []
    for a, b in _PAIRS:
        ca, cb = closes(a), closes(b)
        ra, rb = _returns(ca), _returns(cb)
        for w in _WINDOWS:
            corr = _pearson(ra[-w:], rb[-w:])
            if corr is None:
                continue
            computed.append({"a": a, "b": b, "w": w,
                              "corr": round(corr, 5),
                              "n": min(len(ra[-w:]), len(rb[-w:]))})

    print(f"Computed {len(computed)} correlation point(s) for {today}.")
    for c in computed:
        print(f"  {c['a']}-{c['b']} {c['w']}d: r={c['corr']:+.3f} (n={c['n']})")

    if dry_run:
        print("[dry-run] no persistence, no alerts.")
        return 0

    shifts: list[str] = []
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for c in computed:
                if c["w"] == 20:
                    prev = _prev_corr(cur, c["a"], c["b"], 20, today)
                else:
                    prev = None
                try:
                    cur.execute(
                        """
                        INSERT INTO correlation_snapshots
                          (snapshot_date, ticker_a, ticker_b, window_days,
                           correlation, n_obs)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (snapshot_date, ticker_a, ticker_b,
                                     window_days)
                          DO UPDATE SET correlation=EXCLUDED.correlation,
                                        n_obs=EXCLUDED.n_obs,
                                        computed_at=NOW()
                        """,
                        (today, c["a"], c["b"], c["w"], c["corr"], c["n"]),
                    )
                except Exception as e:
                    log.warning("corr insert failed %s-%s/%s: %s",
                                c["a"], c["b"], c["w"], e)
                    continue

                if c["w"] != 20 or prev is None:
                    continue
                cur_c = c["corr"]
                shift_type = None
                if (prev < 0 < cur_c) or (cur_c < 0 < prev):
                    shift_type = "sign_flip"
                elif abs(cur_c - prev) > _MAGNITUDE_THRESHOLD:
                    shift_type = "magnitude"
                if shift_type:
                    pair = f"{c['a']}-{c['b']}"
                    note = (f"{pair} 20d correlation {prev:+.2f} -> "
                            f"{cur_c:+.2f} ({shift_type})")
                    shifts.append(note)
                    try:
                        cur.execute(
                            """
                            INSERT INTO regime_shifts
                              (pair, window_days, prev_corr, curr_corr,
                               shift_type, note)
                            VALUES (%s,%s,%s,%s,%s,%s)
                            """,
                            (pair, 20, prev, cur_c, shift_type, note),
                        )
                    except Exception as e:
                        log.warning("regime_shift insert failed: %s", e)
                    try:
                        import db_pg as _db
                        _db.record_alert(
                            ticker=pair, boundary="regime_shift",
                            signal_date=today, recommendation_text=note,
                            suggested_action="REGIME_SHIFT")
                    except Exception as e:
                        log.warning("regime_shift alert failed: %s", e)
            conn.commit()
    except Exception as e:
        print(f"ERROR: persistence failed: {e}", file=sys.stderr)
        return 3

    if shifts:
        msg = "ðŸ“Š CORRELATION REGIME SHIFT\n" + "\n".join(f"- {s}" for s in shifts)
        print(msg)
        try:
            from notifier import send_telegram
            send_telegram("CORRELATION REGIME SHIFT", msg, priority=2)
        except Exception as e:
            log.warning("telegram regime-shift push failed: %s", e)
    else:
        print("No correlation regime shifts vs prior snapshot.")
    return 0


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.correlation_tracker")
    p.add_argument("--dry-run", action="store_true",
                   help="compute + print only; no persistence, no alerts")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(_cli())
