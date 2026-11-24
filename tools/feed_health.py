"""Feed health: is every paid data feed actually landing in the bot? (Kris, 11/10)

Checks the DATA, not the task status. On 11/10 the T1A and Macro Show deck
tasks had reported "succeeded" every morning while storing nothing since 10/26,
and the SpotGamma equity hub had been dead since 11/4.

    py -m tools.feed_health            # print the report
    py -m tools.feed_health --send     # also text the one-line verdict to Telegram
    py -m tools.feed_health --alert    # text Telegram ONLY when a feed goes down or comes back

Lag = weekdays with no new data. Before 10:00 ET today doesn't count yet.
No LLM calls, read-only.
"""
from __future__ import annotations

import argparse
import os
import datetime as dt
import pathlib
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = pathlib.Path(__file__).resolve().parent.parent

# (label, source, table, column, weekdays of lag allowed)
FEEDS = [
    ("T1A Market Situation Report", "Hedgeye/T1A", "t1a_snapshots", "report_date", 1),
    ("Macro Show deck", "Hedgeye", "tms_decks", "show_date", 1),
    ("The Call notes", "Hedgeye", "hedgeye_the_call", "signal_date", 1),
    ("Risk Ranges", "Hedgeye", "hedgeye_risk_ranges", "signal_date", 1),
    ("Signal Strength", "Hedgeye", "hedgeye_signal_strength", "snapshot_date", 1),
    ("Real-Time Alerts", "Hedgeye", "hedgeye_rta", "signal_date", 2),
    ("Keith's signals", "Hedgeye", "hedgeye_keiths_signals", "signal_date", 5),   # weekly (Mondays)
    ("ETF Pro ranges", "Hedgeye", "hedgeye_etf_pro_ranges", "week_of", 5),
    ("Quad (dashboard)", "Hedgeye", "hedgeye_quad", "as_of", 5),
    ("Hedgeye emails", "Hedgeye", "hedgeye_emails_raw", "received_at", 1),
    ("Research digest", "Hedgeye", "research_digest", "signal_date", 1),
    # 11/19: filled by tools/fill_empty_tables.py after each capture block (went 2 days stale unnoticed)
    ("Early Look (table)", "Hedgeye", "hedgeye_early_look", "publish_date", 1),
    ("Macro Show summary notes (table)", "Hedgeye", "hedgeye_macro_show_notes", "publish_date", 1),
    ("SpotGamma index levels", "SpotGamma", "sg_index_levels", "note_date", 1),
    ("SpotGamma equity hub", "SpotGamma", "spotgamma_snapshots", "snapshot_date", 1),
    ("SpotGamma tilt", "SpotGamma", "sg_tilt", "trade_date", 1),
    # 11/15: the Tape, read every 15 min in market hours from Kris's Chrome (tools/sg_tape.py)
    ("SpotGamma tape (15-min reads)", "SpotGamma", "sg_tape_reads", "read_at", 1),
    # 11/24: the watcher chat's TRACE/HIRO reads, loaded from data/sg_watch by tools/sg_watch_load.py
    ("SpotGamma watch (TRACE/HIRO reads)", "SpotGamma", "sg_watch_reads", "read_date", 1),
    # 11/24: the Volatility Dashboard per name, once a day after the close (tools/sg_vol_capture.js -> tools/sg_vol.py)
    ("SpotGamma volatility dashboard", "SpotGamma", "sg_vol_daily", "trade_date", 1),
    # 11/15: the rest of the dashboard (tools/sg_daily_capture.js, sg_report_capture.js -> tools/sg_pages.py).
    # sg_index_daily.trade_date is the session the levels were computed from (one session behind the page date).
    ("SpotGamma dashboard pages (daily run)", "SpotGamma", "sg_page_captures", "captured_at", 1),
    ("SpotGamma index levels (full list)", "SpotGamma", "sg_index_daily", "trade_date", 2),
    ("SpotGamma reports (FlowPatrol, Opening Setup, notes)", "SpotGamma", "sg_reports", "report_date", 1),
    ("TradingView levels", "TradingView", "tv_levels", "bar_date", 0),   # last session's bar by morning
    ("Fidelity book", "Fidelity", "book_positions", "snapshot_date", 5),
    # 11/14: Capital Allocation Pro daily dashboard (tools/cap_alloc_dashboard.py); as_of = the prior
    # session, published the next morning, so 2 weekdays of lag is normal before it lands.
    ("Cap Alloc dashboard", "Hedgeye", "hedgeye_cap_alloc_dashboard", "as_of", 2),
    # 11/15 MFR (Elite Plus). Railway pulls these on its own; the capture desk owns checking them
    # and re-running a missed night by hand. snapshot = REST ranges/IV (00:02 UTC nightly);
    # indicators = Trade/Trend/cloud/targets via MCP (06:00 UTC Tue-Sat, last session's bar);
    # correlations run right after the indicators; the morning read lands 6:30 AM ET weekdays.
    ("MFR snapshot (ranges, IV)", "MFR", "mfr_snapshots", "snapshot_date", 1),
    ("MFR indicators (Trade/Trend/cloud)", "MFR", "mfr_indicators", "bar_date", 0),
    ("MFR book correlations", "MFR", "mfr_book_corr", "as_of", 0),
    ("MFR sector correlation", "MFR", "mfr_sector_corr", "bar_date", 0),
    ("Global macro morning read", "MFR/Yahoo", "global_macro_morning", "report_date", 0),
]
# Judged against the last session all day (their bar for today only exists after the close).
PRIOR_BAR_TABLES = {"tv_levels", "mfr_indicators", "mfr_book_corr", "mfr_sector_corr"}
TRANSCRIPTS = [("The Call transcript", "the_call"), ("Macro Show transcript", "macro_show")]
REPORT_BACKLOG_MAX = 60
RANGE_COVERAGE_MIN = 80.0   # % of TV-range-dependent screener names with a range on the latest bar
# 11/11: the overnight TV task "succeeded" while storing 15 rows for bar 11/10.
MIN_ROWS = {"tv_levels": 300, "hedgeye_risk_ranges": 20, "hedgeye_cap_alloc_dashboard": 50,
            "mfr_snapshots": 500, "mfr_indicators": 800, "mfr_book_corr": 40}


