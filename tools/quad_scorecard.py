"""tools/quad_scorecard.py — print the audited quarterly scorecard.

Thin client of the nowcast_vs_realized view (migration 099), which owns
the logic: method tiers (period-tagged > prose), three horizons
(H1 first call >=90d pre-quarter, H2 last pre-quarter, H3 last
in-quarter), FRED-realized (2bp CPI tie rule) and Hedgeye-printed-actual
comparators, def-gap flag, and 1/0.5/0 scoring (0.5 = the winning note
also stated the realized quad — hedged/dual statement).

    py tools/quad_scorecard.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
db_pg._load_dotenv_fallback()


def main() -> int:
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT period, h1_quad, h1_note, h2_quad, h2_note,
                   h3_quad, h3_note, fred_quad, he_actual_quad, def_gap,
                   h2_score_vs_he, h3_score_vs_he
            FROM nowcast_vs_realized ORDER BY ref_month""")
        rows = cur.fetchall()
    fmt = lambda q, d: f"{q} @{d.strftime('%m/%d/%y')}" if q else "-"
    print(f"{'period':<7}{'H1 first(-90d)':>16}{'H2 pre-qtr':>14}"
          f"{'H3 in-qtr':>14}{'FRED':>6}{'HE-act':>8}{'gap':>5}"
          f"{'H2/H3 vs HE':>13}")
    t = {"h1": [0.0, 0], "h2": [0.0, 0], "h3": [0.0, 0]}
    for (p, q1, d1, q2, d2, q3, d3, fq, hq, gap, s2, s3) in rows:
        for tag, q, s in (("h1", q1, None), ("h2", q2, s2), ("h3", q3, s3)):
            ref = hq
            if q and ref:
                if s is None:                       # h1 has no score col
                    s = 1.0 if q == ref else 0.0
                t[tag][0] += float(s)
                t[tag][1] += 1
        sc = lambda s: ("." if s is None else
                        {1.0: "Y", 0.5: "half", 0.0: "n"}[float(s)])
        print(f"{p:<7}{fmt(q1, d1):>16}{fmt(q2, d2):>14}{fmt(q3, d3):>14}"
              f"{str(fq or '-'):>6}{str(hq or '-'):>8}"
              f"{'GAP' if gap else '-':>5}{sc(s2) + '/' + sc(s3):>13}")
    for tag in ("h1", "h2", "h3"):
        pts, n = t[tag]
        print(f"{tag.upper()} vs HE-actual: {pts:g}/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
