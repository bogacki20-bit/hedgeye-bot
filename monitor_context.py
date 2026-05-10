"""
Builds Hedgeye / SpotGamma context dicts from corpus_documents. Used by
price_monitor.compose_recommendation to attach real macro / dealer context to
alerts_fired rows (replacing the previous hardcoded placeholders).

Both lookups are non-fatal — return {} on any error or if no relevant doc is
found. Results are TTL-cached (15 min) so a monitor cycle that fans out across
many tickers makes one query per (hedgeye, ticker) pair.

Output shape — parsed fields are best-effort regex; provenance + raw_snippet
are always included on a hit so the ML side can recover anything missed.

CLI smoke test:
    py monitor_context.py
    py monitor_context.py --ticker OIH --full
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import psycopg2.extras

from db_pg import get_conn

log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 900  # 15 minutes
_cache: dict = {}         # key -> (epoch_seconds, value)


# ─────────────────────────── Cache helpers ───────────────────────────

def _cache_get(key: str):
    entry = _cache.get(key)
    if not entry:
        return None
    ts, val = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return val


def _cache_put(key: str, val) -> None:
    _cache[key] = (time.time(), val)


def clear_cache() -> None:
    """Reset the TTL cache. Useful in tests or after a corpus reingest."""
    _cache.clear()


# ─────────────────────────── Corpus query ───────────────────────────

def _fetch_latest_doc(
    sources: list[str],
    *,
    ticker: Optional[str] = None,
    max_chars: int = 4000,
) -> Optional[dict]:
    """Return the most recent corpus_documents row matching `sources`.

    If `ticker` is provided, only rows that mention it (title or full_text
    ILIKE substring) are eligible, AND rows whose title is *exactly* the
    ticker symbol (the convention SpotGamma equity-hub captures use) are
    preferred over body-only matches like run logs that happen to enumerate
    every captured ticker.

    Returns None on miss or DB error. Body is truncated to `max_chars`.
    """
    sql = (
        "SELECT source, source_date, source_ref, title, "
        "       substring(full_text, 1, %s) AS body "
        "  FROM corpus_documents "
        " WHERE source = ANY(%s)"
    )
    params: list = [max_chars, list(sources)]
    order_by = "ORDER BY source_date DESC, id DESC"
    if ticker:
        sql += " AND (title ILIKE %s OR full_text ILIKE %s)"
        like = f"%{ticker}%"
        params.extend([like, like])
        # Two-tier ranking: exact-title-match first, then most recent.
        order_by = (
            "ORDER BY (CASE WHEN UPPER(title) = UPPER(%s) THEN 0 ELSE 1 END), "
            "         source_date DESC, id DESC"
        )
        params.append(ticker)
    sql += " " + order_by + " LIMIT 1"

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        log.warning(f"monitor_context._fetch_latest_doc failed: {e}")
        return None


# ─────────────────────────── Hedgeye parsing ───────────────────────────

_QUAD_RE = re.compile(r"\bQuad\s*([1234])\b", re.IGNORECASE)
_VIX_BUCKET_RE = re.compile(
    r"\b(investable|uninvestable|invalidation|level\s+break)\b",
    re.IGNORECASE,
)


def _parse_hedgeye_fields(text: str) -> dict:
    """Best-effort regex parse of Quad + VIX bucket labels from macro show text."""
    out: dict = {"current_quad": None, "vix_bucket": None}
    if not text:
        return out
    m = _QUAD_RE.search(text)
    if m:
        out["current_quad"] = f"Quad {m.group(1)}"
    m = _VIX_BUCKET_RE.search(text)
    if m:
        # Normalize whitespace inside the matched label
        out["vix_bucket"] = re.sub(r"\s+", "_", m.group(1).strip().lower())
    return out


def get_hedgeye_ctx() -> dict:
    """Return current Hedgeye macro context as a dict.

    Shape (all fields nullable; provenance + raw_snippet always present on hit):
        current_quad   — "Quad 1".."Quad 4" or None
        vix_bucket     — "investable" / "uninvestable" / "level_break" / None
        source         — corpus source value (e.g. "macro_show_dashboard")
        source_date    — ISO date the doc was published
        source_ref     — corpus_documents.source_ref (URL or filename)
        raw_snippet    — first 1500 chars of body (ML-side fallback)

    Returns {} on any error or if no eligible doc exists. Never raises.
    """
    cached = _cache_get("hedgeye")
    if cached is not None:
        return cached

    ctx: dict = {}
    try:
        # Prefer the most condensed source first, fall back to fuller ones.
        # Bump max_chars to 12k — the dashboard puts the "Investable" /
        # "Uninvestable" VIX-bucket label past the 4k mark in its meta footer.
        doc = (
            _fetch_latest_doc(["macro_show_dashboard"], max_chars=12000) or
            _fetch_latest_doc(["macro_show_summary_notes"], max_chars=12000) or
            _fetch_latest_doc(["macro_show_deck"], max_chars=12000)
        )
        if doc:
            body = doc.get("body") or ""
            fields = _parse_hedgeye_fields(body)
            ctx = {
                **fields,
                "source": doc.get("source"),
                "source_date": (
                    str(doc["source_date"]) if doc.get("source_date") else None
                ),
                "source_ref": doc.get("source_ref"),
                "raw_snippet": body[:1500],
            }
    except Exception as e:
        log.warning(f"get_hedgeye_ctx failed (returning empty): {e}")
        ctx = {}

    _cache_put("hedgeye", ctx)
    return ctx


# ─────────────────────────── SpotGamma parsing ───────────────────────────

_GAMMA_REGIME_RE = re.compile(r"\b(positive|negative)\s+gamma\b", re.IGNORECASE)
# Number pattern: allows comma-separated thousands ("7,300") and decimals.
# Captured commas are stripped before float() in _parse_spotgamma_fields.
_PRICE_NUM = r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?|[0-9]{1,7}(?:\.[0-9]+)?)"
_CALL_WALL_RE = re.compile(
    rf"call\s*wall[^\d$]{{0,30}}\$?{_PRICE_NUM}",
    re.IGNORECASE,
)
_PUT_WALL_RE = re.compile(
    rf"put\s*wall[^\d$]{{0,30}}\$?{_PRICE_NUM}",
    re.IGNORECASE,
)
_HEDGE_WALL_RE = re.compile(
    rf"(?:hedge|key\s+gamma)\s*wall[^\d$]{{0,30}}\$?{_PRICE_NUM}",
    re.IGNORECASE,
)


def _to_float(s: str) -> Optional[float]:
    """Parse a price-like string ('7,300', '418.00') to float, or None."""
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _parse_spotgamma_fields(text: str) -> dict:
    out: dict = {"gamma_regime": None, "key_levels": {}}
    if not text:
        return out
    m = _GAMMA_REGIME_RE.search(text)
    if m:
        out["gamma_regime"] = f"{m.group(1).lower()}_gamma"
    levels: dict = {}
    for re_obj, key in (
        (_CALL_WALL_RE,  "call_wall"),
        (_PUT_WALL_RE,   "put_wall"),
        (_HEDGE_WALL_RE, "hedge_wall"),
    ):
        m = re_obj.search(text)
        if m:
            v = _to_float(m.group(1))
            if v is not None:
                levels[key] = v
    if levels:
        out["key_levels"] = levels
    return out


def _fetch_typed_spotgamma(ticker: str) -> Optional[dict]:
    """Read the most-recent spotgamma_snapshots row for a ticker.

    Preferred over the corpus_documents regex path because columns are typed
    and exact (no regex false positives). Returns None on miss or DB error.
    Returned dict matches the shape get_spotgamma_ctx exposes:
      ticker, gamma_regime, key_levels{call_wall,put_wall,hedge_wall},
      iv_rank, skew_rank, top_gamma_exp, source, source_date, source_ref.
    """
    try:
        # Lazy import — slice 1 added spotgamma_client.py; we keep monitor_context
        # functional even if spotgamma_client is somehow unimportable.
        import spotgamma_client
        row = spotgamma_client.latest(ticker)
    except Exception as e:
        log.warning(f"spotgamma_client.latest({ticker}) raised: {e}")
        return None
    if not row:
        return None

    # Build key_levels from non-null typed columns.
    levels: dict = {}
    for col in ("call_wall", "put_wall", "hedge_wall",
                "key_gamma_strike", "key_delta_strike"):
        v = row.get(col)
        if v is not None:
            try:
                levels[col] = float(v)
            except (TypeError, ValueError):
                pass

    # Gamma regime — typed columns don't carry the literal "positive/negative
    # gamma" label, but call_gamma + put_gamma signs imply it. Negative call
    # gamma + negative put gamma = dealers are short gamma overall (the typical
    # SpotGamma "negative gamma" regime).
    gamma_regime = None
    cg, pg = row.get("call_gamma"), row.get("put_gamma")
    try:
        if cg is not None and pg is not None:
            net = float(cg) + float(pg)
            gamma_regime = "negative_gamma" if net < 0 else "positive_gamma"
    except (TypeError, ValueError):
        pass

    return {
        "ticker": (row.get("ticker") or ticker).upper(),
        "gamma_regime": gamma_regime,
        "key_levels": levels,
        "iv_rank":    float(row["iv_rank"])    if row.get("iv_rank")    is not None else None,
        "skew_rank":  float(row["skew_rank"])  if row.get("skew_rank")  is not None else None,
        "top_gamma_exp": (
            row["top_gamma_exp"].isoformat()
            if row.get("top_gamma_exp") is not None
            else None
        ),
        "source": "spotgamma_snapshots",
        "source_date": (
            row["snapshot_date"].isoformat()
            if row.get("snapshot_date") is not None
            else None
        ),
        "source_ref": row.get("source_ref"),
        "capture_type": row.get("capture_type"),
    }


def get_spotgamma_ctx(ticker: str) -> dict:
    """Return per-ticker SpotGamma context as a dict.

    Two-tier read:
      1. spotgamma_snapshots (typed columns, populated by spotgamma_client)
      2. corpus_documents regex over spotgamma_equityhub / _other / founders_note
    Tier 1 is preferred because columns are exact; tier 2 is the fallback for
    tickers that don't yet have a typed row (e.g. before populator runs, or
    for SpotGamma-only docs that aren't equity hubs).

    Shape (all fields nullable; provenance always present on hit):
        ticker         — input ticker, uppercased
        gamma_regime   — "positive_gamma" / "negative_gamma" / None
        key_levels     — {"call_wall": float, "put_wall": float, ...} (may be {})
        iv_rank        — percent (e.g. 36.55), tier-1 only
        skew_rank      — percent, tier-1 only
        top_gamma_exp  — ISO date, tier-1 only
        capture_type   — premarket / eod / on_demand, tier-1 only
        source         — "spotgamma_snapshots" or corpus source value
        source_date    — ISO date the row/doc was captured
        source_ref     — file path or corpus_documents.source_ref
        raw_snippet    — tier-2 only: first 1500 chars of body

    Returns {} if `ticker` is empty, on DB error, or if neither a typed row
    nor a corpus doc exists. Never raises.
    """
    if not ticker:
        return {}
    cache_key = f"sg::{ticker.upper()}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    ctx: dict = {}
    # Tier 1: typed read
    try:
        typed = _fetch_typed_spotgamma(ticker)
        if typed:
            ctx = typed
    except Exception as e:
        log.warning(f"typed spotgamma read for {ticker} raised: {e}")

    # Tier 2: corpus_documents regex fallback (only if tier 1 missed)
    if not ctx:
        try:
            doc = (
                _fetch_latest_doc(["spotgamma_equityhub", "spotgamma_other"], ticker=ticker) or
                _fetch_latest_doc(["spotgamma_founders_note"])
            )
            if doc:
                body = doc.get("body") or ""
                fields = _parse_spotgamma_fields(body)
                ctx = {
                    "ticker": ticker.upper(),
                    **fields,
                    "source": doc.get("source"),
                    "source_date": (
                        str(doc["source_date"]) if doc.get("source_date") else None
                    ),
                    "source_ref": doc.get("source_ref"),
                    "raw_snippet": body[:1500],
                }
        except Exception as e:
            log.warning(f"get_spotgamma_ctx({ticker}) tier-2 failed: {e}")

    _cache_put(cache_key, ctx)
    return ctx


# ─────────────────────────── CLI smoke test ───────────────────────────

def _cli() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="monitor_context smoke test: dump hedgeye_ctx and (optional) spotgamma_ctx"
    )
    ap.add_argument("--ticker", help="Also dump get_spotgamma_ctx() for this ticker")
    ap.add_argument(
        "--full", action="store_true",
        help="Include raw_snippet in the printed dicts (default truncates it)",
    )
    args = ap.parse_args()

    def _trim(d: dict) -> dict:
        if args.full or not isinstance(d, dict):
            return d
        return {k: v for k, v in d.items() if k != "raw_snippet"}

    print("=== get_hedgeye_ctx() ===")
    h = get_hedgeye_ctx()
    print(json.dumps(_trim(h), indent=2, default=str))
    print()

    if args.ticker:
        print(f"=== get_spotgamma_ctx({args.ticker!r}) ===")
        s = get_spotgamma_ctx(args.ticker)
        print(json.dumps(_trim(s), indent=2, default=str))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