def _weekday_lag(last: dt.date | None, now: dt.datetime) -> int | None:
    if last is None:
        return None
    end = now.date() if now.hour >= 10 else now.date() - dt.timedelta(days=1)
    d, lag = last + dt.timedelta(days=1), 0
    while d <= end:
        lag += d.weekday() < 5
        d += dt.timedelta(days=1)
    return lag


def check(now: dt.datetime | None = None) -> list[dict]:
    import db_pg
    now = now or dt.datetime.now(ET)
    out = []
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for label, src, table, col, allowed in FEEDS:
            try:
                cur.execute("SELECT data_type FROM information_schema.columns "
                            "WHERE table_name=%s AND column_name=%s", (table, col))
                (dtype,) = cur.fetchone()
                expr = f"({col} AT TIME ZONE 'America/New_York')::date" if "with time zone" in dtype else f"{col}::date"
                if table in MIN_ROWS:   # a run that stores a handful of rows isn't a run
                    cur.execute(f"SELECT max(d) FROM (SELECT {expr} d FROM {table} GROUP BY 1 "
                                f"HAVING count(*) >= %s) x", (MIN_ROWS[table],))
                else:
                    cur.execute(f"SELECT max({expr}) FROM {table}")
                last = cur.fetchone()[0]
            except Exception as e:  # missing table/column -> report, keep going
                conn.rollback()
                out.append({"label": label, "src": src, "last": None, "lag": None, "ok": False, "err": str(e)[:80]})
                continue
            # 11/11: a TV bar for today only exists after the close (the night run
            # stores it), so judge tv_levels against yesterday all day long.
            lag = _weekday_lag(last, now.replace(hour=0) if table in PRIOR_BAR_TABLES else now)
            out.append({"label": label, "src": src, "last": last, "lag": lag,
                        "ok": lag is not None and lag <= allowed})
    # 11/14 FRED (tools/fred_load.py). FRED itself is a business day late: ICE credit posts
    # ~9 AM for yesterday, Treasury yields ~3:15 PM for yesterday. So 1 day of lag is normal for
    # credit, 2 for Treasuries until the afternoon load.
    try:   # 11/14: the CPI/PPI/jobs calendar must always reach at least 3 weeks ahead
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(release_date), max(fetched_at)::date FROM macro_calendar")
            far, got = cur.fetchone()
        ok = far is not None and far >= now.date() + dt.timedelta(days=21) and got is not None and (now.date() - got).days <= 7
        out.append({"label": "Macro calendar (CPI/PPI/jobs dates)", "src": "FRED", "last": f"through {far}, loaded {got}",
                    "lag": None if ok else 1, "ok": ok})
    except Exception as e:
        out.append({"label": "Macro calendar (CPI/PPI/jobs dates)", "src": "FRED", "last": None, "lag": None, "ok": False, "err": str(e)[:80]})
    for label, sid, allowed in (("FRED credit spreads (HY OAS)", "BAMLH0A0HYM2", 1),
                                ("FRED Treasury yields (10Y)", "DGS10", 2)):
        try:
            with db_pg.get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT max(obs_date) FROM fred_observations WHERE series_id=%s", (sid,))
                last = cur.fetchone()[0]
            lag = _weekday_lag(last, now)
            out.append({"label": label, "src": "FRED", "last": last, "lag": lag,
                        "ok": lag is not None and lag <= allowed})
        except Exception as e:
            out.append({"label": label, "src": "FRED", "last": None, "lag": None, "ok": False, "err": str(e)[:80]})
    # 11/15: the MFR "track" group (Buy/Sell, BTFD, Momentum, Vola, Matrix) runs after "core" in
    # the same nightly job, so a core-only night still shows the indicators line ok. Check it alone.
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(bar_date) FROM (SELECT bar_date FROM mfr_indicators "
                        "WHERE track_fetched_at IS NOT NULL GROUP BY 1 HAVING count(*) >= %s) x",
                        (MIN_ROWS["mfr_indicators"],))
            last = cur.fetchone()[0]
        lag = _weekday_lag(last, now.replace(hour=0))
        out.append({"label": "MFR signals (track group)", "src": "MFR", "last": last, "lag": lag,
                    "ok": lag is not None and lag <= 0})
    except Exception as e:
        out.append({"label": "MFR signals (track group)", "src": "MFR", "last": None, "lag": None, "ok": False, "err": str(e)[:80]})
    # 11/11: "MFR Range" stopped landing 11/9 while TRADE/TREND kept arriving, so the
    # TradingView row count looked fine. Check range coverage for the names that lean
    # on TV ranges (band_source 'tv' or null) on the latest full TV bar, any capture.
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(bar_date) FROM (SELECT bar_date FROM tv_levels GROUP BY 1 "
                        "HAVING count(*) >= %s) x", (MIN_ROWS["tv_levels"],))
            bar = cur.fetchone()[0]
            # Asian listings carry the next session's date by US morning, so bar or bar+1
            cur.execute("SELECT count(*), count(*) FILTER (WHERE EXISTS (SELECT 1 FROM tv_levels t "
                        "  WHERE t.ticker = s.ticker AND t.range_high IS NOT NULL "
                        "  AND t.bar_date BETWEEN %s AND %s::date + 1)) FROM v_screener s "
                        "WHERE s.band_source IS NULL OR s.band_source = 'tv'", (bar, bar))
            need, have = cur.fetchone()
        pct = 100.0 * have / need if need else 100.0
        out.append({"label": "TradingView MFR ranges", "src": "TradingView",
                    "last": f"{have}/{need} on {bar}", "lag": round(100 - pct),
                    "ok": pct >= RANGE_COVERAGE_MIN})
    except Exception as e:
        out.append({"label": "TradingView MFR ranges", "src": "TradingView", "last": None,
                    "lag": None, "ok": False, "err": str(e)[:80]})
    if now.weekday() < 5 and (now.hour, now.minute) >= (11, 45):
        for label, folder in TRANSCRIPTS:
            f = ROOT / "data" / "transcripts" / folder / f"{now.date()}.txt"
            out.append({"label": label, "src": "Hedgeye", "last": now.date() if f.exists() else None,
                        "lag": 0 if f.exists() else 1, "ok": f.exists()})
    try:
        r = subprocess.run([sys.executable, "-m", "tools.report_capture", "status"], cwd=ROOT,
                           capture_output=True, text=True, timeout=120)
        m = re.search(r"(\d+) reports not captured", r.stdout)
        n = int(m.group(1)) if m else 0
        out.append({"label": "Full-report capture backlog", "src": "Hedgeye", "last": f"{n} waiting",
                    "lag": n, "ok": n <= REPORT_BACKLOG_MAX})
    except Exception as e:
        out.append({"label": "Full-report capture backlog", "src": "Hedgeye", "last": None,
                    "lag": None, "ok": False, "err": str(e)[:80]})
    # 11/15 (Kris "everything ... recoverable"): nightly full database backup to the private Drive folder
    # (tools/db_backup.py, capture desk evening block). Older than ~36 h = missed a night.
    try:
        from tools.db_backup import backups, DEFAULT_DIR
        bl = backups(DEFAULT_DIR)
        if bl:
            stamp = dt.datetime.strptime(os.path.basename(bl[-1])[3:16], "%Y%m%d_%H%M")
            age_h = (now.replace(tzinfo=None) - stamp).total_seconds() / 3600
            out.append({"label": "Database backup (Drive)", "src": "Railway DB", "last": f"{stamp:%m/%d %H:%M}, {age_h:.0f} h old",
                        "lag": round(age_h / 24, 1), "ok": age_h <= 36})
        else:
            out.append({"label": "Database backup (Drive)", "src": "Railway DB", "last": None, "lag": None, "ok": False,
                        "err": "no backup in " + DEFAULT_DIR})
    except Exception as e:  # noqa: BLE001
        out.append({"label": "Database backup (Drive)", "src": "Railway DB", "last": None, "lag": None, "ok": False,
                    "err": str(e)[:80]})
    out.extend(_tape_age_check(now))
    out.extend(_capture_desk_checks(now))
    out.extend(_content_checks())
    return out


