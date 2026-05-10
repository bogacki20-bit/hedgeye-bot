"""SpotGamma equity-hub data client — typed per-ticker snapshots.

Mirrors the MFR client/inventory pattern (mfr_client.py + ticker_inventory.py)
for SpotGamma. Where MFR has a public REST API, SpotGamma's data lives behind
a login-gated dashboard, so this module's primary path is parsing the markdown
files our daily Cowork sweeps already capture under
`data/snapshots/spotgamma/{date}/equityhub*/{TICKER}.md`. Slice 2 will add an
on-demand Chrome-driven scrape for runtime per-ticker freshness.

Public API:
    parse_equityhub_md(path)               -> dict | None
    save_snapshot(ticker, date, ctype, p)  -> bool
    populate_from_snapshots(root=None)     -> dict           # walk + save all
    latest(ticker)                         -> dict | None    # most-recent row
    is_stale(ticker, max_age_minutes=60)   -> bool

CLI smoke test:
    py spotgamma_client.py --populate
    py spotgamma_client.py --latest OIH
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Optional

import psycopg2.extras

from db_pg import get_conn

log = logging.getLogger(__name__)

DEFAULT_SNAPSHOTS_ROOT = Path(__file__).parent / "data" / "snapshots" / "spotgamma"


# ─────────────────────────── Markdown parsing ───────────────────────────

# Table-row pattern: `| <field> | <value> |` (one or more pipes; whitespace
# tolerant). Captures field name and value, both stripped.
_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|", re.MULTILINE)

# Bullet: `- **Field:** value` / `- **Field**: value` / `- Field: value`.
# We match the structural pieces (leading bullet, field name up to colon, value
# to EOL) and post-process to strip `**` from both halves. This handles every
# bold style SpotGamma's exporters use without ad-hoc regex variants.
_BULLET_RE = re.compile(
    r"^[-*]\s+(?P<rawfield>[^:]+?)\s*:\s*(?P<rawvalue>.+?)\s*$",
    re.MULTILINE,
)


def _clean_md(s: str) -> str:
    """Strip surrounding whitespace and `**` markers from a markdown bullet half."""
    if not s:
        return ""
    return s.strip().strip("*").strip()

# Header: `# SpotGamma {TICKER} Equity Hub — {YYYY-MM-DD}[ EOD]` etc.
_HEADER_RE = re.compile(
    r"^#\s+SpotGamma\s+(?P<ticker>[A-Z][A-Z0-9.\-]{0,9})\s+Equity\s+Hub",
    re.IGNORECASE | re.MULTILINE,
)


def _to_number(raw: str) -> Optional[float]:
    """Parse a value string like '$418.72', '+0.24%', '-2.31M', '300,735' to float.

    Strips $, %, +, ,. Recognizes 'M' suffix as ×1e6, 'K' as ×1e3, 'B' as ×1e9.
    Returns None for em-dash, empty, or unparseable strings.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("—", "-", "–", "n/a", "N/A", "—%"):
        return None
    # Strip leading currency / sign, trailing percent
    s = s.replace("$", "").replace(",", "").replace("+", "").strip()
    if s.endswith("%"):
        s = s[:-1].strip()
    mult = 1.0
    if s.endswith(("M", "m")):
        mult = 1e6
        s = s[:-1].strip()
    elif s.endswith(("K", "k")):
        mult = 1e3
        s = s[:-1].strip()
    elif s.endswith(("B", "b")):
        mult = 1e9
        s = s[:-1].strip()
    try:
        return float(s) * mult
    except ValueError:
        return None


def _to_int(raw: str) -> Optional[int]:
    v = _to_number(raw)
    return int(v) if v is not None else None


