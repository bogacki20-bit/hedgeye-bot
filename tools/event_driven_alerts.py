"""Event-driven delta alerts for Hedgeye product feeds.

Compares the two most recent snapshots of each product and fires a
Telegram alert + records an alerts_fired row for each material change.

Wording (operator-spec, 2026-05-25):
    ✅ <TICKER> ADDED to <product> [<side>] on <YYYY-MM-DD>
    ❌ <TICKER> REMOVED from <product> [<side>] on <YYYY-MM-DD>
    🔄 <TICKER> FLIPPED on <product>: <old_side> → <new_side> on <YYYY-MM-DD>
    ⚠️  <TICKER> <product> rank moved UP/DOWN N positions (now rank R) on <date>

The bracketed [<side>] segment is omitted for products without a long /
short axis in their schema (Signal Strength, Portfolio Solutions,
Investing Ideas — these are single-bucket lists in our DB).

Products covered:
  Portfolio Solutions (hedgeye_portfolio_solutions)
    - ADDED / REMOVED between the two latest snapshot_dates
    - Rank jump > 20 positions (snapshot-over-snapshot, or rank_change_1w)
  ETF Pro (hedgeye_etf_pro_ranges)
    - ADDED / REMOVED from the weekly range list, tagged Long/Short
      based on description prefix
    - FLIPPED Long↔Short across two consecutive week_of snapshots
  Signal Strength (hedgeye_signal_strength)
    - is_added_today / is_removed_today flags on the latest snapshot
      (SS has no native long/short split — single bucket)
  Investing Ideas (hedgeye_investing_ideas) — added 2026-05-25
    - ADDED / REMOVED between the two latest snapshot_dates (long-only
      leaderboard)

alerts_fired has no `kind` column; `boundary` carries the discriminator
('ps_delta' | 'etf_pro_delta' | 'ss_delta' | 'ii_delta'). Telegram uses
notifier.send_telegram (the bridge has no telegram_send entrypoint).

CLI:
    python -m tools.event_driven_alerts            # alert + persist
    python -m tools.event_driven_alerts --dry-run  # print only
"""

from __future__ import annotations

import sys
import logging
import datetime as dt

log = logging.getLogger(__name__)

# Windows defaults stdout to cp1252, which can't encode the ✅/❌/🔄/⚠️
# emoji used in the alert text. Reconfigure to utf-8 so the diagnostic
# `print()` calls and the launcher's stdout pipe both round-trip cleanly.
# Telegram delivery is already utf-8 safe; this is purely about local
# stdout. Wrapped in try so non-Windows / older Python builds don't trip.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

_RANK_JUMP_THRESHOLD = 20


# ─────────────────────────── helpers ───────────────────────────

def _two_latest_dates(cur, table: str, date_col: str) -> tuple:
    cur.execute(
        f"SELECT DISTINCT {date_col} FROM {table} "
        f"ORDER BY {date_col} DESC LIMIT 2"
    )
    rows = [r[0] for r in cur.fetchall()]
    today = rows[0] if rows else None
    prev = rows[1] if len(rows) > 1 else None
    return today, prev


def _etf_pro_side(description: str | None) -> str | None:
    """Return 'Long' / 'Short' from the ETF Pro `description` prefix.
    parser_etf_pro.upsert_rows writes "LONG | ..." / "SHORT | ..." as the
    canonical bias tag. Returns None for old rows without a prefix."""
    if not description:
        return None
    head = description.strip().upper()
    if head.startswith("LONG"):
        return "Long"
    if head.startswith("SHORT"):
        return "Short"
    return None


def _fmt_added(ticker: str, product: str, on_date: dt.date,
               side: str | None = None) -> str:
    side_chunk = f" {side}" if side else ""
    return f"✅ {ticker} ADDED to {product}{side_chunk} on {on_date}"


def _fmt_removed(ticker: str, product: str, on_date: dt.date,
                 side: str | None = None) -> str:
    side_chunk = f" {side}" if side else ""
    return f"❌ {ticker} REMOVED from {product}{side_chunk} on {on_date}"


def _fmt_flipped(ticker: str, product: str, on_date: dt.date,
                 old_side: str, new_side: str) -> str:
    return (f"🔄 {ticker} FLIPPED on {product}: "
            f"{old_side} → {new_side} on {on_date}")


def _fmt_rank_move(ticker: str, product: str, on_date: dt.date,
                   jump: int, new_rank: int) -> str:
    direction = "UP" if jump > 0 else "DOWN"
    return (f"⚠️ {ticker} {product} rank moved {direction} "
            f"{abs(jump)} positions (now rank {new_rank}) on {on_date}")