def _tape_age_check(now: dt.datetime) -> list[dict]:
    """11/20: the day-level tape check said "ok" while the 15-min reads had stopped for 2 h
    (09:54 -> 12:08). In market hours on a weekday the newest read must be < 25 min old."""
    if now.weekday() >= 5 or not (dt.time(9, 50) <= now.time() <= dt.time(16, 20)):
        return []
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(read_at) FROM sg_tape_reads")
            last = cur.fetchone()[0]
        if last is None:
            return [{"label": "SpotGamma tape (intraday age)", "src": "SpotGamma", "last": None, "lag": None, "ok": False}]
        age = (now - last.astimezone(ET)).total_seconds() / 60
        return [{"label": "SpotGamma tape (intraday age)", "src": "SpotGamma",
                 "last": f"{last.astimezone(ET):%H:%M}, {age:.0f} min old", "lag": round(age), "ok": age < 25}]
    except Exception as e:  # noqa: BLE001
        return [{"label": "SpotGamma tape (intraday age)", "src": "SpotGamma", "last": None, "lag": None, "ok": False,
                 "err": str(e)[:80]}]


def _capture_desk_checks(now: dt.datetime) -> list[dict]:
    """11/14 capture desk: sources with no table of their own (portal videos, sector notes,
    weekly quad forecast, stalk ledger). Items from today count only after 4 PM ET."""
    out = []
    try:
        from tools import capture_inventory as ci
        rows = ci.build(14)
        cut = now.date().isoformat() if now.hour >= 16 else (now.date() - dt.timedelta(days=1)).isoformat()
        recent = [r for r in ci.gaps(rows, 4) if r["date"] <= cut]
        vids = [r for r in recent if "transcript" in r["needs"]]
        reps = [r for r in recent if "report" in r["needs"]]
        out.append({"label": "Portal videos transcribed", "src": "Hedgeye",
                    "last": f"{len(vids)} missing (4d)", "lag": len(vids), "ok": not vids})
        out.append({"label": "Full reports incl. portal-only", "src": "Hedgeye",
                    "last": f"{len(reps)} missing (4d)", "lag": len(reps), "ok": len(reps) <= 5})
        age = (dt.datetime.now().timestamp() - ci.PORTAL.stat().st_mtime) / 86400 if ci.PORTAL.exists() else None
        out.append({"label": "Portal listing", "src": "Hedgeye",
                    "last": f"{age:.1f} d old" if age is not None else None,
                    "lag": None if age is None else int(age), "ok": age is not None and age < 1.5})
    except Exception as e:
        out.append({"label": "Capture inventory", "src": "Hedgeye", "last": None, "lag": None,
                    "ok": False, "err": str(e)[:80]})
    # Weekly quad forecast: Friday ~5 PM Macro Week Summary Notes -> *_week_notes/monthly_quad_forecast.csv
    fri = now.date() - dt.timedelta(days=(now.weekday() - 4) % 7)
    if fri == now.date() and now.hour < 19:
        fri -= dt.timedelta(days=7)
    have = sorted(p.parent.name[:10] for p in (ROOT / "data" / "hedgeye" / "research").glob("*_week_notes/monthly_quad_forecast.csv"))
    last = have[-1] if have else None
    out.append({"label": "Weekly quad forecast", "src": "Hedgeye", "last": last, "lag": None,
                "ok": bool(last) and last >= fri.isoformat()})
    # 11/19: the weekly Position Monitor (Monday "Week of M/D/YYYY: Position Monitors Update" email -> PDF ->
    # _pm_ingest.py --commit -> bucket_history) had no check; the 11/16 week never went in and nothing flagged it.
    # Stale when a newer update email than the last ingested week has been in for more than a day.
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(effective_date) FROM bucket_history")
            pm_last = cur.fetchone()[0]
            cur.execute("SELECT max(received_at) FROM hedgeye_emails_raw "
                        "WHERE subject ILIKE '%%Position Monitors Update%%'")
            pm_mail = cur.fetchone()[0]
        mail_day = pm_mail.astimezone(ZoneInfo("America/New_York")).date() if pm_mail else None
        behind = bool(mail_day and pm_last and mail_day > pm_last + dt.timedelta(days=1))
        late = behind and (now - pm_mail.astimezone(now.tzinfo)).total_seconds() > 86400
        out.append({"label": "Position Monitor (weekly buckets)", "src": "Hedgeye",
                    "last": f"week of {pm_last}" + (f"; update email {mail_day} not ingested" if behind else ""),
                    "lag": (mail_day - pm_last).days if behind else 0, "ok": not late})
    except Exception as e:
        out.append({"label": "Position Monitor (weekly buckets)", "src": "Hedgeye", "last": None, "lag": None,
                    "ok": False, "err": str(e)[:80]})
    # TradingView global macro list (Kris 11/14): 47 countries' index/10Y/FX + commodities, crypto, vol.
    # A miss here matters as much as a stock miss. Known TradingView gaps are listed, not hidden.
    try:
        import db_pg
        from tools import macro_symbols as ms
        keys = {v[0] for d in (ms.INDEX_TV, ms.YIELD_TV, ms.FX_TV) for v in d.values()} - {"USD"}   # USD = 2x semis; dollar = DXY
        keys |= {"WTIC", "BRENT", "NATGAS", "COPPER", "GOLD", "SILVER", "BTC", "ETH", "VIX", "^MOVE", "DXY"}
        known = {"SPBLPGPT", "MERV"}    # 11/14: Peru index unresolved; Merval data stops 10/16 on TradingView
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT max(bar_date) FROM (SELECT bar_date FROM tv_levels GROUP BY 1 HAVING count(*) >= 300) x")
            bar = cur.fetchone()[0]
            cur.execute("SELECT DISTINCT ticker FROM tv_levels WHERE bar_date BETWEEN %s::date - 2 AND %s::date + 3 "
                        "AND ticker = ANY(%s)", (bar, bar, list(keys)))
            have = {r[0] for r in cur.fetchall()}
            # 11/19: a foreign index / yield / currency one trading day behind is a local holiday (11/16: Mexico's
            # Revolution Day left IPC, MX10Y and COLCAP on 11/13), not a capture miss. Shown as late, still ok.
            foreign = {v[0] for d in (ms.INDEX_TV, ms.YIELD_TV, ms.FX_TV) for k, v in d.items() if k != "US"}
            prev = bar - dt.timedelta(days=3 if bar.weekday() == 0 else 1)
            cur.execute("SELECT DISTINCT ticker FROM tv_levels WHERE bar_date >= %s "
                        "AND ticker = ANY(%s)", (prev, sorted((keys & foreign) - have)))
            late = {r[0] for r in cur.fetchall()}
        missing = sorted(keys - have - known - late)
        out.append({"label": "TradingView macro list", "src": "TradingView",
                    "last": f"{len(have)}/{len(keys)} on {bar}" + (f"; missing {','.join(missing[:8])}" if missing else "")
                            + (f"; 1 day late (holiday?) {','.join(sorted(late)[:8])}" if late else ""),
                    "lag": len(missing), "ok": not missing})
    except Exception as e:
        out.append({"label": "TradingView macro list", "src": "TradingView", "last": None, "lag": None,
                    "ok": False, "err": str(e)[:80]})
    # QUAD_STATUS.md "Now" must match the latest dashboard read stored in hedgeye_quad (Kris 11/14)
    try:
        import db_pg
        txt = (ROOT / "docs" / "playbook" / "QUAD_STATUS.md").read_text(encoding="utf-8")
        m = re.search(r"## Now:.*?updated (\d{1,2})/(\d{1,2})/(\d{2,4})", txt)
        doc = dt.date(2000 + int(m.group(3)) % 100, int(m.group(1)), int(m.group(2))) if m else None
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT as_of_raw FROM hedgeye_quad WHERE source='dashboard_view' ORDER BY as_of DESC LIMIT 1")
            raw = (cur.fetchone() or [""])[0] or ""
        m2 = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", raw)
        dash = dt.date(2000 + int(m2.group(3)) % 100, int(m2.group(1)), int(m2.group(2))) if m2 else None
        out.append({"label": "QUAD_STATUS.md current", "src": "Hedgeye", "last": f"doc {doc} / dashboard {dash}",
                    "lag": None, "ok": bool(doc and dash and doc >= dash)})
    except Exception as e:
        out.append({"label": "QUAD_STATUS.md current", "src": "Hedgeye", "last": None, "lag": None,
                    "ok": False, "err": str(e)[:80]})
    # ETF Pro bias the readers show vs the capture desk's daily ETF Pro Plus dashboard export (11/19: QAT,
    # dropped 11/2, kept showing "short" to the desk from a stale week). Mismatch counts only when no change
    # email was parsed after the export (a mid-day change is fine until the 4:31 PM export).
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("""
                WITH d AS (SELECT ticker, lower(position) AS bias, loaded_at FROM hedgeye_dashboard_positions
                            WHERE product='etf_pro_plus' AND as_of=(SELECT max(as_of) FROM hedgeye_dashboard_positions
                                                                     WHERE product='etf_pro_plus')),
                     e AS (SELECT ticker, bias, parsed_at FROM hedgeye_etf_pro_ranges
                            WHERE week_of=(SELECT max(week_of) FROM hedgeye_etf_pro_ranges))
                SELECT (SELECT string_agg(e.ticker||':'||coalesce(e.bias,'?'), ',' ORDER BY e.ticker) FROM e
                         WHERE NOT EXISTS (SELECT 1 FROM d WHERE d.ticker=e.ticker AND d.bias=e.bias)),
                       (SELECT string_agg(d.ticker||':'||d.bias, ',' ORDER BY d.ticker) FROM d
                         WHERE NOT EXISTS (SELECT 1 FROM e WHERE e.ticker=d.ticker AND e.bias=d.bias)),
                       (SELECT max(as_of) FROM hedgeye_dashboard_positions WHERE product='etf_pro_plus'),
                       (SELECT max(parsed_at) FROM e) > (SELECT max(loaded_at) FROM d)""")
            extra, missing, as_of, newer = cur.fetchone()
        bad = bool(extra or missing)
        txt = f"vs dashboard {as_of}: " + ("match" if not bad else
               "; ".join(x for x in (f"bot only {extra}" if extra else "", f"dashboard only {missing}" if missing else "") if x))
        out.append({"label": "ETF Pro bias = dashboard", "src": "Hedgeye",
                    "last": txt[:120] + (" (change email after export)" if bad and newer else ""),
                    "lag": None, "ok": (not bad) or bool(newer)})
    except Exception as e:
        out.append({"label": "ETF Pro bias = dashboard", "src": "Hedgeye", "last": None, "lag": None,
                    "ok": False, "err": str(e)[:80]})
    # BUXX_STATUS.md: from the 20th, checked (rewritten) every day until the month's ex-date is past (Kris 11/14)
    if now.day >= 20:
        f = ROOT / "docs" / "playbook" / "BUXX_STATUS.md"
        age = (dt.datetime.now().timestamp() - f.stat().st_mtime) / 86400 if f.exists() else None
        out.append({"label": "BUXX_STATUS.md checked today", "src": "Public",
                    "last": f"{age:.1f} d old" if age is not None else None, "lag": None,
                    "ok": age is not None and age < 1})
    # Stalk ledger: sector name calls logged
    try:
        import csv as _csv
        with open(ROOT / "data" / "stalk" / "ledger.csv", encoding="utf-8") as f:
            dates = [r[0] for r in _csv.reader(f) if r and re.match(r"\d{4}-\d\d-\d\d", r[0])]
        last = dt.date.fromisoformat(max(dates)) if dates else None
        lag = _weekday_lag(last, now)
        out.append({"label": "Stalk ledger (sector calls)", "src": "Hedgeye", "last": last, "lag": lag,
                    "ok": lag is not None and lag <= 2})
    except Exception as e:
        out.append({"label": "Stalk ledger (sector calls)", "src": "Hedgeye", "last": None, "lag": None,
                    "ok": False, "err": str(e)[:80]})
    return out


