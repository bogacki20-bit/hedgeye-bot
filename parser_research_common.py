"""Shared extraction helpers for the Hedgeye research/narrative products
(The Call, Macro Show, Keith's Signals, Early Look, Market Situation
Report, Inflation Nowcast, etc.).

Nothing here touches the DB — pure text extraction so each product parser
can compose: feed_item_id, author handles, quad regime, ticker scan, and
the common "LONGS: a, b  SHORTS: c, d" / "BULLISH: ... BEARISH: ..."
position blocks.
"""
from __future__ import annotations

import re

FEED_ID_RE = re.compile(r"feed_items/(\d+)")
AUTHOR_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_]{2,30})")
QUAD_RE = re.compile(r"#?\bQuad\s*([1-4])\b", re.I)
TICKER_PAREN_RE = re.compile(r"\(([A-Z]{1,6}(?:\.[A-Z]{1,3})?)\)")
TICKER_TOK_RE = re.compile(r"\b[A-Z]{1,6}(?:\.[A-Z]{1,3})?\b")

# All-caps tokens that show up in these texts but are not tickers.
STOP = {
    "LONGS", "SHORTS", "LONG", "SHORT", "BULLISH", "BEARISH", "NEUTRAL",
    "TL", "DR", "AND", "THE", "OR", "US", "USD", "EU", "ETF", "CPI", "PPI",
    "GDP", "PCE", "ISM", "NFP", "VIX", "SPX", "SPY", "QQQ", "AI", "EPS",
    "ATH", "RR", "KM", "EDT", "EST", "PM", "AM", "TBD", "YTD", "DOD",
    "WTD", "MTD", "CEO", "CFO", "FX", "IP", "Q", "FY", "HEDGEYE", "VIEW",
    "LARGER", "IMAGE", "CLICK", "HERE", "PDF", "TODAY", "POSITIONS",
    "POSITION", "MONITOR", "MONITORS", "MENTIONED", "KEY", "TAKEAWAYS",
    "OIL", "GOLD", "DOWNSIDE", "UPSIDE", "MACRO", "SHOW", "DASHBOARD",
}


def strip_html(html: str) -> str:
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["style", "script"]):
            t.decompose()
        text = soup.get_text(" ", strip=True)
    except ImportError:
        text = re.sub(r"<[^>]+>", " ",
                      re.sub(r"<style[^>]*>.*?</style>", "", html,
                             flags=re.S | re.I))
    return re.sub(r"\s+", " ", text).strip()


def text_of(text_body, html_body) -> str:
    if text_body:
        return re.sub(r"\s+", " ", text_body).strip()
    return strip_html(html_body or "")


def feed_item_id(body: str):
    m = FEED_ID_RE.search(body or "")
    return int(m.group(1)) if m else None


def authors(body: str) -> list[str]:
    seen, out = set(), []
    for m in AUTHOR_RE.finditer(body or ""):
        a = m.group(1).lower()
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out[:6]


def quad(body: str):
    m = QUAD_RE.search(body or "")
    return f"Quad {m.group(1)}" if m else None


def clean_tickers(blob: str, *, paren_only: bool = False,
                   limit: int = 60) -> list[str]:
    """Tickers from a fragment. paren_only=True keeps only "(TICKER)"
    forms (used where free prose would otherwise yield noise)."""
    out: list[str] = []
    if paren_only:
        it = (m.group(1) for m in TICKER_PAREN_RE.finditer(blob or ""))
    else:
        it = (m.group(0) for m in TICKER_TOK_RE.finditer(blob or ""))
    for raw in it:
        tk = raw.upper()
        if len(tk) >= 1 and tk not in STOP and tk not in out:
            out.append(tk)
        if len(out) >= limit:
            break
    return out


# "LONGS: a, b, c   SHORTS: d, e" — also matches a "BULLISH:/BEARISH:" pair.
_SIDE_BLOCK_RE = re.compile(
    r"\b(?P<side>LONGS?|SHORTS?|BULLISH|BEARISH)\s*:\s*"
    r"(?P<seg>.+?)(?=\b(?:LONGS?|SHORTS?|BULLISH|BEARISH|NEUTRAL)\s*:|"
    r"Please\s+visit|\(\s*VIEW|TL;?DR|$)",
    re.I | re.S,
)


def side_blocks(body: str, *, paren_only: bool = False) -> list[dict]:
    """Return [{ticker, side}] from LONGS:/SHORTS:/BULLISH:/BEARISH: blocks.
    side is normalised to 'long' or 'short'."""
    rows, seen = [], set()
    for m in _SIDE_BLOCK_RE.finditer(body or ""):
        label = m.group("side").lower()
        side = "long" if label.startswith(("long", "bull")) else "short"
        for tk in clean_tickers(m.group("seg")[:600], paren_only=paren_only):
            key = (tk, side)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"ticker": tk, "side": side})
    return rows