# ─────────────────────────── per-product delta detectors ───────────────────────────

def _ps_deltas(cur) -> list[dict]:
    today, prev = _two_latest_dates(cur, "hedgeye_portfolio_solutions",
                                    "snapshot_date")
    if not today:
        return []
    cur.execute("SELECT ticker, rank, rank_change_1w FROM "
                "hedgeye_portfolio_solutions WHERE snapshot_date=%s", (today,))
    cur_rows = {t: (rk, rc) for t, rk, rc in cur.fetchall()}
    prev_ranks = {}
    if prev:
        cur.execute("SELECT ticker, rank FROM hedgeye_portfolio_solutions "
                    "WHERE snapshot_date=%s", (prev,))
        prev_ranks = {t: rk for t, rk in cur.fetchall()}

    out: list[dict] = []
    for t, (rk, rc) in cur_rows.items():
        if prev and t not in prev_ranks:
            out.append({"ticker": t, "kind": "ps_delta",
                        "signal_date": today,
                        "msg": _fmt_added(t, "Portfolio Solutions", today)
                               + f" (rank {rk})"})
            continue
        jump = None
        if t in prev_ranks and rk is not None and prev_ranks[t] is not None:
            jump = prev_ranks[t] - rk  # +ve = moved up the list
        elif rc is not None:
            jump = rc
        if jump is not None and abs(jump) > _RANK_JUMP_THRESHOLD:
            out.append({"ticker": t, "kind": "ps_delta",
                        "signal_date": today,
                        "msg": _fmt_rank_move(t, "Portfolio Solutions",
                                              today, jump, rk)})
    for t in (prev_ranks.keys() - cur_rows.keys()) if prev else []:
        out.append({"ticker": t, "kind": "ps_delta",
                    "signal_date": today,
                    "msg": _fmt_removed(t, "Portfolio Solutions", today)})
    return out


def _etf_pro_deltas(cur) -> list[dict]:
    """ETF Pro deltas. Includes Long / Short side tagging and FLIP
    detection (same ticker, different bias across consecutive weeks)."""
    today, prev = _two_latest_dates(cur, "hedgeye_etf_pro_ranges", "week_of")
    if not today or not prev:
        return []
    cur.execute("SELECT ticker, description FROM hedgeye_etf_pro_ranges "
                "WHERE week_of=%s", (today,))
    today_rows = {r[0]: _etf_pro_side(r[1]) for r in cur.fetchall()}
    cur.execute("SELECT ticker, description FROM hedgeye_etf_pro_ranges "
                "WHERE week_of=%s", (prev,))
    prev_rows = {r[0]: _etf_pro_side(r[1]) for r in cur.fetchall()}

    out: list[dict] = []
    # ADDED
    for t in sorted(set(today_rows.keys()) - set(prev_rows.keys())):
        out.append({"ticker": t, "kind": "etf_pro_delta",
                    "signal_date": today,
                    "msg": _fmt_added(t, "ETF Pro", today,
                                      side=today_rows.get(t))})
    # REMOVED — use the side it WAS on before being removed
    for t in sorted(set(prev_rows.keys()) - set(today_rows.keys())):
        out.append({"ticker": t, "kind": "etf_pro_delta",
                    "signal_date": today,
                    "msg": _fmt_removed(t, "ETF Pro", today,
                                        side=prev_rows.get(t))})
    # FLIPPED — same ticker present in both, bias changed
    for t in sorted(set(today_rows.keys()) & set(prev_rows.keys())):
        old_side, new_side = prev_rows.get(t), today_rows.get(t)
        if old_side and new_side and old_side != new_side:
            out.append({"ticker": t, "kind": "etf_pro_delta",
                        "signal_date": today,
                        "msg": _fmt_flipped(t, "ETF Pro", today,
                                            old_side, new_side)})
    return out


def _ss_deltas(cur) -> list[dict]:
    """Signal Strength deltas. SS has no native long/short split in our
    schema — single bucket. The parser only captures Add/Remove deltas
    (the full list is image-only in the email) so this is a direct
    read of the is_added_today / is_removed_today flags."""
    today, _ = _two_latest_dates(cur, "hedgeye_signal_strength",
                                 "snapshot_date")
    if not today:
        return []
    cur.execute("SELECT ticker, is_added_today, is_removed_today FROM "
                "hedgeye_signal_strength WHERE snapshot_date=%s", (today,))
    out: list[dict] = []
    for t, added, removed in cur.fetchall():
        if added:
            out.append({"ticker": t, "kind": "ss_delta",
                        "signal_date": today,
                        "msg": _fmt_added(t, "Signal Strength", today)})
        elif removed:
            out.append({"ticker": t, "kind": "ss_delta",
                        "signal_date": today,
                        "msg": _fmt_removed(t, "Signal Strength", today)})
    return out


