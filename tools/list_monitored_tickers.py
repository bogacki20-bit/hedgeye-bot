"""Print the SpotGamma sweep watchlist, one ticker per line.

Source of truth = the `monitored_tickers` SQL VIEW in Railway Postgres
(re-applied 2026-05-12 from migrations/004_ticker_inventory.sql). Every
ticker Hedgeye has ever signaled on, currently flagged `is_active=TRUE`
in hedgeye_ticker_inventory, is in scope.

The Cowork SpotGamma sweep SKILLs invoke this via the file bridge's
ps_exec handler and consume stdout. Keeping connection logic here means
the SKILLs never see DATABASE_PUBLIC_URL.

Priority axis
-------------
  --priority high   tickers touched by recent Hedgeye signals
                    (last_source IN macro_show / risk_range / etf_pro /
                    investing_ideas AND last_seen_at >= CURRENT_DATE -
                    interval '3 days'). 3-day window so a one-day classify
                    outage doesn't zero out the high bucket and cascade to
                    the SG sweep.
  --priority tail   monitored_tickers MINUS today's high set. The long tail
                    that gets covered after the high-priority sweep lands.
  --priority all    everything in monitored_tickers. Default.

Output is plain ASCII, one ticker per line, sorted. Ends without a trailing
newline so callers can split cleanly.
"""
from __future__ import annotations

import argparse
import os
import sys


HIGH_SOURCES = ("macro_show", "risk_range", "etf_pro", "investing_ideas")


def _connect():
    """Open a Postgres connection from env. Same precedence as tools.bridge_call."""
    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_PUBLIC_URL / DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)
    try:
        import psycopg2
    except ImportError:
        print("ERROR: psycopg2 not installed", file=sys.stderr)
        sys.exit(2)
    return psycopg2.connect(url)


def fetch_all() -> list[str]:
    """Every ticker the bot considers in-scope right now."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ticker FROM monitored_tickers ORDER BY ticker")
            return [r[0] for r in cur.fetchall()]


def fetch_high() -> list[str]:
    """Tickers Hedgeye touched today."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT ticker
                  FROM hedgeye_ticker_inventory
                 WHERE last_source = ANY(%s)
                   AND last_seen_at >= CURRENT_DATE - interval '3 days'
                   AND COALESCE(is_active, TRUE) = TRUE
                 ORDER BY ticker
                """,
                (list(HIGH_SOURCES),),
            )
            return [r[0] for r in cur.fetchall()]


def fetch_tail() -> list[str]:
    """monitored_tickers minus today's high set."""
    high = set(fetch_high())
    return [t for t in fetch_all() if t not in high]


def fetch_hedgeye_active(lookback_days: int = 7) -> list[str]:
    """Tickers Keith is ACTIVELY surfacing through Hedgeye products.

    Union of every ticker that has landed in a Hedgeye product feed
    (Risk Range / Signal Strength / Portfolio Solutions / ETF Pro /
    Investing Ideas) within the last `lookback_days`. This is the
    operational expression of his Quad view — the slide-derived Quad
    universe is only the framework reference.

    Tickers are normalized via ticker_aliases.normalize_ticker, deduped,
    and sorted. Empty/null tickers are dropped.
    """
    window = f"{int(lookback_days)} days"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ticker FROM hedgeye_risk_ranges
                    WHERE signal_date >= CURRENT_DATE - interval %s
                UNION
                SELECT DISTINCT ticker FROM hedgeye_signal_strength
                    WHERE snapshot_date >= CURRENT_DATE - interval %s
                UNION
                SELECT DISTINCT ticker FROM hedgeye_portfolio_solutions
                    WHERE snapshot_date >= CURRENT_DATE - interval %s
                UNION
                SELECT DISTINCT ticker FROM hedgeye_etf_pro_ranges
                    WHERE week_of >= CURRENT_DATE - interval %s
                UNION
                SELECT DISTINCT ticker FROM hedgeye_investing_ideas
                    WHERE snapshot_date >= CURRENT_DATE - interval %s
                """,
                (window, window, window, window, window),
            )
            rows = [r[0] for r in cur.fetchall()]

    try:
        from tools.ticker_aliases import normalize_ticker
    except ImportError:
        from ticker_aliases import normalize_ticker  # type: ignore

    out: set[str] = set()
    for t in rows:
        n = normalize_ticker(t)
        if n and n.strip():
            out.add(n.strip())
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source", choices=("hedgeye_active", "quad", "monitored"),
        default="monitored",
        help="Universe source. 'hedgeye_active' = tickers Keith is actively "
             "surfacing through Hedgeye products in the last --lookback-days. "
             "'monitored' (default) preserves the legacy --priority behavior.",
    )
    ap.add_argument(
        "--lookback-days", type=int, default=7,
        help="Lookback window for --source hedgeye_active (default: 7)",
    )
    ap.add_argument(
        "--priority", choices=("high", "tail", "all"), default="all",
        help="Which slice to print for --source monitored (default: all)",
    )
    ap.add_argument(
        "--count", action="store_true",
        help="Print only the count (useful for SKILL pre-flight checks)",
    )
    args = ap.parse_args()

    if args.source == "hedgeye_active":
        tickers = fetch_hedgeye_active(args.lookback_days)
    elif args.source == "quad":
        # Quad-derived universe lives in proactive_scanner / tools.doctrine;
        # this resolver only owns the monitored + hedgeye_active sources.
        print("ERROR: --source quad is resolved by proactive_scanner, "
              "not list_monitored_tickers", file=sys.stderr)
        return 2
    elif args.priority == "high":
        tickers = fetch_high()
    elif args.priority == "tail":
        tickers = fetch_tail()
    else:
        tickers = fetch_all()

    if args.count:
        sys.stdout.write(str(len(tickers)))
        return 0

    sys.stdout.write("\n".join(tickers))
    return 0


if __name__ == "__main__":
    sys.exit(main())
