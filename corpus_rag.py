"""
Corpus retrieval helper. Provides FTS-based context fetching from corpus_documents.

Used by classifier.py (and eventually price_monitor.py / recommender.py) to inject
relevant Hedgeye / SpotGamma / VolStudies context into Claude API calls or alert
metadata payloads.

The corpus_documents_fts GIN index makes this fast even with hundreds of docs —
no pgvector required for v1. Add embeddings later when corpus crosses ~1000 docs
or when FTS starts missing concept matches.

CLI smoke test:
    py corpus_rag.py XLE energy quad
    py corpus_rag.py volatility natenberg --sources volsignals_paid_course
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from typing import Iterable

import psycopg2.extras

from db_pg import get_conn

log = logging.getLogger(__name__)


# Tokens that look like tickers but are almost always English words or
# Hedgeye boilerplate. Filtered out before they pollute FTS queries.
_TICKER_STOPWORDS = {
    "BUY", "SELL", "HOLD", "LONG", "SHORT", "BEST", "ADD", "TRIM",
    "ETF", "USD", "USA", "API", "NYSE", "NASDAQ", "SEC", "FED", "FOMC",
    "NEW", "OLD", "TOP", "BIG", "FOR", "AND", "THE", "ARE", "ALL",
    "OUR", "YOU", "WAS", "HAS", "GOT", "NOT", "BUT", "CAN", "GET",
    "WILL", "WITH", "FROM", "INTO", "OVER", "THIS", "THAT", "WHAT",
    "WHEN", "WHERE", "ONLY", "JUST", "MORE", "EACH", "BOTH", "EVEN",
    "DEAL", "RISK", "TRADE", "CALL", "PUT", "PUTS", "CALLS",
}

# Theme keywords that are useful corpus search terms when they appear
# in an email — these are domain words that show up across the corpus.
_THEME_TOKENS = [
    "volatility", "gamma", "delta", "vega", "theta", "charm",
    "hedging", "dealer", "regime", "quad", "macro",
    "energy", "financials", "retail", "tech", "industrials",
    "real estate", "duration", "yield", "credit", "earnings",
    "natenberg", "spx", "vix", "ndx", "spot", "implied", "realized",
    "lognormal", "skew", "term structure",
]


def _build_tsquery(terms: Iterable[str]) -> str:
    """Build a Postgres to_tsquery string from raw terms.

    Terms get OR'd together (any match). Single short ALL-CAPS tokens (likely
    tickers) get prefix matching to handle variations like "XLE" vs "xle's".
    Special chars are stripped to avoid tsquery syntax errors.
    """
    cleaned = []
    seen = set()
    for raw in terms:
        if not raw:
            continue
        # Strip everything that isn't alphanumeric or underscore
        t = re.sub(r"[^a-zA-Z0-9_]", " ", str(raw)).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        # Multi-word terms ("real estate") split into ANDed words
        parts = t.split()
        if len(parts) > 1:
            cleaned.append("(" + " & ".join(p.lower() for p in parts) + ")")
        else:
            word = parts[0]
            # Likely-ticker → prefix match (3-5 chars, all upper, all alpha)
            if 2 <= len(word) <= 5 and word.isalpha() and word.isupper():
                cleaned.append(word.lower() + ":*")
            else:
                cleaned.append(word.lower())
    return " | ".join(cleaned)


def extract_query_terms(text: str, *, max_terms: int = 12) -> list[str]:
    """Pull candidate corpus search terms from arbitrary text.

    Returns deduplicated list of:
      - Likely tickers (2-5 char ALL-CAPS, minus stopwords)
      - Theme tokens (volatility, gamma, energy, etc.) found in the text

    Order matters — tickers come first since they're usually higher-signal.
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    # Tickers
    for m in re.findall(r"\b[A-Z]{2,5}\b", text):
        if m in _TICKER_STOPWORDS or m in seen:
            continue
        seen.add(m)
        out.append(m)
    # Themes
    text_lower = text.lower()
    for theme in _THEME_TOKENS:
        if theme in text_lower and theme not in seen:
            seen.add(theme)
            out.append(theme)
    return out[:max_terms]


