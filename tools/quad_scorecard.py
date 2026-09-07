"""tools/quad_scorecard.py — A3 audit scorecard for the stated-Quad corpus.

Per quarter, Hedgeye's call at three horizons (lead time is the product):
  H1  first call >= 90 days before quarter start
  H2  last call before quarter start
  H3  last call in-quarter (before quarter end)

"Call" = the winning statement of the chosen note: explicit period-tagged
methods (gip/qtag/mtag) outrank bare prose, then mention frequency
(n_hits), then lowest quad for determinism. Compared against BOTH
comparators: FRED-realized (quad_monthly) and Hedgeye's own printed
actual (hedgeye_quad_actual, where transcribed). def_gap flags the
quarters where the two comparators disagree — that's our rule-vs-theirs
definition gap, not a Hedgeye miss.

    py tools/quad_scorecard.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402
db_pg._load_dotenv_fallback()

MRANK = {"gip": 0, "qtag": 1, "mtag": 1, "prose": 9}


def qdates(period: str) -> tuple[date, date]:
    qn, yy = int(period[0]), int(period[2:4])
    start = date(2000 + yy, qn * 3 - 2, 1)
    end = (date(2000 + yy + (qn == 4), (qn % 4) * 3 + 1, 1)
           - timedelta(days=1))
    return start, end


def note_winner(rows):
    """rows: [(note_date, quad, n_hits, method)] for ONE note -> quad."""
    best = min(MRANK.get(m, 9) for _d, _q, _h, m in rows)
    votes = {}
    for _d, q, h, m in rows:
        if MRANK.get(m, 9) == best:
            votes[q] = votes.get(q, 0) + h
    return sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def horizon_call(stmts, cutoff, first=False):
    """stmts: [(note_date, quad, n_hits, method)] about one period.
    Pick the earliest (first=True) or latest note <= cutoff; return
    (quad, note_date) or (None, None)."""
    eligible = sorted({d for d, *_ in stmts if d <= cutoff})
    if not eligible:
        return None, None
    nd = eligible[0] if first else eligible[-1]
    return note_winner([r for r in stmts if r[0] == nd]), nd


def main() -> int:
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT period, note_date, quad, n_hits, coalesce(method,'prose')
            FROM hedgeye_quad_stated
            WHERE scope='quarterly' AND period ~ '^[1-4]Q[0-9]{2}$'
            ORDER BY period, note_date""")
        by_period = {}
        for period, nd, q, h, m in cur.fetchall():
            by_period.setdefault(period, []).append((nd, q, h, m))
        cur.execute("SELECT quarter, quad FROM hedgeye_quad_actual")
        he_actual = dict(cur.fetchall())
        cur.execute("SELECT month, quad FROM quad_monthly")
        fred = dict(cur.fetchall())

    print(f"{'period':<7}{'H1 first(-90d)':>16}{'H2 pre-qtr':>14}"
          f"{'H3 in-qtr':>14}{'FRED':>6}{'HE-act':>8}{'def_gap':>9}"
          f"{'H2/H3 vs FRED':>15}")
    tally = {"H1": [0, 0], "H2": [0, 0], "H3": [0, 0]}
    for period in sorted(by_period,
                         key=lambda p: (p[2:4], p[0])):
        stmts = by_period[period]
        qs, qe = qdates(period)
        h1, d1 = horizon_call(stmts, qs - timedelta(days=90), first=True)
        h2, d2 = horizon_call(stmts, qs - timedelta(days=1))
        h3, d3 = horizon_call(stmts, qe)
        f = fred.get(qs)
        ha = he_actual.get(period)
        gap = ("GAP" if f and ha and f != ha else
               "-" if f and ha else "?")
        for tag, call in (("H1", h1), ("H2", h2), ("H3", h3)):
            if call and f:
                tally[tag][0] += (call == f)
                tally[tag][1] += 1
        fmt = lambda c, d: f"{c} @{d.strftime('%m/%d/%y')}" if c else "-"
        m2 = ("Y" if h2 == f else "n") if h2 and f else "."
        m3 = ("Y" if h3 == f else "n") if h3 and f else "."
        print(f"{period:<7}{fmt(h1, d1):>16}{fmt(h2, d2):>14}"
              f"{fmt(h3, d3):>14}{str(f or '-'):>6}{str(ha or '-'):>8}"
              f"{gap:>9}{m2 + '/' + m3:>15}")
    for tag in ("H1", "H2", "H3"):
        hit, n = tally[tag]
        print(f"{tag} vs FRED: {hit}/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
