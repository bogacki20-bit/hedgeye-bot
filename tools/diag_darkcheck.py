"""diag_darkcheck.py — READ-ONLY. Latest v_screener row for the dark-name set.

Connects with DATABASE_PUBLIC_URL from .env (falling back to the process env, so
it also works under `railway run`). SELECT only; no writes, no DDL.

NOTE ON SCOPE: v_screener carries the MFR band only (range_low/range_high come
from mfr_snapshots). It does NOT include shadow ranges — the mfr->shd failover
is applied in the display path (tools/screener.py, weekend_report), not in the
view. So a dark name correctly shows NO RANGE here even though the reports now
print a ·shd band for it. To see the shadow side, query shadow_snapshots.
"""

import os
import sys

TICKERS = ["NOBL", "BUG", "COLO", "CPER", "ENZL", "EPHE", "IPAY",
           "REW", "SPLV", "VTIP", "WEAT"]

# v_screener exposes range_low / range_high (NOT lower_range / upper_range).
SQL = """
    SELECT ticker, snapshot_date, range_pos,
           range_low  AS lower_range,
           range_high AS upper_range,
           trend_dir
    FROM v_screener
    WHERE ticker = ANY(%s)
"""


def _dsn() -> str:
    """DATABASE_PUBLIC_URL from .env, else the process env."""
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(here, ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() in ("DATABASE_PUBLIC_URL", "DATABASE_URL"):
                    return v.strip().strip('"').strip("'")
    except OSError as e:
        sys.exit(f"cannot read {env_path}: {e}")
    sys.exit("DATABASE_PUBLIC_URL not set and not found in .env")


def main() -> None:
    try:
        import psycopg2
    except ImportError:
        sys.exit("psycopg2 not installed")

    conn = psycopg2.connect(_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(SQL, (TICKERS,))
            rows = {r[0]: r[1:] for r in cur.fetchall()}
    finally:
        conn.close()

    def f(x, nd=2):
        return f"{float(x):.{nd}f}" if x is not None else "—"

    print(f"{'ticker':<8} {'snapshot':<12} {'rp':>6} {'lower':>10} {'upper':>10}  trend")
    print("-" * 62)
    for t in TICKERS:
        r = rows.get(t)
        if r is None:
            print(f"{t:<8} NO ROW in v_screener")
            continue
        sd, rp, lo, hi, trend = r
        if lo is None or hi is None:
            print(f"{t:<8} {str(sd or '—'):<12} NO RANGE"
                  f"{'':>18}  {trend or '—'}")
            continue
        print(f"{t:<8} {str(sd):<12} {f(rp):>6} {f(lo):>10} {f(hi):>10}  {trend or '—'}")


if __name__ == "__main__":
    main()
