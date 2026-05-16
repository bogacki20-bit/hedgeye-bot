"""End-of-week report generator.

Writes reports/2026-week-<ISO_week>.md and pushes a Telegram summary.
Sections:
  - Hedgeye accuracy this week        (outcomes_log win rate)
  - SpotGamma regime changes          (spotgamma_snapshots gamma sign flips)
  - MFR Hurst distribution            (mfr_snapshots hurst buckets)
  - Portfolio P&L breakdown           (outcomes_log net by account/ticker)
  - Bot accountability                (alerts, [SG NOT CITED] /
                                        [REASONING UNVERIFIED] counts)

Every section degrades to "n/a (no data / table not migrated)" rather
than failing, so it runs before the ML migrations are applied.

CLI:
    python -m tools.generate_eow_report --week current
    python -m tools.generate_eow_report --week current --dry-run
"""

from __future__ import annotations

import sys
import logging
import datetime as dt
from pathlib import Path

log = logging.getLogger(__name__)

_REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


def _week_bounds(week: str) -> tuple[dt.date, dt.date, int, int]:
    today = dt.date.today()
    year, iso_week, _ = today.isocalendar()
    if week not in ("current", None):
        try:
            iso_week = int(week)
        except ValueError:
            pass
    monday = dt.date.fromisocalendar(year, iso_week, 1)
    sunday = monday + dt.timedelta(days=6)
    return monday, sunday, year, iso_week


def _q(cur, sql, params=()):
    """Run a read query inside its own SAVEPOINT so one failing query
    (e.g. a not-yet-migrated table) doesn't poison the transaction for
    the remaining sections."""
    try:
        cur.execute("SAVEPOINT eow_q")
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT eow_q")
        return rows
    except Exception as e:
        log.debug("query failed (%s): %s", sql.split()[0] if sql else "?", e)
        try:
            cur.execute("ROLLBACK TO SAVEPOINT eow_q")
        except Exception:
            pass
        return None


def _hedgeye_accuracy(cur, lo, hi) -> str:
    rows = _q(cur, """
        SELECT COUNT(*), COUNT(*) FILTER (WHERE was_winner),
               COALESCE(SUM(pnl_dollars),0)
          FROM outcomes_log
         WHERE closed_at::date BETWEEN %s AND %s
    """, (lo, hi))
    if rows is None:
        return "- n/a (outcomes_log not migrated yet)"
    n, w, pnl = rows[0]
    if not n:
        return "- No closed round trips this week."
    return (f"- Closed round trips: {n}\n"
            f"- Win rate: {w}/{n} ({w/n*100:.0f}%)\n"
            f"- Net realized P&L: ${float(pnl):,.2f}")


def _sg_regime(cur, lo, hi) -> str:
    rows = _q(cur, """
        SELECT ticker,
               COUNT(*) FILTER (WHERE call_gamma IS NOT NULL) AS n
          FROM spotgamma_snapshots
         WHERE snapshot_date BETWEEN %s AND %s
         GROUP BY ticker ORDER BY n DESC LIMIT 5
    """, (lo, hi))
    if rows is None:
        return "- n/a (spotgamma_snapshots unavailable)"
    if not rows:
        return "- No SpotGamma snapshots this week."
    return "\n".join(f"- {t}: {n} snapshots" for t, n in rows)


def _hurst_dist(cur, lo, hi) -> str:
    rows = _q(cur, """
        SELECT
          COUNT(*) FILTER (WHERE hurst >= 0.6)              AS trending,
          COUNT(*) FILTER (WHERE hurst <= 0.4)              AS mean_rev,
          COUNT(*) FILTER (WHERE hurst > 0.4 AND hurst<0.6) AS random_w,
          COUNT(*) FILTER (WHERE hurst IS NOT NULL)         AS total
          FROM mfr_snapshots
         WHERE snapshot_date BETWEEN %s AND %s
    """, (lo, hi))
    if rows is None:
        return "- n/a (mfr_snapshots unavailable)"
    tr, mr, rw, tot = rows[0]
    if not tot:
        return "- No MFR Hurst data this week."
    return (f"- Trending (H>=0.6): {tr} ({tr/tot*100:.0f}%)\n"
            f"- Mean-reverting (H<=0.4): {mr} ({mr/tot*100:.0f}%)\n"
            f"- Random walk: {rw} ({rw/tot*100:.0f}%)  [n={tot}]")


def _pnl_breakdown(cur, lo, hi) -> str:
    rows = _q(cur, """
        SELECT account_number, COUNT(*), COALESCE(SUM(pnl_dollars),0)
          FROM outcomes_log
         WHERE closed_at::date BETWEEN %s AND %s
         GROUP BY account_number ORDER BY 3 DESC
    """, (lo, hi))
    if rows is None:
        return "- n/a (outcomes_log not migrated yet)"
    if not rows:
        return "- No realized P&L this week."
    return "\n".join(
        f"- {acct or 'unknown'}: {n} trades, ${float(p):,.2f}"
        for acct, n, p in rows)


def _accountability(cur, lo, hi) -> str:
    rows = _q(cur, """
        SELECT
          COUNT(*) AS alerts,
          COUNT(*) FILTER (WHERE recommendation_text LIKE '%%[SG NOT CITED]%%'),
          COUNT(*) FILTER (WHERE recommendation_text LIKE '%%[REASONING UNVERIFIED]%%')
          FROM alerts_fired
         WHERE fired_at::date BETWEEN %s AND %s
    """, (lo, hi))
    if rows is None:
        return "- n/a (alerts_fired unavailable)"
    a, sg, ru = rows[0]
    return (f"- Alerts fired: {a}\n"
            f"- [SG NOT CITED]: {sg}\n"
            f"- [REASONING UNVERIFIED]: {ru}")


def run(week: str = "current", dry_run: bool = False) -> int:
    try:
        import db_pg
    except Exception as e:
        print(f"ERROR: db_pg unavailable: {e}", file=sys.stderr)
        return 2

    lo, hi, year, iso_week = _week_bounds(week)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        sections = {
            "Hedgeye accuracy this week": _hedgeye_accuracy(cur, lo, hi),
            "SpotGamma regime / coverage": _sg_regime(cur, lo, hi),
            "MFR Hurst distribution": _hurst_dist(cur, lo, hi),
            "Portfolio P&L breakdown": _pnl_breakdown(cur, lo, hi),
            "Bot accountability": _accountability(cur, lo, hi),
        }

    title = f"EOW Report — {year} week {iso_week} ({lo} … {hi})"
    md = [f"# {title}", ""]
    for h, body in sections.items():
        md += [f"## {h}", body, ""]
    report_md = "\n".join(md)

    print(report_md)

    if dry_run:
        print("[dry-run] no file written, no Telegram.")
        return 0

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = _REPORTS_DIR / f"{year}-week-{iso_week}.md"
    out.write_text(report_md, encoding="utf-8")
    print(f"Wrote {out}")

    try:
        from notifier import send_telegram
        summary = (f"{title}\n\n"
                   + sections["Hedgeye accuracy this week"] + "\n\n"
                   + sections["Bot accountability"])
        send_telegram("EOW REPORT", summary, priority=2)
    except Exception as e:
        log.warning("telegram EOW summary failed: %s", e)
    return 0


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.generate_eow_report")
    p.add_argument("--week", default="current",
                   help='"current" or an ISO week number')
    p.add_argument("--dry-run", action="store_true",
                   help="print only; no file, no Telegram")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(week=a.week, dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(_cli())