def verdict(rows: list[dict], now: dt.datetime) -> str:
    bad = [r for r in rows if not r["ok"]]
    if not bad:
        return f"Feeds {now:%H:%M}: all {len(rows)} fresh"
    parts = [f"{r['label']} (last {r['last'] or r.get('err', 'none')})" for r in bad]
    return f"Feeds {now:%H:%M}: {len(bad)} STALE: " + "; ".join(parts)


ALERT_STATE = ROOT / "data" / "feed_alert_state.json"


def alert_message(rows: list[dict], now: dt.datetime, state_path: pathlib.Path = ALERT_STATE) -> str | None:
    """Feed-down alert (Kris 11/22: "an alert that goes to telegram if any feed is down").
    Texts only on a CHANGE, so it can run after every capture block without spamming:
      - a feed is STALE on two runs in a row (one stale run can be a job still landing) -> DOWN
      - a feed that was alerted DOWN is ok again -> BACK
    State is a small local file: {label: {"runs": n, "alerted": bool, "last": str}}."""
    import json
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        state = {}
    down, back, new_state = [], [], {}
    for r in rows:
        prev = state.get(r["label"], {})
        if r["ok"]:
            if prev.get("alerted"):
                back.append(r["label"])
            continue
        runs = int(prev.get("runs", 0)) + 1
        alerted = bool(prev.get("alerted"))
        if runs >= 2 and not alerted:
            down.append(f"{r['label']} (last {r['last'] or r.get('err', 'none')})")
            alerted = True
        new_state[r["label"]] = {"runs": runs, "alerted": alerted, "last": str(r["last"])}
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(new_state, indent=1), encoding="utf-8")
    if not down and not back:
        return None
    parts = []
    if down:
        parts.append(f"FEED DOWN {now:%H:%M} ET: " + "; ".join(down))
    if back:
        parts.append(f"FEED BACK {now:%H:%M} ET: " + "; ".join(back))
    still = sum(1 for v in new_state.values() if v["alerted"]) - len(down)
    if still > 0:
        parts.append(f"({still} other feed(s) still down)")
    return "\n".join(parts)