def fetch_context(
    terms: Iterable[str],
    *,
    sources: list[str] | None = None,
    limit: int = 5,
    snippet_chars: int = 600,
) -> list[dict]:
    """Retrieve up to `limit` corpus_documents most relevant to `terms` via FTS.

    Args:
        terms: keywords (tickers, themes) to search for
        sources: restrict to these `source` values; None = all sources
        limit: max docs to return
        snippet_chars: how much body text to include per result

    Returns:
        list of dicts: {source, source_date, source_ref, title, snippet, rank}
        Sorted by rank desc. Empty list on error or no matches.
    """
    query = _build_tsquery(terms)
    if not query:
        return []

    sql = """
        SELECT
            source,
            source_date,
            source_ref,
            title,
            ts_rank(to_tsvector('english', full_text),
                    to_tsquery('english', %s)) AS rank,
            substring(full_text, 1, %s) AS snippet
        FROM corpus_documents
        WHERE to_tsvector('english', full_text) @@ to_tsquery('english', %s)
    """
    params: list = [query, snippet_chars, query]
    if sources:
        sql += " AND source = ANY(%s)"
        params.append(list(sources))
    sql += " ORDER BY rank DESC LIMIT %s"
    params.append(limit)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(sql, params)
                rows = [dict(r) for r in cur.fetchall()]
        return rows
    except Exception as e:
        # Never raise — corpus context is enrichment, not a hard dependency.
        log.warning(f"corpus_rag.fetch_context failed (continuing without context): {e}")
        return []


def format_for_prompt(results: list[dict], *, max_chars: int = 4000) -> str:
    """Format retrieval results as a prompt-injectable XML block.

    Wraps each in a <doc> tag with source + date + title attributes, body in
    a <snippet> child. Caps total chars to keep prompt size predictable.
    Returns an empty string if no results.
    """
    if not results:
        return ""
    header = "<corpus_context>"
    footer = "</corpus_context>"
    used = len(header) + len(footer) + 2  # newlines
    blocks: list[str] = [header]
    for r in results:
        src = (r.get("source") or "").replace('"', "")
        date = r.get("source_date")
        ref = (r.get("source_ref") or "").replace('"', "")
        title = (r.get("title") or "").replace("<", "").replace(">", "")
        snippet = (r.get("snippet") or "").strip()
        # Sanitize the snippet for prompt embedding
        snippet = snippet.replace("<", "&lt;").replace(">", "&gt;")
        block = (
            f'  <doc source="{src}" date="{date}" ref="{ref}">\n'
            f'    <title>{title}</title>\n'
            f'    <snippet>{snippet}</snippet>\n'
            f'  </doc>'
        )
        if used + len(block) + 1 > max_chars:
            blocks.append(f'  <!-- truncated, {len(results) - (len(blocks)-1)} more results dropped -->')
            break
        blocks.append(block)
        used += len(block) + 1
    blocks.append(footer)
    return "\n".join(blocks)


def fetch_and_format(
    text: str,
    *,
    sources: list[str] | None = None,
    limit: int = 5,
    snippet_chars: int = 500,
    max_prompt_chars: int = 3000,
) -> tuple[str, list[str], int]:
    """Convenience: extract terms from `text`, retrieve, format for prompt.

    Returns (formatted_block, terms_used, result_count). The formatted block
    is empty string if no terms or no matches.
    """
    terms = extract_query_terms(text)
    if not terms:
        return "", [], 0
    results = fetch_context(terms, sources=sources, limit=limit, snippet_chars=snippet_chars)
    formatted = format_for_prompt(results, max_chars=max_prompt_chars)
    return formatted, terms, len(results)


# ─────────────────────────── CLI smoke test ───────────────────────────

def _cli():
    ap = argparse.ArgumentParser(description="Corpus FTS smoke test")
    ap.add_argument("terms", nargs="+", help="search terms")
    ap.add_argument("--sources", nargs="+", default=None,
                    help="restrict to specific source values")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--show-snippet", action="store_true",
                    help="print a 200-char snippet for each hit")
    ap.add_argument("--show-prompt", action="store_true",
                    help="print the formatted prompt block at the end")
    args = ap.parse_args()

    print(f"query terms: {args.terms}")
    if args.sources:
        print(f"restricted to sources: {args.sources}")
    print()
    results = fetch_context(args.terms, sources=args.sources, limit=args.limit)
    print(f"hits: {len(results)}")
    print()
    for i, r in enumerate(results, 1):
        title = (r["title"] or "")[:80]
        print(f"  {i}. [{r['source']}] {r['source_date']}  rank={r['rank']:.4f}")
        print(f"     {title}")
        if args.show_snippet:
            snippet = (r["snippet"] or "").strip().replace("\n", " ")[:200]
            print(f"     | {snippet}")
        print()
    if args.show_prompt:
        print("─" * 60)
        print(format_for_prompt(results))


if __name__ == "__main__":
    _cli()
