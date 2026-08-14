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


# ─────────────────────────── Quad tilt extraction ────────────────────
#
# Hedgeye announces a regime change as a TILT: "our tilt from Quad 3 to
# Quad 4 for both July and Q3". The naive `quad()` above grabs the FIRST
# Quad mention — which is the tilt-FROM (Quad 3), the regime we are
# LEAVING. That silently masks the actual call. `tilt_target_quads()`
# reads the DESTINATION Quad and assigns it to the monthly / quarterly /
# global axis, honouring forward-dated month references ("for July" when
# the email is published in June → the monthly flip is effective July 1).

import datetime as _dt

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "tilt/shift/move/rotate ... from Quad X to Quad Y"  → destination Y.
_FROM_TO_RE = re.compile(
    r"\bfrom\s+#?Quad\s*([1-4])\s+to\s+#?Quad\s*([1-4])", re.I)
# Directional "shifting/tilting/moving/rotating/chopping (in)to Quad Y" with
# no explicit FROM — destination Y. Also catches "in favor of Quad Y".
_TO_ONLY_RE = re.compile(
    r"\b(?:tilt\w*|shift\w*|mov\w*|rotat\w*|chopping|lean\w*|"
    r"in\s+favor\s+of|favo\w*r?s?|now\s+a)\s+"
    r"(?:marginally\s+|heavily\s+|sizeabl[ey]\s+|toward[s]?\s+|"
    r"(?:in)?to\s+|a\s+){0,3}#?Quad\s*([1-4])", re.I)
_MONTH_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
    r"nov(?:ember)?|dec(?:ember)?)\b", re.I)
_QUARTER_RE = re.compile(r"\bQ([1-4])\b")


def _next_month_start(month_num: int, ref: _dt.date) -> _dt.date:
    """First day of the next occurrence of `month_num` on/after `ref`'s
    month. Same-month → ref's month this year; earlier month → next year."""
    year = ref.year + 1 if month_num < ref.month else ref.year
    return _dt.date(year, month_num, 1)


def _periods_in_window(text: str, start: int, end: int,
                       fwd: int = 60, back: int = 24) -> tuple[set, set]:
    """Return (month_nums, quarter_nums) mentioned in a window around the
    [start,end) span. Stops the forward scan at clause breaks so a tilt
    'for July and Q3 and our June CPI Nowcast' doesn't sweep in 'June'."""
    seg = text[max(0, start - back): min(len(text), end + fwd)]
    # Cut the forward context at the first clause that introduces an
    # unrelated subject ("and our", "CPI", "Nowcast", a period, etc).
    cut = re.split(r"\band our\b|\bCPI\b|Nowcast|[.;]", seg, maxsplit=1,
                   flags=re.I)[0]
    months = {_MONTHS[m.group(1).lower()] for m in _MONTH_RE.finditer(cut)}
    quarters = {int(m.group(1)) for m in _QUARTER_RE.finditer(cut)}
    return months, quarters