def db_rows(now: dt.datetime) -> list[dict]:
    """Only the feeds that live in the database (the FEEDS list and the intraday tape age).
    Safe on Railway: no local files, no laptop paths."""
    import db_pg
    out = []
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for label, src, table, col, allowed in FEEDS:
            try:
                cur.execute("SELECT data_type FROM information_schema.columns "
                            "WHERE table_name=%s AND column_name=%s", (table, col))
                (dtype,) = cur.fetchone()
                expr = f"({col} AT TIME ZONE 'America/New_York')::date" if "with time zone" in dtype else f"{col}::date"
                if table in MIN_ROWS:
                    cur.execute(f"SELECT max(d) FROM (SELECT {expr} d FROM {table} GROUP BY 1 "
                                f"HAVING count(*) >= %s) x", (MIN_ROWS[table],))
                else:
                    cur.execute(f"SELECT max({expr}) FROM {table}")
                last = cur.fetchone()[0]
            except Exception as e:
                conn.rollback()
                out.append({"label": label, "src": src, "last": None, "lag": None, "ok": False, "err": str(e)[:80]})
                continue
            lag = _weekday_lag(last, now.replace(hour=0) if table in PRIOR_BAR_TABLES else now)
            out.append({"label": label, "src": src, "last": last, "lag": lag,
                        "ok": lag is not None and lag <= allowed})
    out.extend(_tape_age_check(now))
    return out


