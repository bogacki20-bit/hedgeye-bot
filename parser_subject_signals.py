"""Shared subject-line signal extractor for the image-only Christian Drake
products (MOMO Tracker, CRYPTO QUANT).

Both products ship their tables as images; the only machine-readable signal
is in the subject, e.g.:
  "Mag7 (+0.5%), MSFT/ORCL=BULLISH, TSLA (+4%, BULLISH), ORCL To Bullish Trend"
  "₿TC (-0.9%), ETH/SOL/AVAX/XRP=BEARISH, XRP=BULLISH, SOL (+3.5%)"

extract_signals() returns a list of {token, pct_change, sentiment, raw}
dicts (one per asset mention), merged so a token seen with both a % and a
sentiment yields a single row carrying both.
"""
from __future__ import annotations

import re
from typing import Optional

_TICK = r"[A-Z0-9]{1,6}"

# TOKEN(S)=SENTIMENT  (left side may be slash-joined: ETH/SOL/AVAX=BEARISH)
_SENT_RE = re.compile(
    rf"(?P<toks>{_TICK}(?:/{_TICK})*)\s*=\s*"
    r"(?P<sent>BULLISH|BEARISH|NEUTRAL)", re.I)
# TICKER (+1.2%[, BULLISH])  optionally followed by  )=BULLISH
_PCT_RE = re.compile(
    r"(?P<tok>₿?\??TC|Mag7|[A-Za-z]{1,6})\s*\(\s*"
    r"(?P<pct>[+-]?\d+(?:\.\d+)?)\s*%\s*"
    r"(?:,\s*(?P<sent_in>BULLISH|BEARISH|NEUTRAL)\s*)?\)?"
    r"\s*=?\s*(?P<sent_out>BULLISH|BEARISH|NEUTRAL)?", re.I)
# TICKER To Bullish/Bearish/Neutral [Trend]
_TREND_RE = re.compile(
    r"\b(?P<tok>[A-Z]{2,6})\s+To\s+"
    r"(?P<sent>Bullish|Bearish|Neutral)\b", re.I)


def _norm_token(tok: str, normalize: dict[str, str]) -> str:
    t = tok.strip().upper()
    t = t.replace("₿", "").lstrip("?")          # ₿TC / ?TC -> TC
    return normalize.get(t, t)


def extract_signals(subject: str, *, normalize: dict[str, str],
                     stop: set[str], min_len: int = 2) -> list[dict]:
    if not subject:
        return []
    # Drop the product prefix ("MOMO Tracker |" / "CRYPTO QUANT |")
    s = re.sub(r"\s+", " ", subject)
    s = s.split("|", 1)[1] if "|" in s else s

    merged: dict[str, dict] = {}

    def put(tok: str, *, pct=None, sent=None, raw=""):
        key = _norm_token(tok, normalize)
        if not key or len(key) < min_len or key in stop:
            return
        row = merged.setdefault(
            key, {"token": key, "pct_change": None,
                  "sentiment": None, "raw": raw})
        if pct is not None:
            row["pct_change"] = pct
        if sent:
            row["sentiment"] = sent.lower()
        if raw and len(raw) > len(row["raw"]):
            row["raw"] = raw

    for m in _PCT_RE.finditer(s):
        try:
            pct = float(m.group("pct"))
        except (TypeError, ValueError):
            pct = None
        sent = m.group("sent_in") or m.group("sent_out")
        put(m.group("tok"), pct=pct, sent=sent, raw=m.group(0).strip())

    for m in _SENT_RE.finditer(s):
        sent = m.group("sent")
        for tok in m.group("toks").split("/"):
            put(tok, sent=sent, raw=m.group(0).strip())

    for m in _TREND_RE.finditer(s):
        put(m.group("tok"), sent=m.group("sent"), raw=m.group(0).strip())

    return list(merged.values())