def _to_date(raw: str) -> Optional[date_cls]:
    """Parse a date string like '2026-10-16' or '10/16/2026' or '—'."""
    if not raw:
        return None
    s = str(raw).strip()
    if s in ("—", "-", "–", "n/a", "N/A", ""):
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# Map from markdown field labels (lowercased, trimmed) to (column_name, parser).
# Both bullets and table rows are matched against this dict.
_FIELD_MAP: dict[str, tuple[str, callable]] = {
    # Headline
    "current price":       ("price",            _to_number),
    "daily change":        ("daily_change_pct", _to_number),
    "previous close":      ("prev_close",       _to_number),
    "stock volume":        ("stock_volume",     _to_int),
    "52-week high":        ("high_52w",         _to_number),
    "52-week low":          ("low_52w",          _to_number),
    "earnings date":       ("earnings_date",    _to_date),
    # Key levels
    "call wall":           ("call_wall",        _to_number),
    "put wall":            ("put_wall",         _to_number),
    "hedge wall":          ("hedge_wall",       _to_number),
    "hedge wall (table)":  ("hedge_wall",       _to_number),
    "key gamma strike":    ("key_gamma_strike", _to_number),
    "key delta strike":    ("key_delta_strike", _to_number),
    # Gamma posture
    "call gamma":          ("call_gamma",       _to_number),
    "put gamma":           ("put_gamma",        _to_number),
    "top gamma exp":       ("top_gamma_exp",    _to_date),
    "top delta exp":       ("top_delta_exp",    _to_date),
    "gamma hedge est":     ("gamma_hedge_est",  _to_number),
    # Options activity
    "call volume":         ("call_volume",      _to_int),
    "put volume":          ("put_volume",       _to_int),
    "put/call oi ratio":   ("put_call_oi_ratio", _to_number),
    "1 m rv":              ("rv_1m",            _to_number),
    "1 m iv":              ("iv_1m",            _to_number),
    "iv rank":             ("iv_rank",          _to_number),
    "skew rank":           ("skew_rank",        _to_number),
    "garch rank":          ("garch_rank",       _to_number),
    "options implied move": ("options_implied_move", _to_number),
}


def parse_equityhub_md(path: Path | str) -> Optional[dict]:
    """Parse a SpotGamma equity-hub markdown file into a typed dict.

    Walks both bullet-style headline lines and pipe-table rows, looking up each
    field label in `_FIELD_MAP`. Returns None if the file isn't a recognizable
    equity-hub capture (no ticker in header). Unknown fields get dropped.

    Adds `_ticker` (from header) and `_raw_text` to the returned dict for
    callers that need provenance.
    """
    p = Path(path)
    try:
        body = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.warning("spotgamma_client: cannot read %s: %s", p, e)
        return None

    header_match = _HEADER_RE.search(body)
    if not header_match:
        return None
    ticker = header_match.group("ticker").upper()

    fields: dict = {}
    # Bullets
    for m in _BULLET_RE.finditer(body):
        label = _clean_md(m.group("rawfield")).lower()
        value = _clean_md(m.group("rawvalue"))
        if label in _FIELD_MAP:
            col, parser = _FIELD_MAP[label]
            fields[col] = parser(value)
    # Table rows (after | Field | Value |). Skip header / separator rows.
    for m in _TABLE_ROW_RE.finditer(body):
        label = m.group(1).strip().lower()
        value = m.group(2).strip()
        # Skip table headers ("Field" / "Level") and dash separators.
        if label in ("field", "level") or set(label) <= {"-", " "}:
            continue
        if label in _FIELD_MAP:
            col, parser = _FIELD_MAP[label]
            fields[col] = parser(value)

    fields["_ticker"] = ticker
    fields["_raw_text"] = body
    return fields


# ─────────────────────────── Path → (ticker, date, capture_type) ───────────────────────────