def tilt_target_quads(subject: str, body: str,
                      ref_date: _dt.date | None = None) -> dict | None:
    """Extract the DESTINATION Quad(s) of any regime tilt in this note.

    Returns a structured dict (or None when no tilt/Quad call is present):

        {"effective_monthly":        "Quad 4" | None,
         "effective_quarterly":      "Quad 4" | None,
         "effective_global":         "Quad 4" | None,
         "monthly_effective_from":   "2026-07-01" | None,   # ISO, or None=now
         "quarterly_effective_from": None}                  # quarterly=immediate

    Period assignment:
      * A tilt phrase whose window names a quarter (Q1-Q4) → quarterly axis.
      * A tilt phrase whose window names a month → monthly axis; if that
        month is in the FUTURE relative to ref_date, the monthly flip is
        forward-dated to the 1st of that month (operator switches on the
        1st, not on publication). Quarterly tilts apply immediately.
      * A tilt phrase with no period context is GENERAL — it seeds both the
        monthly and quarterly axes (unless a period-specific phrase already
        set that axis).
    """
    subject = subject or ""
    body = body or ""
    ref_date = ref_date or _dt.date.today()
    text = f"{subject}. {body}"

    monthly = quarterly = general = None
    from_monthly = from_quarterly = from_general = None
    monthly_from = None

    # 1. Explicit "from Quad X to Quad Y" — strongest signal, period-aware.
    #    The FROM quad is the regime being LEFT; it's the value the bot must
    #    hold an axis at while a forward-dated tilt is not yet effective.
    for m in _FROM_TO_RE.finditer(text):
        src = f"Quad {m.group(1)}"
        dest = f"Quad {m.group(2)}"
        months, quarters = _periods_in_window(text, m.start(), m.end())
        if quarters:
            quarterly, from_quarterly = dest, src
        if months:
            monthly, from_monthly = dest, src
            fut = sorted(mo for mo in months if mo != ref_date.month)
            if fut:
                monthly_from = _next_month_start(min(fut), ref_date).isoformat()
        if not months and not quarters:
            general = general or dest
            from_general = from_general or src

    # 2. Directional "shifting/chopping into Quad Y" (subject + body) — fills
    #    any axis still empty. Subject "Chopping Into #Quad4?" lands here.
    if not (monthly and quarterly):
        for m in _TO_ONLY_RE.finditer(text):
            dest = f"Quad {m.group(1)}"
            months, quarters = _periods_in_window(text, m.start(), m.end())
            if quarters and not quarterly:
                quarterly = dest
            if months and not monthly:
                monthly = dest
                fut = sorted(mo for mo in months if mo != ref_date.month)
                if fut:
                    monthly_from = _next_month_start(
                        min(fut), ref_date).isoformat()
            if not months and not quarters:
                general = general or dest

    # NB: there is deliberately NO bare-"Quad N" fallback. A note that only
    # mentions a Quad in passing (no tilt / favored / directional cue) must
    # NOT seed a regime target — otherwise the live updater would churn the
    # regime on incidental references. Only the directional/from-to/favored
    # phrasings above produce a target.
    if not (monthly or quarterly or general):
        return None

    # A general (period-less) target seeds whichever axis is still empty.
    eff_monthly = monthly or general
    eff_quarterly = quarterly or general
    # Forward-date only applies to a monthly target that named a future month.
    if eff_monthly != monthly:
        monthly_from = None

    return {
        "effective_monthly":        eff_monthly,
        "effective_quarterly":      eff_quarterly,
        "effective_global":         None,
        "monthly_effective_from":   monthly_from,
        "quarterly_effective_from": None,
        # The regime the tilt is LEAVING — used to hold an axis at the
        # correct pre-tilt value while a forward-dated tilt is pending.
        "from_monthly":             from_monthly or from_general,
        "from_quarterly":           from_quarterly or from_general,
    }


# ─────────────────────────── Deck / PDF link discovery ───────────────
#
# Research-note emails link the actual slide deck behind a CTA ("CLICK
# HERE FOR THE UPDATED 2Q26 MACRO THEMES DECK"). The <a> usually wraps
# only "CLICK HERE" and the deck href is a Hedgeye click-tracker
# (url63.hedgeye.com/ls/click?...) that 30x-redirects to the real PDF, so
# we match on the link TEXT plus the trailing copy, and exclude the
# obvious non-deck links (unsubscribe / preferences / browser view).

_ANCHOR_RE = re.compile(r"<a\b[^>]*\bhref=\"([^\"]+)\"[^>]*>(.*?)</a>",
                        re.I | re.S)
_DECK_CUE_RE = re.compile(
    r"\b(DECK|DOWNLOAD|FULL\s+REPORT|FULL\s+DECK|MACRO\s+THEMES|"
    r"VIEW\s+THE\s+(?:DECK|REPORT|PRESENTATION)|PRESENTATION|SLIDES?)\b",
    re.I)
_DECK_SKIP_RE = re.compile(
    r"unsubscrib|preferenc|update\s+your|in\s+your\s+browser|view\s+it|"
    r"privacy|terms|contact|twitter|facebook|linkedin|youtube|mailto:",
    re.I)


