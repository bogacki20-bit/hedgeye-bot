"""capture_audit.py: is the CONTENT really in the bot, not just a file? (Kris, 11/14/2026)

The 11/14 hand audit found holes the capture desk had marked done: report charts and tables
saved as images never downloaded, viewer shells instead of the real file, image-only PDFs,
and structured tables that quietly stopped updating. This runs that audit mechanically, and
tools/feed_health.py shows its counts, so "all ok" means the data is in.

    py -m tools.capture_audit            # summary + worst offenders
    py -m tools.capture_audit --days 14  # look-back for report files (default 14)
    py -m tools.capture_audit --json

Read-only.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "data" / "hedgeye" / "research"
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# (table, date column, weekdays of lag allowed). Structured data the desk relies on.
# Weekly sources get 5-7; daily ones 1-2. Tables in DATA_INVENTORY's "stalled" list are
# here on purpose, so they keep showing until they're restarted or retired there.
TABLES = [
    ("hedgeye_risk_ranges", "signal_date", 1), ("hedgeye_signal_strength", "snapshot_date", 1),
    ("hedgeye_rta", "signal_date", 2), ("hedgeye_the_call", "signal_date", 1),
    ("tms_decks", "show_date", 1), ("hedgeye_portfolio_solutions", "snapshot_date", 1),
    ("hedgeye_etf_pro_ranges", "week_of", 7), ("hedgeye_keiths_signals", "signal_date", 7),
    ("hedgeye_quad", "as_of", 5), ("hedgeye_country_quads", "read_date", 5),
    ("hedgeye_momo", "signal_date", 2), ("hedgeye_crypto_quant", "signal_date", 2),
    ("hedgeye_hedgai", "signal_date", 3), ("hedgeye_retail", "signal_date", 3),
    ("hedgeye_financials", "signal_date", 5), ("hedgeye_inflation_nowcast", "signal_date", 25),
    ("t1a_snapshots", "report_date", 1), ("sg_index_levels", "note_date", 1),
    ("sg_tilt", "trade_date", 1), ("spotgamma_snapshots", "snapshot_date", 1),
    ("fred_observations", "obs_date", 2), ("tv_levels", "bar_date", 1),
    ("hedgeye_trend_daily", "date", 2),   # quad_nowcast_daily retired 11/14 (one-off ML table; hedgeye_quad + QUAD_STATUS replace it)
    ("hedgeye_cap_alloc_dashboard", "as_of", 2), ("hedgeye_cap_alloc_country_quads", "as_of", 2),
    ("hedgeye_monthly_trend_tail", "as_of", 25), ("hedgeye_trend_changes", "change_date", 2),   # 11/14 dashboard pages
    ("hedgeye_gip_model", "as_of", 7), ("hedgeye_monthly_quad_probs", "as_of", 7),
    ("hedgeye_dashboard_positions", "as_of", 2), ("hedgeye_rta_archive", "close_ts", 5),
    ("hedgeye_risk_range_outbucket", "as_of", 2), ("hedgeye_the_call_analyst", "as_of", 2),
    ("hedgeye_early_look", "publish_date", 1), ("hedgeye_macro_show_notes", "publish_date", 1), ("hedgeye_quad_nowcast", "publish_date", 7),
]


# Event-driven tables fill only when one kind of email arrives (earnings recaps run in season).
# They count as stale only when a matching email came in after the newest stored row
# (11/17: hedgeye_financials flagged STALE in the off-season, though nothing was missed).
EVENT_SUBJECTS = {
    "hedgeye_financials": r"Financials?[[:space:]].*Earnings[[:space:]]+Recap",
}


def _weekday_lag(last: dt.date | None, today: dt.date) -> int | None:
    if last is None:
        return None
    d, lag = last + dt.timedelta(days=1), 0
    while d < today:
        lag += d.weekday() < 5
        d += dt.timedelta(days=1)
    return lag


def scan_files(days: int) -> dict:
    cut = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    shells, img_pages, pdf_blank = [], [], []
    # A shell is resolved when the real file was fetched and saved elsewhere under data/hedgeye
    # with the same name (e.g. data/hedgeye/cap_alloc_dashboard/<name>.html).
    fetched = {f.name for f in (ROOT / "data" / "hedgeye").rglob("*.html")
               if RESEARCH not in f.parents and f.stat().st_size > 20000}
    if not RESEARCH.exists():
        return {"shells": shells, "images": img_pages, "image_only_pdfs": pdf_blank}
    for day in RESEARCH.iterdir():
        if not (day.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name) and day.name >= cut):
            continue
        for f in day.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix == ".html" and f.stat().st_size < 20000:
                t = f.read_text(encoding="utf-8", errors="replace")
                if "attachment-viewer" in t and f.name not in fetched:
                    shells.append(str(f.relative_to(RESEARCH)))
            if f.name == "page.html":
                h = f.read_text(encoding="utf-8", errors="replace")
                n = len(re.findall(r'<img class="img-responsive"', h))
                if n >= 3 and not any(p.suffix.lower() in IMG_EXT for p in f.parent.iterdir()):
                    img_pages.append((str(f.parent.relative_to(RESEARCH)), n))
            if f.suffix == ".pdf":
                txt = f.with_suffix(".txt")
                if txt.exists() and txt.stat().st_size < 200:
                    pdf_blank.append(str(f.relative_to(RESEARCH)))
    return {"shells": sorted(shells), "images": sorted(img_pages), "image_only_pdfs": sorted(pdf_blank)}


def scan_tables() -> list[dict]:
    import db_pg
    today = dt.date.today()
    out = []
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for t, col, allowed in TABLES:
            try:
                cur.execute(f"SELECT max({col})::date, count(*) FROM {t}")
                last, n = cur.fetchone()
                lag = _weekday_lag(last, today)
                ok = bool(n) and lag is not None and lag <= allowed
                if not ok and n and t in EVENT_SUBJECTS:
                    cur.execute("SELECT count(*) FROM hedgeye_emails_raw WHERE subject ~* %s "
                                "AND received_at::date > %s", (EVENT_SUBJECTS[t], last))
                    ok = cur.fetchone()[0] == 0     # no new matching email -> nothing to store
                out.append({"table": t, "last": str(last) if last else None, "rows": n, "lag": lag,
                            "ok": ok})
            except Exception as e:
                conn.rollback()
                out.append({"table": t, "last": None, "rows": 0, "lag": None, "ok": False,
                            "err": "missing" if "does not exist" in str(e) else str(e)[:60]})
    return out


def audit(days: int = 14) -> dict:
    f = scan_files(days)
    t = scan_tables()
    return {"files": f, "tables": t,
            "counts": {"viewer_shells": len(f["shells"]), "pages_with_unread_images": len(f["images"]),
                       "image_only_pdfs": len(f["image_only_pdfs"]),
                       "stale_or_missing_tables": sum(1 for x in t if not x["ok"])}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    from db_pg import _load_dotenv_fallback
    _load_dotenv_fallback()
    r = audit(a.days)
    if a.json:
        print(json.dumps(r, indent=1, default=str))
        return 0
    c = r["counts"]
    print(f"CONTENT AUDIT (last {a.days} days): viewer shells {c['viewer_shells']} | pages with unread chart/table images "
          f"{c['pages_with_unread_images']} | image-only PDFs {c['image_only_pdfs']} | stale or missing tables {c['stale_or_missing_tables']}")
    by = collections.Counter(re.sub(r"^\d+_", "", p.split("/")[-1].split("\\")[-1])[:28] for p, _ in r["files"]["images"])
    if by:
        print("  unread images by report:", ", ".join(f"{k} x{v}" for k, v in by.most_common(8)))
    for x in r["tables"]:
        if not x["ok"]:
            print(f"  table {x['table']}: last {x['last']}, lag {x['lag']} weekdays, rows {x['rows']}" + (f" ({x['err']})" if x.get("err") else ""))
    return 0 if not any(c.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
