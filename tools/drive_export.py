"""Copy the paid data the bot holds into one folder Google Drive syncs (Kris, 11/10).

Gemini, the Claude.ai project and OpenClaw read that folder. It is RAW data:
no rule card, no scanner calls, except desk_calls_log.md, which is kept as its
own file so a reviewer can check the calls against the data.

    py -m tools.drive_export                       # -> outputs/drive_export/Hedgeye Desk
    py -m tools.drive_export --dest "G:/My Drive"  # -> G:/My Drive/Hedgeye Desk
    py -m tools.drive_export --days 30             # how far back (default 14)

DRIVE_EXPORT_DIR in the environment works like --dest. Files are copied only
when new or changed. Private subscription content: keep the Drive folder unshared.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDER = "Hedgeye Desk"

# (csv name, SQL). %(since)s = cutoff date. Account numbers are never selected.
TABLES = {
    "book_positions_latest": "SELECT account_name, symbol, description, quantity, market_value, cost_basis, snapshot_date "
                             "FROM book_positions WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions) ORDER BY account_name, symbol",
    "hedgeye_risk_ranges": "SELECT * FROM hedgeye_risk_ranges WHERE signal_date >= %(since)s ORDER BY signal_date DESC, ticker",
    "hedgeye_etf_pro_ranges": "SELECT * FROM hedgeye_etf_pro_ranges WHERE week_of >= %(since)s ORDER BY week_of DESC",
    "hedgeye_signal_strength": "SELECT * FROM hedgeye_signal_strength WHERE snapshot_date >= %(since)s ORDER BY snapshot_date DESC",
    "signal_strength_roster_current": "SELECT * FROM ss_roster_history WHERE removed_on IS NULL ORDER BY 1",
    "position_monitor_buckets": "SELECT * FROM ticker_tags WHERE hedgeye_bucket_0629 IS NOT NULL ORDER BY 1",
    "hedgeye_keiths_signals": "SELECT * FROM hedgeye_keiths_signals WHERE signal_date >= %(since)s ORDER BY signal_date DESC",
    "hedgeye_real_time_alerts": "SELECT * FROM hedgeye_rta WHERE signal_date >= %(since)s ORDER BY signal_ts DESC",
    "hedgeye_quad": "SELECT * FROM hedgeye_quad ORDER BY as_of DESC",
    "hedgeye_country_quads": "SELECT * FROM hedgeye_country_quads ORDER BY 1 DESC",
    "cap_alloc_dashboard": "SELECT * FROM hedgeye_cap_alloc_dashboard WHERE as_of >= %(since)s ORDER BY as_of DESC, asset_class, ticker",
    "hedgeye_monthly_trend_tail": "SELECT * FROM hedgeye_monthly_trend_tail WHERE as_of = (SELECT max(as_of) FROM hedgeye_monthly_trend_tail) ORDER BY ticker",
    "hedgeye_trend_changes": "SELECT * FROM hedgeye_trend_changes WHERE change_date >= %(since)s ORDER BY change_date DESC, ticker",
    "hedgeye_gip_model": "SELECT * FROM hedgeye_gip_model WHERE as_of = (SELECT max(as_of) FROM hedgeye_gip_model) ORDER BY block, metric, period",
    "hedgeye_monthly_quad_probs": "SELECT * FROM hedgeye_monthly_quad_probs WHERE as_of >= %(since)s ORDER BY as_of DESC, month",
    "hedgeye_dashboard_positions": "SELECT * FROM hedgeye_dashboard_positions WHERE as_of = (SELECT max(as_of) FROM hedgeye_dashboard_positions) ORDER BY product, rank NULLS LAST, ticker",
    "hedgeye_rta_archive_recent": "SELECT * FROM hedgeye_rta_archive WHERE open_ts >= now() - interval '365 days' OR close_ts IS NULL ORDER BY open_ts DESC",
    "hedgeye_risk_range_outbucket": "SELECT * FROM hedgeye_risk_range_outbucket WHERE as_of = (SELECT max(as_of) FROM hedgeye_risk_range_outbucket) ORDER BY removed_date DESC",
    "hedgeye_the_call_analyst": "SELECT * FROM hedgeye_the_call_analyst WHERE as_of >= %(since)s ORDER BY as_of DESC, ticker",
    "hedgeye_quad_nowcast": "SELECT * FROM hedgeye_quad_nowcast WHERE publish_date >= %(since)s ORDER BY publish_date DESC, horizon_quarter",
    "cap_alloc_country_quads": "SELECT * FROM hedgeye_cap_alloc_country_quads WHERE as_of >= %(since)s ORDER BY as_of DESC, country, period",
    "hedgeye_inflation_nowcast": "SELECT * FROM hedgeye_inflation_nowcast WHERE signal_date >= %(since)s ORDER BY signal_date DESC",
    "research_digest": "SELECT * FROM research_digest WHERE signal_date >= %(since)s ORDER BY signal_date DESC",
    "fred_observations": "SELECT m.grp, m.label, o.series_id, o.obs_date, o.value FROM fred_observations o JOIN fred_series_meta m USING (series_id) "
                         "WHERE o.obs_date >= %(since)s ORDER BY m.grp, o.series_id, o.obs_date DESC",
    "research_calls": "SELECT * FROM research_calls ORDER BY 1 DESC LIMIT 3000",
    "t1a_market_situation": "SELECT * FROM t1a_snapshots WHERE report_date >= %(since)s ORDER BY report_date DESC",
    "macro_show_decks": "SELECT * FROM tms_decks WHERE show_date >= %(since)s ORDER BY show_date DESC",
    "spotgamma_index_levels": "SELECT * FROM sg_index_levels WHERE note_date >= %(since)s ORDER BY note_date DESC, sym",
    "tradingview_levels_latest": "SELECT DISTINCT ON (ticker) ticker, bar_date, tv_symbol, bar_close, mfr_trend_lower, mfr_trend_upper, "
                                 "vola_a, vola_b, range_low, range_high, lt_range_low, lt_range_high "
                                 "FROM tv_levels ORDER BY ticker, bar_date DESC",
    # 11/15 MFR platform (Railway nightly): Trade/Trend/cloud/targets + snapshot range/IV, book
    # correlations, sector correlation and the morning global macro read.
    "mfr_levels_latest": "SELECT i.ticker, i.bar_date, s.price, i.vola_trade, i.vola_trend, i.vola_bear, i.trend_bear, i.trend_bull, "
                         "s.range_low, s.range_high, s.trend_signal, s.momentum_signal, s.iv, s.rv, "
                         "i.tm_open, i.tm_up1, i.tm_dn1, i.tq_open, i.tq_up1, i.tq_dn1, i.ty_open, i.ty_up1, i.ty_dn1 "
                         "FROM v_mfr_indicators_latest i LEFT JOIN LATERAL (SELECT * FROM mfr_snapshots m WHERE m.ticker = i.ticker "
                         "ORDER BY snapshot_date DESC LIMIT 1) s ON true ORDER BY i.ticker",
    "mfr_book_correlations": "SELECT * FROM mfr_book_corr WHERE as_of = (SELECT max(as_of) FROM mfr_book_corr) ORDER BY abs(market_value) DESC",
    "mfr_book_corr_summary": "SELECT * FROM v_mfr_book_corr_summary ORDER BY as_of DESC",
    "mfr_book_pairs": "SELECT * FROM mfr_book_pairs WHERE as_of = (SELECT max(as_of) FROM mfr_book_pairs) ORDER BY win, corr DESC",
    "mfr_sector_corr": "SELECT * FROM mfr_sector_corr WHERE bar_date >= %(since)s ORDER BY bar_date DESC",
    "mfr_pair_corr": "SELECT * FROM mfr_pair_corr WHERE as_of >= %(since)s ORDER BY as_of DESC, a, b",
    "global_macro_morning": "SELECT report_date, headline, body FROM global_macro_morning WHERE report_date >= %(since)s ORDER BY report_date DESC",
}

README = """# Hedgeye Desk: raw data folder