def _ii_deltas(cur) -> list[dict]:
    """Investing Ideas deltas (Top-21 long leaderboard). Added 2026-05-25.

    Schema has explicit `date_added` / `date_removed` columns, but using
    snapshot-over-snapshot is consistent with how the other products
    detect deltas and survives parser bugs that leave the date columns
    null. The product is long-only — no side tag."""
    # Probe the table first — install may not be migrated yet on every
    # environment, and we don't want a missing-table error to nuke the
    # PS/ETF Pro/SS deltas.
    try:
        cur.execute("SELECT 1 FROM hedgeye_investing_ideas LIMIT 1")
    except Exception as e:
        log.debug("ii_deltas: table unavailable (%s); skipping II", e)
        return []

    today, prev = _two_latest_dates(cur, "hedgeye_investing_ideas",
                                    "snapshot_date")
    if not today:
        return []
    cur.execute("SELECT DISTINCT ticker FROM hedgeye_investing_ideas "
                "WHERE snapshot_date=%s", (today,))
    today_set = {r[0] for r in cur.fetchall()}
    prev_set: set = set()
    if prev:
        cur.execute("SELECT DISTINCT ticker FROM hedgeye_investing_ideas "
                    "WHERE snapshot_date=%s", (prev,))
        prev_set = {r[0] for r in cur.fetchall()}

    out: list[dict] = []
    for t in sorted(today_set - prev_set):
        out.append({"ticker": t, "kind": "ii_delta",
                    "signal_date": today,
                    "msg": _fmt_added(t, "Investing Ideas", today)})
    for t in sorted(prev_set - today_set):
        out.append({"ticker": t, "kind": "ii_delta",
                    "signal_date": today,
                    "msg": _fmt_removed(t, "Investing Ideas", today)})
    return out


# ─────────────────────────── runner ───────────────────────────

def run(dry_run: bool = False) -> int:
    try:
        import db_pg
    except Exception as e:
        print(f"ERROR: db_pg unavailable: {e}", file=sys.stderr)
        return 2

    deltas: list[dict] = []
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            deltas += _ps_deltas(cur)
            deltas += _etf_pro_deltas(cur)
            deltas += _ss_deltas(cur)
            deltas += _ii_deltas(cur)
    except Exception as e:
        print(f"ERROR: delta computation failed: {e}", file=sys.stderr)
        return 3

    print(f"Detected {len(deltas)} event delta(s).")
    for d in deltas:
        print(f"  [{d['kind']}] {d['msg']}")

    if dry_run:
        print("[dry-run] no Telegram, no alerts_fired writes.")
        return 0

    # Idempotency: record FIRST and Telegram only on a fresh row.
    # alerts_fired has ON CONFLICT (ticker, boundary, signal_date) DO
    # NOTHING — record_alert returns None for a duplicate. signal_date is
    # the snapshot date this delta was computed against (per-product),
    # NOT calendar today, so re-running the detector tomorrow against the
    # SAME snapshots (e.g. ETF Pro weekly hasn't refreshed yet) suppresses
    # the duplicate telegram.
    sent = 0
    suppressed = 0
    for d in deltas:
        sig_date = d.get("signal_date") or dt.date.today()
        new_alert_id: int | None = None
        try:
            import db_pg
            new_alert_id = db_pg.record_alert(
                ticker=d["ticker"],
                boundary=d["kind"],
                signal_date=sig_date,
                recommendation_text=d["msg"],
                suggested_action="EVENT",
            )
        except Exception as e:
            log.warning("record_alert failed for %s: %s", d["ticker"], e)

        if new_alert_id is None:
            suppressed += 1
            print(f"  [dedup] {d['kind']} {d['ticker']} already alerted on {sig_date}")
            continue

        try:
            from notifier import send_telegram
            send_telegram(f"HEDGEYE {d['kind'].upper()}", d["msg"], priority=2)
        except Exception as e:
            log.warning("telegram send failed for %s: %s", d["ticker"], e)
        sent += 1
    print(f"Fired {sent} alert(s); suppressed {suppressed} duplicate(s).")
    return 0


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.event_driven_alerts")
    p.add_argument("--dry-run", action="store_true",
                   help="detect + print only; no Telegram, no DB writes")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(_cli())