def _classify_path(path: Path) -> Optional[tuple[str, date_cls, str]]:
    """Pull (ticker, snapshot_date, capture_type) from a snapshots path.

    Expected layouts:
      .../spotgamma/{YYYY-MM-DD}/equityhub/{TICKER}.md        -> premarket
      .../spotgamma/{YYYY-MM-DD}/equityhub_eod/{TICKER}.md    -> eod
      .../spotgamma/{YYYY-MM-DD}/{TICKER}.md                  -> premarket (legacy)
    Returns None if the path doesn't match one of these.
    """
    try:
        parts = path.parts
        # Find the spotgamma segment
        if "spotgamma" not in parts:
            return None
        idx = parts.index("spotgamma")
        # Need at least date dir and filename after 'spotgamma'
        if idx + 2 >= len(parts):
            return None
        date_str = parts[idx + 1]
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_str)
        if not m:
            return None
        d = date_cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        # Capture type from subfolder
        subdirs = parts[idx + 2:-1]  # everything between date and filename
        if "equityhub_eod" in subdirs:
            ctype = "eod"
        elif "equityhub" in subdirs:
            ctype = "premarket"
        elif not subdirs:
            ctype = "premarket"  # legacy root-level capture
        else:
            return None
        ticker = path.stem.upper()
        # Skip non-ticker filenames (e.g. _run_log, _index, market_overview)
        if ticker.startswith("_") or not re.match(r"^[A-Z][A-Z0-9.\-]{0,9}$", ticker):
            return None
        return ticker, d, ctype
    except (ValueError, IndexError):
        return None


# ─────────────────────────── DB persistence ───────────────────────────

# Columns we write besides the PK and metadata. Must match schema.sql order
# in the INSERT below.
_VALUE_COLUMNS = (
    "price", "prev_close", "daily_change_pct", "stock_volume",
    "high_52w", "low_52w", "earnings_date",
    "call_wall", "put_wall", "hedge_wall",
    "key_gamma_strike", "key_delta_strike",
    "call_gamma", "put_gamma", "top_gamma_exp", "top_delta_exp",
    "gamma_hedge_est",
    "call_volume", "put_volume", "put_call_oi_ratio",
    "iv_1m", "rv_1m", "iv_rank", "skew_rank", "garch_rank",
    "options_implied_move",
)


def save_snapshot(
    ticker: str,
    snapshot_date: date_cls,
    capture_type: str,
    parsed: dict,
    *,
    source_ref: Optional[str] = None,
) -> bool:
    """Upsert a parsed snapshot into spotgamma_snapshots.

    `parsed` should be the dict returned from parse_equityhub_md (or a
    Chrome-scrape parser in slice 2). Unknown / missing fields are stored as
    NULL. Idempotent: PK (ticker, snapshot_date, capture_type) collides → row
    is updated.

    Returns True on successful write, False on any error.
    """
    if not ticker or not snapshot_date or not capture_type:
        return False
    if capture_type not in ("premarket", "eod", "on_demand"):
        log.warning("save_snapshot: unknown capture_type=%r", capture_type)
        return False

    # full_payload: keep all parsed fields except the noisy raw_text + ticker.
    payload = {k: v for k, v in parsed.items() if k not in ("_raw_text", "_ticker")}
    # Convert dates to ISO strings for JSON
    for k, v in list(payload.items()):
        if isinstance(v, date_cls):
            payload[k] = v.isoformat()

    raw_text = parsed.get("_raw_text") or ""

    cols_sql = ", ".join(_VALUE_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_VALUE_COLUMNS))
    update_sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in _VALUE_COLUMNS)

    values = [parsed.get(c) for c in _VALUE_COLUMNS]

    sql = f"""
        INSERT INTO spotgamma_snapshots (
            ticker, snapshot_date, capture_type,
            {cols_sql},
            source_ref, raw_text, full_payload, fetched_at
        )
        VALUES (
            %s, %s, %s,
            {placeholders},
            %s, %s, %s, NOW()
        )
        ON CONFLICT (ticker, snapshot_date, capture_type) DO UPDATE SET
            {update_sets},
            source_ref   = EXCLUDED.source_ref,
            raw_text     = EXCLUDED.raw_text,
            full_payload = EXCLUDED.full_payload,
            fetched_at   = NOW()
    """
    params = [ticker.upper(), snapshot_date, capture_type, *values,
              source_ref, raw_text, json.dumps(payload, default=str)]

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        return True
    except Exception as e:
        log.warning("save_snapshot(%s, %s, %s) failed: %s",
                    ticker, snapshot_date, capture_type, e)
        return False