Written by the bot (tools/drive_export.py). Raw subscription data, no desk rules.

- desk_snapshot_raw.md: current book + every line/range per position (start here)
- desk_calls_log.md: the desk's calls, kept separate so they can be checked against the data
- tables/: CSV exports (book, Hedgeye risk ranges, ETF Pro, Signal Strength, Position Monitor,
  Keith's signals, Real-Time Alerts, quads, research digest, Tier 1 Alpha, Macro Show decks,
  SpotGamma index levels, TradingView MFR/Vola2vola values)
- transcripts/: The Call and The Macro Show, one file per day
- process_notes/: show notes written from the transcripts
- reports/<date>/: full Hedgeye reports (text, PDFs, chart pages) behind each email
- spotgamma/: Founder's Note call log

Updated {stamp}.
"""


def _copy(src: pathlib.Path, dst: pathlib.Path) -> bool:
    if dst.exists() and dst.stat().st_size == src.stat().st_size and dst.stat().st_mtime >= src.stat().st_mtime - 2:   # Drive for desktop rounds mtimes
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _dated(name: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(name[:10])
    except ValueError:
        return None


def export(dest: pathlib.Path, days: int) -> dict:
    import db_pg
    out = dest / FOLDER
    since = dt.date.today() - dt.timedelta(days=days)
    n = {"tables": 0, "files": 0, "errors": []}

    # tables
    (out / "tables").mkdir(parents=True, exist_ok=True)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for name, sql in TABLES.items():
            try:
                cur.execute(sql, {"since": since})
                cols = [d[0] for d in cur.description]
                with open(out / "tables" / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(cols)
                    w.writerows(cur.fetchall())
                n["tables"] += 1
            except Exception as e:
                conn.rollback()
                n["errors"].append(f"{name}: {str(e)[:80]}")

    # single files
    singles = [(ROOT / "outputs" / "desk_snapshot" / "desk_snapshot_raw.md", out / "desk_snapshot_raw.md"),
               (ROOT / "outputs" / "desk_calls_log.md", out / "desk_calls_log.md"),
               (ROOT / "data" / "sg_eval" / "founders_note_calls.md", out / "spotgamma" / "founders_note_calls.md")]
    for src, dst in singles:
        if src.exists():
            n["files"] += _copy(src, dst)

    # transcripts
    for show in ("the_call", "macro_show"):
        for f in (ROOT / "data" / "transcripts" / show).glob("*.txt"):
            d = _dated(f.name)
            if d and d >= since:
                n["files"] += _copy(f, out / "transcripts" / show / f.name)
    # 11/14 process audit: Keith's monthly Q&As and coaching lessons, all dates (small, rarely change)
    for show in ("macro_qa", "coaching_lesson"):
        for f in (ROOT / "data" / "transcripts" / show).glob("*.txt"):
            n["files"] += _copy(f, out / "transcripts" / show / f.name)

    # process notes
    for f in (ROOT / "outputs").glob("macro_show_process_notes_*.md"):
        d = _dated(f.stem.rsplit("_", 1)[-1])
        if d and d >= since:
            n["files"] += _copy(f, out / "process_notes" / f.name)

    # full reports
    research = ROOT / "data" / "hedgeye" / "research"
    for day in research.iterdir() if research.exists() else []:
        d = _dated(day.name)
        if day.is_dir() and d and d >= since and len(day.name) == 10:
            for f in day.rglob("*"):
                if f.is_file():
                    n["files"] += _copy(f, out / "reports" / f.relative_to(research))

    (out / "README.md").write_text(README.format(stamp=dt.datetime.now().strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    n["files"] += gemini_start(out)
    n["dest"] = str(out)
    return n


GEMINI_README = """START HERE (rebuilt {stamp}).
This small folder is what Gemini reads: Ask Gemini can't search the full Hedgeye Desk folder
(about 1,900 files in subfolders) and doesn't read .md files, so the key files are copied
here as .txt every time the pack refreshes.
- QUAD_STATUS.txt: where we are and where we're going (now-quads plus the next-quad forecast)
- PORTFOLIO_DIRECTION.txt: the plan, the two sleeves, open items
- RULE_CARD.txt: the rules (lines, sizing, recycling, market picture, conviction score)
- BUXX_STATUS.txt: the monthly $5,000 BUXX buy window
- MACRO_CALENDAR.txt: CPI, PPI, jobs, GDP/PCE release dates + Hedgeye's inflation nowcast
- DESK_SNAPSHOT.txt: the book with every position's range, cloud, Vola Trade and ETF Pro range
- TODAY_INDEX.txt / TODAY_FULL_TEXT.txt / TODAY_SPOTGAMMA_T1A.txt: today's captured research
The CSV tables (book, risk ranges, Signal Strength, ETF Pro, quads, FRED) are in ../tables.
"""


def gemini_start(out: pathlib.Path) -> int:
    """Kris 11/14: Gemini in Drive only reads a small folder, and not .md files. Rebuild
    00_GEMINI_START with the key files as .txt (about 10 files) on every export."""
    g = out / "00_GEMINI_START"
    g.mkdir(parents=True, exist_ok=True)
    pb = ROOT / "docs" / "playbook"
    pairs = [(pb / f"{k}.md", g / f"{k}.txt") for k in ("QUAD_STATUS", "PORTFOLIO_DIRECTION", "RULE_CARD", "BUXX_STATUS", "MACRO_CALENDAR")]
    pairs.append((ROOT / "outputs" / "desk_snapshot" / "desk_snapshot_raw.md", g / "DESK_SNAPSHOT.txt"))
    packs = sorted(p for p in (ROOT / "outputs" / "daily_pack").glob("20??-??-??") if p.is_dir()) \
        if (ROOT / "outputs" / "daily_pack").exists() else []
    if packs:
        day = packs[-1]
        for pat, name in (("00_INDEX_*.md", "TODAY_INDEX.txt"), ("10_full_text_*.md", "TODAY_FULL_TEXT.txt"),
                          ("11_spotgamma_t1a_*.md", "TODAY_SPOTGAMMA_T1A.txt")):
            hits = sorted(day.glob(pat))
            if hits:
                pairs.append((hits[-1], g / name))
    copied = 0
    for src, dst in pairs:
        if src.exists():
            copied += _copy(src, dst)
    (g / "00_README_START_HERE.txt").write_text(
        GEMINI_README.format(stamp=dt.datetime.now().strftime("%Y-%m-%d %H:%M")), encoding="utf-8")
    return copied


def _cli() -> int:
    import db_pg
    db_pg._load_dotenv_fallback()   # before argparse, so DRIVE_EXPORT_DIR in .env sets the default
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=os.environ.get("DRIVE_EXPORT_DIR") or str(ROOT / "outputs" / "drive_export"))
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    r = export(pathlib.Path(a.dest), a.days)
    print(f"{r['dest']}: {r['tables']} tables, {r['files']} files copied")
    for e in r["errors"]:
        print("ERROR", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
