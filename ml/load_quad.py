"""ml/load_quad.py — realized Quad from FRED (Phase B(b), roadmap section 1).

GDPC1 (quarterly real GDP) + CPIAUCSL (monthly CPI) via fredgraph.csv (no
key needed); raw CSVs banked in data/reference/fred/.

Quad(quarter) = sign of D(YoY GDP growth) x sign of D(YoY CPI, quarter
average) vs the prior quarter: Q1 G+I- / Q2 G+I+ / Q3 G-I+ / Q4 G-I-.
Every month of the quarter carries the quarter's quad. known_at =
first day of the SECOND month after quarter end (conservative stand-in for
the BEA advance release + 1; we carry no ALFRED vintages — later is the
safe direction). Zero G deltas count as "down"; CPI ties |dI| <= 2bp
count as DECELERATION (Hedgeye's convention, operator rule 2026-09-08) —
printed when they occur.

    py ml/load_quad.py [--dry-run]
"""
from __future__ import annotations

import argparse
import io
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
from psycopg2.extras import execute_values  # noqa: E402

NY = ZoneInfo("America/New_York")
REF = REPO / "data" / "reference" / "fred"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"


def fetch(sid: str) -> pd.Series:
    import requests
    REF.mkdir(parents=True, exist_ok=True)
    r = requests.get(FRED.format(sid=sid), timeout=60)
    r.raise_for_status()
    (REF / f"{sid}.csv").write_text(r.text, encoding="utf-8")
    df = pd.read_csv(io.StringIO(r.text))
    dcol = df.columns[0]           # 'DATE' or 'observation_date'
    s = df.set_index(pd.to_datetime(df[dcol]))[sid]
    return pd.to_numeric(s, errors="coerce").dropna()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gdp = fetch("GDPC1")            # quarterly, dated first month of quarter
    cpi = fetch("CPIAUCSL")         # monthly
    gdp_yoy = (gdp / gdp.shift(4) - 1) * 100
    cpi_q = cpi.resample("QS").mean()
    cpi_yoy = (cpi_q / cpi_q.shift(4) - 1) * 100
    g_roc = gdp_yoy - gdp_yoy.shift(1)
    i_roc = cpi_yoy.reindex(gdp_yoy.index) - cpi_yoy.reindex(gdp_yoy.index).shift(1)

    rows = []
    flats = []
    for qstart, g in g_roc.items():
        i = i_roc.get(qstart)
        if g != g or i is None or i != i or qstart.year < 2016:
            continue
        if g == 0 or abs(i) <= 0.02:
            flats.append(str(qstart.date()))
        # tie rule (operator, 2026-09-08): |dCPI| <= 2bp counts as
        # DECELERATION, matching Hedgeye's convention (their printed
        # actuals call 1Q24 and 1Q25 - both knife-edge CPI - Quad 4)
        g_up, i_up = g > 0, i > 0.02
        quad = 1 if (g_up and not i_up) else 2 if (g_up and i_up) \
            else 3 if (not g_up and i_up) else 4
        # known_at: first day of the SECOND month after quarter end
        qend = qstart + pd.offsets.QuarterEnd(0)
        ka_date = (qend + pd.offsets.MonthBegin(2)).date()
        known_at = datetime(ka_date.year, ka_date.month, ka_date.day,
                            9, 0, tzinfo=NY)
        for m in range(3):
            month = (qstart + pd.offsets.MonthBegin(m)).date()
            rows.append((month, quad, round(float(g), 4), round(float(i), 4),
                         known_at))
    if flats:
        print(f"flat deltas treated as 'down': quarters {flats}")
    print(f"{len(rows)} month rows, {rows[0][0]} .. {rows[-1][0]}")
    tail = [r for r in rows if r[0] >= date(2024, 1, 1)]
    for m, q, g, i, ka in tail:
        if m.month in (1, 4, 7, 10):
            print(f"  {m} Q{q}  g_roc {g:+.2f} i_roc {i:+.2f}  known {ka.date()}")
    if args.dry_run:
        print("[dry-run] no writes")
        return 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO quad_monthly (month, quad, g_roc, i_roc, known_at) "
            "VALUES %s ON CONFLICT (month) DO UPDATE SET "
            "quad=EXCLUDED.quad, g_roc=EXCLUDED.g_roc, i_roc=EXCLUDED.i_roc, "
            "known_at=EXCLUDED.known_at, loaded_at=now()",
            rows, page_size=500)
        conn.commit()
        cur.execute("SELECT count(*) FROM quad_monthly")
        print(f"quad_monthly holds {cur.fetchone()[0]} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