def populate_from_snapshots(root: Optional[Path] = None) -> dict:
    """Walk the snapshots directory and save every recognizable equity-hub file.

    Returns a summary dict: {discovered, parsed, saved, skipped, failed}.
    """
    base = root or DEFAULT_SNAPSHOTS_ROOT
    if not base.exists():
        log.warning("populate_from_snapshots: %s does not exist", base)
        return {"discovered": 0, "parsed": 0, "saved": 0, "skipped": 0, "failed": 0}

    summary = {"discovered": 0, "parsed": 0, "saved": 0, "skipped": 0, "failed": 0}
    for f in base.rglob("*.md"):
        cls = _classify_path(f)
        if not cls:
            continue
        summary["discovered"] += 1
        ticker, d, ctype = cls
        parsed = parse_equityhub_md(f)
        if not parsed:
            summary["skipped"] += 1
            continue
        summary["parsed"] += 1
        # The header ticker should match the filename ticker; if it doesn't
        # (rare — typo), prefer the filename one and log.
        header_ticker = parsed.get("_ticker")
        if header_ticker and header_ticker != ticker:
            log.info("populate: header_ticker=%s filename_ticker=%s for %s — using filename",
                     header_ticker, ticker, f)
        ok = save_snapshot(ticker, d, ctype, parsed,
                           source_ref=str(f.relative_to(base)).replace("\\", "/"))
        if ok:
            summary["saved"] += 1
        else:
            summary["failed"] += 1
    return summary


# ─────────────────────────── Read helpers ───────────────────────────

def latest(ticker: str) -> Optional[dict]:
    """Return the most-recent spotgamma_snapshots row for a ticker, or None.

    Newest by `fetched_at`. Ties broken by snapshot_date DESC.
    """
    if not ticker:
        return None
    sql = """
        SELECT * FROM spotgamma_snapshots
         WHERE ticker = %s
         ORDER BY fetched_at DESC, snapshot_date DESC
         LIMIT 1
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, (ticker.upper(),))
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        log.warning("spotgamma_client.latest(%s) failed: %s", ticker, e)
        return None


def is_stale(ticker: str, max_age_minutes: int = 60) -> bool:
    """Return True if the latest snapshot for ticker is older than max_age_minutes
    (or if no snapshot exists). Used by slice 2 to decide whether to chrome-scrape.
    """
    row = latest(ticker)
    if not row:
        return True
    fetched = row.get("fetched_at")
    if not fetched:
        return True
    age_minutes = (datetime.now(fetched.tzinfo) - fetched).total_seconds() / 60.0
    return age_minutes > max_age_minutes


# ─────────────────────────── CLI smoke test ───────────────────────────

def _cli() -> None:
    ap = argparse.ArgumentParser(
        description="spotgamma_client CLI: populate from snapshots, or look up latest by ticker"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--populate", action="store_true",
                   help="Walk data/snapshots/spotgamma and upsert every equity hub file")
    g.add_argument("--latest", metavar="TICKER",
                   help="Print the latest spotgamma_snapshots row for TICKER")
    g.add_argument("--parse", metavar="PATH",
                   help="Parse a single equity hub markdown file and print the dict (no DB)")
    ap.add_argument("--root", help="Override snapshots root (defaults to repo data/snapshots/spotgamma)")
    args = ap.parse_args()

    if args.populate:
        root = Path(args.root) if args.root else None
        summary = populate_from_snapshots(root)
        print(json.dumps(summary, indent=2))
        return

    if args.latest:
        row = latest(args.latest)
        if not row:
            print(f"no rows for {args.latest!r}")
            return
        # Trim raw_text + full_payload for terminal readability
        printable = {k: v for k, v in row.items() if k not in ("raw_text", "full_payload")}
        print(json.dumps(printable, indent=2, default=str))
        return

    if args.parse:
        parsed = parse_equityhub_md(args.parse)
        if not parsed:
            print(f"could not parse {args.parse!r}")
            return
        printable = {k: v for k, v in parsed.items() if k != "_raw_text"}
        print(json.dumps(printable, indent=2, default=str))
        return


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