DB_LABELS = {f[0] for f in FEEDS} | {"SpotGamma tape (intraday age)"}


def run_server_alert(now: dt.datetime | None = None) -> str:
    """Called by the always-on bot (main.py) every 15 minutes, so a dead feed is texted
    even when the laptop and the capture desk are off (Kris 11/22). Weekdays 7:15 AM to
    8:00 PM ET only: no Hedgeye mail lands overnight, so a night check would only make noise."""
    now = now or dt.datetime.now(ET)
    if now.weekday() >= 5 or not (dt.time(7, 15) <= now.time() <= dt.time(20, 0)):
        return "quiet hours"
    vol = pathlib.Path("/data")
    state = (vol if vol.is_dir() else ROOT / "data") / "feed_alert_state_server.json"
    msg = alert_message(db_rows(now), now, state)
    if msg is None:
        return "no change"
    from notifier import send_telegram
    return "sent" if send_telegram("Feed alert", msg) else "send failed"


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="text the verdict to Telegram")
    ap.add_argument("--alert", action="store_true",
                    help="text Telegram only when a feed goes down (2 runs in a row) or comes back")
    ap.add_argument("--alert-dry", action="store_true", help="like --alert but print instead of texting")
    a = ap.parse_args()
    import db_pg
    db_pg._load_dotenv_fallback()
    now = dt.datetime.now(ET)
    rows = check(now)
    for r in rows:
        mark = "ok   " if r["ok"] else "STALE"
        lag = "" if r["lag"] is None else f"lag {r['lag']}"
        print(f"{mark} {r['src']:12} {r['label']:30} last {r['last']}  {lag}")
    v = verdict(rows, now)
    print(v)
    if a.send:
        from parser_tms_deck import load_telegram_env
        load_telegram_env()     # the laptop .env has no Telegram settings
        from notifier import send_telegram
        if not send_telegram("Feed health", v):
            print("Telegram send failed")
            return 1
    if a.alert or a.alert_dry:
        # The bot on Railway texts the database feeds itself (run_server_alert); the laptop
        # only covers what lives on this computer, so a dead feed is texted once, not twice.
        local = rows if os.getenv("FEED_ALERT_ALL") else [r for r in rows if r["label"] not in DB_LABELS]
        msg = alert_message(local, now)
        if msg is None:
            print("feed alert: no change")
        elif a.alert_dry:
            print("feed alert (dry):\n" + msg)
        else:
            from parser_tms_deck import load_telegram_env
            load_telegram_env()
            from notifier import send_telegram
            ok = send_telegram("Feed alert", msg)
            print("feed alert sent" if ok else "feed alert: Telegram send failed")
            if not ok:
                return 1
    return 0


def _content_checks() -> list[dict]:
    """11/14 (Kris: "I need this to be reliable"): saved is not captured. tools/capture_audit
    finds viewer shells, report chart/table images never downloaded, image-only PDFs and
    structured tables that stopped. Each kind is its own line; any count above 0 is not ok."""
    try:
        from tools import capture_audit
        r = capture_audit.audit(14)
    except Exception as e:
        return [{"label": "Content audit", "src": "Capture", "last": None, "lag": None, "ok": False, "err": str(e)[:80]}]
    c = r["counts"]
    stale = [x["table"] for x in r["tables"] if not x["ok"]]
    rows = [("Content: viewer shells (real file not fetched)", c["viewer_shells"]),
            ("Content: report pages with unread chart/table images", c["pages_with_unread_images"]),
            ("Content: image-only PDFs not read", c["image_only_pdfs"]),
            ("Content: stale or missing data tables" + (f" ({', '.join(stale)})" if stale else ""), c["stale_or_missing_tables"])]
    return [{"label": lbl, "src": "Capture", "last": f"{n} open", "lag": n, "ok": n == 0} for lbl, n in rows]


if __name__ == "__main__":
    raise SystemExit(_cli())