def deck_pdf_url(html_body: str) -> str | None:
    """Best-effort discovery of the slide-deck URL linked from a research
    note email. Prefers a direct ``.pdf`` href; otherwise returns the CTA
    link whose anchor text + trailing copy reads like a deck/report CTA.
    Returns None when nothing deck-like is found."""
    html = html_body or ""
    if not html:
        return None

    anchors = list(_ANCHOR_RE.finditer(html))

    # 1. A direct PDF href wins outright.
    for m in anchors:
        href = m.group(1).strip()
        if ".pdf" in href.lower() and href.lower().startswith("http"):
            return href

    # 2. CTA link: anchor text + ~120 chars of following copy must read like
    #    a deck CTA, and the link must not be an obvious utility link.
    for m in anchors:
        href = m.group(1).strip()
        if not href.lower().startswith("http"):
            continue
        atext = re.sub(r"<[^>]+>", " ", m.group(2))
        tail = re.sub(r"<[^>]+>", " ", html[m.end(): m.end() + 240])
        context = re.sub(r"\s+", " ", f"{atext} {tail}").strip()
        if _DECK_SKIP_RE.search(atext):
            continue
        if _DECK_CUE_RE.search(context):
            return href
    return None


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
#
# 2026-08-02 — this minted BEEN, FROM, HAS, JUST, LIST and SIGNAL into
# ticker_inventory (source=keiths_signals, all six in a two-second window on
# 7/29), and from there into the MFR enrollment backlog under a "paste into
# Activate Assets" instruction.
#
# Two compounding defects:
#   * seg was `.+?` under re.S, so it ran across newlines until it found a
#     terminator — if the email had no "Please visit"/"TL;DR" tail, that meant
#     600 characters of prose (clean_tickers' cap) fed to a token matcher.
#   * the whole pattern carried re.I, which would also have made any lowercase
#     stop-character useless, since [a-z] matches uppercase under IGNORECASE.
#
# Fix: the segment is [^a-z], so a ticker list ENDS at the first lowercase
# letter — which is where the list ends in every real email. IGNORECASE is
# applied to the labels only, via (?i:...), so "Longs:" and "LONGS:" both match
# while the bound stays real. TICKER_TOK_RE allows 1-6 chars, which is why
# SIGNAL could come from here and not from parser_signal_strength's 1-5 matcher.
# 2026-08-14 — a SECOND defect from the same 7/29 intra-week email. Its body
# carried a NEW compound header "Signal Strength Longs/Shorts:" (a colon after
# "Longs/Shorts" the weekly never had). The "Shorts:" inside that header matched
# as a block label, and because \s*:\s* consumed the space, seg began on the 'L'
# of the very next "LONGS:" — the [^a-z]+? lookahead can't re-anchor from "ONGS:",
# so seg swallowed the entire LONGS block as side=short. Every long was mis-stored
# short from 2026-07-29 onward, silently, for ~2.5 weeks. Two fixes:
#   (?<!/) — a label fused to a "Longs/Shorts:" compound header is not a block.
#   seg's inline (?!<label>:) — seg can never consume into the next block's label,
#     regardless of case, so a block cannot swallow the next block even when the
#     two are label-adjacent (the case the plain lookahead could not catch).
# Neither change drops real tickers: real ticker lists contain no LONGS:/SHORTS:
# tokens, and real block labels are never preceded by '/'.
_SIDE_BLOCK_RE = re.compile(
    r"(?<!/)\b(?P<side>(?i:LONGS?|SHORTS?|BULLISH|BEARISH))\s*:\s*"
    r"(?P<seg>(?:(?!(?i:LONGS?|SHORTS?|BULLISH|BEARISH|NEUTRAL)\s*:)[^a-z])+?)"
    r"(?=(?i:LONGS?|SHORTS?|BULLISH|BEARISH|NEUTRAL)\s*:|"
    r"(?i:Please\s+visit)|\(\s*VIEW|(?i:TL;?DR)|[a-z]|$)",
    re.S,
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
