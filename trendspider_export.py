r"""TrendSpider custom-symbol export job — TRENDSPIDER_ML_ROUND1_SPEC_v1 Step 1.

Exports the bot's stored MFR / volume / RS / diversification / quad features to
TrendSpider custom symbols (one symbol per feature) for the Round-1 ML
feasibility test on SPY 60-min.

Doctrine (do not violate):
  * Python owns all arithmetic; no LLM calls.
  * Read ONLY stored corpus tables — never recompute a level at export time.
  * known_at = the timestamp the stored row was WRITTEN by its producing job
    (mfr_snapshots.fetched_at / volume+rs+diversification computed_at /
    quad_regime_history.effective_at) — never the market date. Source upserts
    bump their write-timestamps, so known_at can be later than first-known;
    that is the safe direction (operator decision 5, 2026-09-04) and rows are
    deduped on (symbol, source_key) so a bumped timestamp never re-exports.
  * Corpus-first: rows land in ts_export_log before/while uploading.
  * SpotGamma features are EXCLUDED (Step-0 finding: sg tables dead since
    2026-08-06, backfill-collapsed fetched_at). MFR's own zero_gamma /
    call_wall_mfr / put_wall_mfr substitute (operator decision 1).

CLI:
  py trendspider_export.py --dry-run --backfill   # CSVs to ./ts_export/, no DB writes, no upload
  py trendspider_export.py --backfill             # stage + upload full history
  py trendspider_export.py                        # incremental (default)

Validation is loud: NaN/None-shaped values, non-positive levels, timestamps
not strictly in the past, duplicate NY-minute stamps, or a known_at that is
non-monotonic vs. source order all ABORT the run (exit 1) with the offending
rows printed. Rows that are legitimately absent in the corpus (NULL column,
trendError) are skipped with a printed per-row reason — never silently.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import time
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

import db_pg  # noqa: E402

NY = ZoneInfo("America/New_York")
UTC = timezone.utc

# No trailing slash — the slash variant 504s (TrendSpider working example,
# 2026-09-04). One symbol per POST, named in the body's "symbol" field.
DEFAULT_UPLOAD_URL = "https://charts.trendspider.com/userapi/1/data/custom_symbols"
SLEEP_BETWEEN_UPLOADS_S = 20      # rate limit: "a few calls per minute"
MAX_CSV_BYTES = 7 * 1024 * 1024   # TrendSpider upload cap
DRY_RUN_DIR = REPO_ROOT / "ts_export"

TREND_MAP = {"trendBullish": 1.0, "trendNeutral": 0.0, "trendBearish": -1.0}
DECEL_CAP = 7


# ─────────────────────────── row / result shapes ───────────────────────────

@dataclass
class Row:
    source_key: str          # dedupe identity (ISO date, or effective_at text)
    known_at: datetime       # tz-aware write timestamp of the source row
    value: float


@dataclass
class Extract:
    rows: list[Row] = field(default_factory=list)
    skips: list[tuple[str, str]] = field(default_factory=list)  # (source_key, reason)


# ─────────────────────────── extractors ───────────────────────────
#
# Each extractor takes (cur, since) where `since` is the max already-uploaded
# source_key for that symbol (or None for full history / first run) and
# returns an Extract. All SQL is SELECT-only against stored corpus tables.

def _num(raw):
    """Decimal/str/float -> float. Returns None for None."""
    return None if raw is None else float(raw)


def mfr_column(ticker: str, value_sql: str, convert=None):
    """Extractor over mfr_snapshots: one value column (or SQL expression),
    known_at = fetched_at, source_key = snapshot_date. NULL values are loud
    skips (pre-migration gaps, tickers MFR doesn't price options on, ...)."""
    def extract(cur, since) -> Extract:
        cur.execute(
            f"""
            SELECT snapshot_date::text, fetched_at, {value_sql}
              FROM mfr_snapshots
             WHERE ticker = %s AND (%s::text IS NULL OR snapshot_date::text > %s)
             ORDER BY snapshot_date
            """,
            (ticker, since, since),
        )
        out = Extract()
        for key, fetched_at, raw in cur.fetchall():
            if raw is None:
                out.skips.append((key, "NULL in source"))
                continue
            if convert is not None:
                val, reason = convert(raw)
                if reason is not None:
                    out.skips.append((key, reason))
                    continue
            else:
                val = _num(raw)
            out.rows.append(Row(key, fetched_at, val))
        return out
    return extract


def _convert_trend(raw):
    """trendBullish/Neutral/Bearish -> +1/0/-1; trendError -> loud skip
    (operator decision 6); anything else is a hard failure."""
    if raw == "trendError":
        return None, "trendError"
    if raw in TREND_MAP:
        return TREND_MAP[raw], None
    raise ValueError(f"unknown trend_signal value: {raw!r}")


def extract_decel(cur, since) -> Extract:
    """volume_snapshots SPY: -1 when distribution (price down 3d AND volume
    NOT decelerating), else decel_streak capped at DECEL_CAP (decision 4)."""
    cur.execute(
        """
        SELECT snapshot_date::text, computed_at, decel_streak,
               decelerating, price_down_3d
          FROM volume_snapshots
         WHERE ticker = 'SPY' AND (%s::text IS NULL OR snapshot_date::text > %s)
         ORDER BY snapshot_date
        """,
        (since, since),
    )
    out = Extract()
    for key, computed_at, streak, decel, down3 in cur.fetchall():
        if down3 is True and decel is False:
            out.rows.append(Row(key, computed_at, -1.0))
        elif streak is not None:
            out.rows.append(Row(key, computed_at, float(min(streak, DECEL_CAP))))
        else:
            out.skips.append((key, "NULL decel_streak and not distribution"))
    return out


def extract_corr60(cur, since) -> Extract:
    cur.execute(
        """
        SELECT snapshot_date::text, computed_at, avg_pairwise_corr
          FROM diversification_snapshots
         WHERE window_days = 60 AND universe = 'sector_spdr'
           AND (%s::text IS NULL OR snapshot_date::text > %s)
         ORDER BY snapshot_date
        """,
        (since, since),
    )
    out = Extract()
    for key, computed_at, corr in cur.fetchall():
        if corr is None:
            out.skips.append((key, "NULL avg_pairwise_corr"))
        else:
            out.rows.append(Row(key, computed_at, _num(corr)))
    return out


def extract_roro(cur, since) -> Extract:
    """rs_snapshots HYG vs TLT: the STORED 10-session ROC of the HYG/TLT
    ratio (rs_trade). The ratio LEVEL is not stored anywhere, so the stored
    ROC is exported instead (operator decision 3)."""
    cur.execute(
        """
        SELECT snapshot_date::text, computed_at, rs_trade
          FROM rs_snapshots
         WHERE ticker = 'HYG' AND benchmark = 'TLT'
           AND (%s::text IS NULL OR snapshot_date::text > %s)
         ORDER BY snapshot_date
        """,
        (since, since),
    )
    out = Extract()
    for key, computed_at, roc in cur.fetchall():
        if roc is None:
            out.skips.append((key, "NULL rs_trade"))
        else:
            out.rows.append(Row(key, computed_at, _num(roc)))
    return out


def quad_extractor(column: str):
    """quad_regime_history: 'Quad N' -> N, known_at = effective_at (decision
    7), source_key = effective_at::text (event stream, not daily)."""
    assert column in ("monthly_quad", "quarterly_quad")
    def extract(cur, since) -> Extract:
        cur.execute(
            f"""
            SELECT effective_at::text AS src_key, effective_at AS eff, {column}
              FROM quad_regime_history
             WHERE (%s::text IS NULL OR effective_at::text > %s)
             ORDER BY eff
            """,
            (since, since),
        )
        out = Extract()
        for key, effective_at, raw in cur.fetchall():
            if raw is None:
                out.skips.append((key, f"NULL {column}"))
                continue
            digits = [c for c in str(raw) if c.isdigit()]
            if len(digits) != 1 or digits[0] not in "1234":
                raise ValueError(f"unparseable quad value {raw!r} at {key}")
            out.rows.append(Row(key, effective_at, float(digits[0])))
        # Event stream: a burst of writes inside one NY minute (e.g. the
        # 2026-06-20 13:45:23/25/26 dashboard-scrape triple) is one event —
        # keep the LAST row of each minute (matches groupingMethod=last),
        # loudly skip the superseded ones. Daily extractors keep the hard
        # minute-collision failure; there a collision means corruption.
        collapsed: list[Row] = []
        for r in out.rows:
            if collapsed and _ny_minute(collapsed[-1].known_at) == _ny_minute(r.known_at):
                out.skips.append((collapsed[-1].source_key,
                                  f"superseded within same minute by {r.source_key}"))
                collapsed[-1] = r
            else:
                collapsed.append(r)
        out.rows = collapsed
        return out
    return extract


# ─────────────────────────── symbol registry ───────────────────────────
#
# bounds: (lo, hi) inclusive sanity window, hard failure outside it.
# Level symbols get lo > 0 per the spec's "levels > 0" rule.

@dataclass(frozen=True)
class Symbol:
    name: str
    extract: object
    bounds: tuple[float, float]


SYMBOLS: list[Symbol] = [
    Symbol("#MFR_SPY_LO",    mfr_column("SPY", "range_low"),            (1e-9, 1e6)),
    Symbol("#MFR_SPY_HI",    mfr_column("SPY", "range_high"),           (1e-9, 1e6)),
    Symbol("#MFR_SPY_TREND", mfr_column("SPY", "trend_signal", _convert_trend), (-1, 1)),
    Symbol("#MFR_SPY_HURST", mfr_column("SPY", "hurst"),                (1e-9, 1.0)),
    Symbol("#MFR_SPY_IVPD",  mfr_column("SPY", "(full_payload->>'ivpd')::numeric"), (-10, 10)),
    Symbol("#MFR_SPY_ZG",    mfr_column("SPY", "zero_gamma"),           (1e-9, 1e6)),
    Symbol("#MFR_SPY_CW",    mfr_column("SPY", "call_wall_mfr"),        (1e-9, 1e6)),
    Symbol("#MFR_SPY_PW",    mfr_column("SPY", "put_wall_mfr"),         (1e-9, 1e6)),
    Symbol("#MFR_SPY_DECEL", extract_decel,                             (-1, DECEL_CAP)),
    Symbol("#MFR_VIX_LO",    mfr_column("^VIX", "range_low"),           (1e-9, 1e4)),
    Symbol("#MFR_VIX_HI",    mfr_column("^VIX", "range_high"),          (1e-9, 1e4)),
    Symbol("#MFR_VIX_TREND", mfr_column("^VIX", "trend_signal", _convert_trend), (-1, 1)),
    Symbol("#RS_SPYUNIV_CORR60", extract_corr60,                        (-1, 1)),
    Symbol("#RORO_HYGTLT_ROC",   extract_roro,                          (-1, 1)),
    Symbol("#QUAD_M", quad_extractor("monthly_quad"),                   (1, 4)),
    Symbol("#QUAD_Q", quad_extractor("quarterly_quad"),                 (1, 4)),
]


# ─────────────────────────── validation ───────────────────────────

class ValidationError(Exception):
    pass


def _ny_minute(ts: datetime) -> str:
    return ts.astimezone(NY).strftime("%Y-%m-%dT%H:%M")


def validate(symbol: Symbol, rows: list[Row]) -> None:
    """Hard-fail checks per spec Step 2. Rows arrive ordered by source_key."""
    now = datetime.now(UTC)
    lo, hi = symbol.bounds
    problems = []
    prev_known = None
    seen_minutes: dict[str, str] = {}
    for r in rows:
        if r.value is None or isinstance(r.value, float) and (
                math.isnan(r.value) or math.isinf(r.value)):
            problems.append(f"{r.source_key}: non-finite value {r.value!r}")
            continue
        if not (lo <= r.value <= hi):
            problems.append(f"{r.source_key}: value {r.value} outside [{lo}, {hi}]")
        if r.known_at.tzinfo is None:
            problems.append(f"{r.source_key}: naive known_at {r.known_at}")
            continue
        if r.known_at >= now:
            problems.append(f"{r.source_key}: known_at {r.known_at} not in the past")
        if prev_known is not None and r.known_at < prev_known:
            problems.append(
                f"{r.source_key}: known_at {r.known_at} earlier than previous "
                f"row's {prev_known} (non-monotonic vs source order)")
        prev_known = r.known_at
        minute = _ny_minute(r.known_at)
        if minute in seen_minutes:
            problems.append(
                f"{r.source_key}: NY-minute stamp {minute} collides with "
                f"{seen_minutes[minute]} (groupingMethod=last would drop one)")
        seen_minutes[minute] = r.source_key
    if problems:
        detail = "\n  ".join(problems[:20])
        more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
        raise ValidationError(f"{symbol.name}: {len(problems)} violation(s):\n  {detail}{more}")


# ─────────────────────────── extraction orchestration ───────────────────────────

def last_uploaded_key(cur, symbol: str):
    cur.execute(
        "SELECT max(source_key) FROM ts_export_log "
        "WHERE symbol = %s AND uploaded_at IS NOT NULL",
        (symbol,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def extract_all(backfill: bool) -> dict[str, Extract]:
    """Run every extractor (read-only) and validate. Returns {symbol: Extract}."""
    results: dict[str, Extract] = {}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for sym in SYMBOLS:
            since = None if backfill else last_uploaded_key(cur, sym.name)
            ext = sym.extract(cur, since)
            validate(sym, ext.rows)
            results[sym.name] = ext
            for key, reason in ext.skips:
                print(f"  SKIP {sym.name} {key}: {reason}")
    return results


# ─────────────────────────── staging (ts_export_log) ───────────────────────────

def stage_rows(results: dict[str, Extract]) -> int:
    """Upsert extracted rows into ts_export_log. Uploaded rows are never
    touched (decision 5: never re-export an uploaded (symbol, source_key));
    still-pending rows refresh value/known_at. Returns rows staged/refreshed."""
    n = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for name, ext in results.items():
            for r in ext.rows:
                cur.execute(
                    """
                    INSERT INTO ts_export_log (symbol, source_key, known_at, value)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (symbol, source_key) DO UPDATE
                        SET value = EXCLUDED.value, known_at = EXCLUDED.known_at
                        WHERE ts_export_log.uploaded_at IS NULL
                    """,
                    (name, r.source_key, r.known_at, r.value),
                )
                n += cur.rowcount
        conn.commit()
    return n


def fetch_pending(symbols: list[str]) -> list[tuple[int, str, datetime, float]]:
    """(id, symbol, known_at, value) for every staged-but-not-uploaded row of
    the given symbols, ordered for the CSV."""
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, known_at, value
              FROM ts_export_log
             WHERE uploaded_at IS NULL AND symbol = ANY(%s)
             ORDER BY symbol, known_at
            """,
            (symbols,),
        )
        return cur.fetchall()


# ─────────────────────────── CSV + upload ───────────────────────────

def _fmt_value(v: float) -> str:
    s = f"{v:.10g}"
    return s


def build_csv(rows: list[tuple[int, str, datetime, float]]) -> str:
    """3-column CSV, no header: #SYMBOL,YYYY-MM-DDTHH:mm,value (NY wall clock)."""
    lines = [f"{sym},{_ny_minute(known_at)},{_fmt_value(val)}"
             for _id, sym, known_at, val in rows]
    return "\n".join(lines) + "\n"


def upload_csv(csv_text: str, url: str, api_key: str, symbol: str) -> tuple[int, str]:
    """POST one single-symbol CSV to the TrendSpider custom-symbols endpoint.
    Returns (http_status, response_body).

    The JSON body MUST be serialized with compact separators: TrendSpider's
    backend hangs ~60s (-> nginx 504) on JSON containing whitespace after
    ':' or ',' — json.dumps' DEFAULT separators. Isolated 2026-09-03 by
    byte-diffing failing vs succeeding requests; same payload compacted
    returns 200 in 0.2s. requests' json= kwarg uses the default separators,
    so serialize explicitly and send via data=."""
    b64 = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")  # no newlines
    payload = json.dumps({
        "symbol": symbol,
        "fileBase64": "data:text/csv;base64," + b64,
        "targetAssetType": "stock",
        "groupingMethod": "last",
    }, separators=(",", ":"))
    import requests
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-type": "application/json",
        },
        data=payload,
        timeout=120,
    )
    return resp.status_code, resp.text


def mark_batch(row_ids: list[int], batch_id: str, status: int, ok: bool) -> None:
    """Record the upload result. 2xx: uploaded_at set. Non-2xx: http_status
    recorded, uploaded_at stays NULL so the rows retry next run."""
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        if ok:
            cur.execute(
                "UPDATE ts_export_log SET uploaded_at = NOW(), "
                "http_status = %s, batch_id = %s WHERE id = ANY(%s)",
                (status, batch_id, row_ids),
            )
        else:
            cur.execute(
                "UPDATE ts_export_log SET http_status = %s, batch_id = %s "
                "WHERE id = ANY(%s)",
                (status, batch_id, row_ids),
            )
        conn.commit()


# ─────────────────────────── report helpers ───────────────────────────

def print_summary(results: dict[str, Extract]) -> None:
    print(f"\n{'symbol':<22} {'rows':>5} {'skips':>5}  known_at range"
          f"{'':<28} value range")
    for sym in SYMBOLS:
        ext = results[sym.name]
        if ext.rows:
            ka_min = _ny_minute(min(r.known_at for r in ext.rows))
            ka_max = _ny_minute(max(r.known_at for r in ext.rows))
            v_min = min(r.value for r in ext.rows)
            v_max = max(r.value for r in ext.rows)
            print(f"{sym.name:<22} {len(ext.rows):>5} {len(ext.skips):>5}  "
                  f"{ka_min} .. {ka_max}  {_fmt_value(v_min)} .. {_fmt_value(v_max)}")
        else:
            print(f"{sym.name:<22} {0:>5} {len(ext.skips):>5}  (no new rows)")


# ─────────────────────────── main ───────────────────────────

def run(dry_run: bool, backfill: bool) -> int:
    db_pg._load_dotenv_fallback()   # ensure TRENDSPIDER_* reach os.environ on CLI runs
    api_key = os.environ.get("TRENDSPIDER_API_KEY")
    url = os.environ.get("TRENDSPIDER_UPLOAD_URL", DEFAULT_UPLOAD_URL)
    if not dry_run and not api_key:
        print("ERROR: TRENDSPIDER_API_KEY not set (.env)")
        return 2

    mode = f"{'DRY-RUN ' if dry_run else ''}{'backfill' if backfill else 'incremental'}"
    print(f"TS EXPORT — {mode} — {len(SYMBOLS)} symbols")

    results = extract_all(backfill)
    print_summary(results)
    total_rows = sum(len(e.rows) for e in results.values())

    if dry_run:
        DRY_RUN_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(NY).strftime("%Y%m%dT%H%M%S")
        n_files = 0
        for sym in SYMBOLS:
            rows = [(0, sym.name, r.known_at, r.value)
                    for r in results[sym.name].rows]
            if not rows:
                continue
            csv_text = build_csv(rows)
            path = DRY_RUN_DIR / f"ts_export_{stamp}_{sym.name.lstrip('#')}.csv"
            path.write_text(csv_text, encoding="utf-8", newline="\n")
            size = len(csv_text.encode("utf-8"))
            if size > MAX_CSV_BYTES:
                raise ValidationError(f"{sym.name} CSV is {size} bytes > 7MB cap")
            n_files += 1
            print(f"{sym.name}: {len(rows)} rows, {size} bytes -> {path}")
        print(f"\nDRY RUN complete — {total_rows} rows across {n_files} "
              f"per-symbol files. Nothing staged, nothing uploaded.")
        return 0

    staged = stage_rows(results)
    print(f"\nstaged/refreshed {staged} rows in ts_export_log")

    batch_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    n_posts = 0
    n_uploaded_rows = 0
    failures: list[str] = []
    for sym in SYMBOLS:
        pending = fetch_pending([sym.name])
        if not pending:
            print(f"{sym.name}: nothing pending")
            continue
        csv_text = build_csv(pending)
        size = len(csv_text.encode("utf-8"))
        if size > MAX_CSV_BYTES:
            raise ValidationError(f"{sym.name} CSV is {size} bytes > 7MB cap")
        batch_id = f"{batch_stamp}-{sym.name.lstrip('#')}"
        if n_posts > 0:
            time.sleep(SLEEP_BETWEEN_UPLOADS_S)
        status, body = upload_csv(csv_text, url, api_key, sym.name)
        ok = 200 <= status < 300
        mark_batch([r[0] for r in pending], batch_id, status, ok)
        n_posts += 1
        if ok:
            n_uploaded_rows += len(pending)
            print(f"{sym.name}: HTTP {status}, {len(pending)} rows uploaded")
        else:
            failures.append(f"{sym.name}: HTTP {status}")
            print(f"{sym.name}: HTTP {status} — NOT marked uploaded.")
            print(f"  response body: {body[:500]}")

    n_symbols = sum(1 for e in results.values() if e.rows)
    if failures:
        summary = (f"TS EXPORT FAILED · {'; '.join(failures)} · "
                   f"{n_uploaded_rows}/{total_rows} rows uploaded")
    else:
        summary = (f"TS EXPORT ok · {n_symbols} symbols · {n_uploaded_rows} rows · "
                   f"{n_posts} posts")
    print(f"\n{summary}")
    try:
        from notifier import send_telegram
        send_telegram("TS EXPORT", summary)
    except Exception as e:
        print(f"WARNING: telegram summary failed: {e}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TrendSpider custom-symbol export")
    ap.add_argument("--dry-run", action="store_true",
                    help="write CSVs to ./ts_export/ and print counts; no DB writes, no upload")
    ap.add_argument("--backfill", action="store_true",
                    help="export full history (default: incremental past last uploaded row)")
    args = ap.parse_args()
    try:
        return run(dry_run=args.dry_run, backfill=args.backfill)
    except ValidationError as e:
        msg = f"TS EXPORT FAILED · validation: {e}"
        print(f"\nERROR: {msg}")
        if not args.dry_run:
            try:
                from notifier import send_telegram
                send_telegram("TS EXPORT", msg[:1000])
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
