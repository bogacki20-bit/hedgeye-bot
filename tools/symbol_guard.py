"""Write-time symbol validation, shared by every parser that extracts ticker
tokens from prose, subject lines, or OCR transcription.

Why this exists (2026-08-23): the parsers each rolled their own token regex
plus a hand-curated stopword list, and whatever survived was stored verbatim.
Storage accumulated three malformation classes — suffix fragments (RPI.L
split into RPI + L by a suffix-blind \\b[A-Z]{1,5}\\b), OCR/prose word-tokens
(MORRIS from "PHILIP MORRIS", WIDEST, BUXXX), and raw option contract
strings — and every downstream consumer (price polling, screens, alerts,
enrollment) inherited them. The MFR backlog gate catches this at PRINT time;
this module is the WRITE-time gate so nothing unresolvable enters storage in
the first place. Keep both: defense in depth.

Philosophy (same as _junk_sweep): COVERAGE decides membership, not spelling.
No wordlists. A token is storable when it is instrument-shaped AND either
already known to the bot's curated stores or resolvable against a live quote
source. Unknown + unresolvable => dropped and LOGGED with the source tag —
never silently.

Fail-open rule: when the live probe itself is down (network, rate limit),
an unknown-but-plausible token is KEPT and logged loudly. Ingest
availability beats purity — a real-time Keith signal must not be lost to a
yfinance outage — and the enrollment print-gate still stands downstream.
"""
from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)

# Instrument shape: 1-7 alnum chars, up to two suffix groups joined by . or -
# (RPI.L, 005930.KS, 1913.HK, ADS.DE, VOLV-B.ST, BRK-B), plus the stored
# futures form X_F (ES_F, FESB_F). At least one LETTER somewhere — a pure
# numeric token ('2513', '100.00') is an OCR artifact, not a ticker.
SHAPE_RE = re.compile(r"^[A-Z0-9]{1,7}(?:[.\-][A-Z0-9]{1,4}){0,2}(?:_F)?$")

# Option contract forms: Fidelity's leading '-' style (-XLV260717C230) and
# anything carrying an embedded YYMMDD C/P strike. Never an equity ticker.
OPTION_RE = re.compile(r"^-|\d{6}[CP][\d.]+")

# Deliberate pseudo-instruments Hedgeye publishes that no quote source
# carries. MAG7 is the momo tracker's Mag7 basket — parser_momo normalizes
# MAG -> MAG7 on purpose. Storable in signal tables; excluded from MFR
# enrollment via KNOWN_UNCOVERABLE.
PSEUDO_INSTRUMENTS = frozenset({"MAG7"})


def plausible(t: str) -> bool:
    """Instrument-shaped: matches SHAPE_RE, contains a letter, is not an
    option contract string. Index notation (^VIX, ^RVX) is instrument-shaped.
    Pure shape — no membership, no network."""
    if not t:
        return False
    t = t.strip().upper().lstrip("^")
    return (bool(SHAPE_RE.match(t)) and bool(re.search(r"[A-Z]", t))
            and not OPTION_RE.search(t))


# ─────────────────────────── known universe ─────────────────────────────────

_KNOWN: set = set()
_KNOWN_AT: float = 0.0
_KNOWN_TTL = 6 * 3600.0


def _alias_names() -> set:
    """Symbols the code itself knows how to handle — alias-map keys AND
    values. USD/GOLD/VIX-style macro names live here, not in any table."""
    out: set = set(PSEUDO_INSTRUMENTS)
    try:
        from tools.ticker_aliases import ALIASES
        out |= {k.upper() for k in ALIASES} | {v.upper() for v in ALIASES.values()}
    except Exception:
        pass
    try:
        from mfr_client import MFR_ALIASES
        out |= {k.upper() for k in MFR_ALIASES}
    except Exception:
        pass
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE
        out |= {k.upper() for k in HEDGEYE_TO_YFINANCE}
    except Exception:
        pass
    return out


def known_universe(refresh: bool = False) -> set:
    """Union of the bot's curated symbol stores, cached 6h per process.

    Deliberately EXCLUDES the mention tables (hedgeye_ticker_inventory /
    _history) and the roster tables the suffix-blind parsers write — those
    are the polluted stores this module exists to protect; including them
    would let garbage validate itself. Included stores earn membership by
    an external act: an operator tagged it (ticker_tags), Fidelity holds it
    (book_positions), MFR ranges it (mfr_snapshots), or Hedgeye published
    structured values for it (hedgeye_risk_ranges, hedgeye_etf_pro_ranges).

    Never raises: on DB failure returns the stale cache (or just the alias
    names), and validate_for_storage fails open accordingly."""
    global _KNOWN, _KNOWN_AT
    if _KNOWN and not refresh and (time.time() - _KNOWN_AT) < _KNOWN_TTL:
        return _KNOWN
    names = _alias_names()
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for sql in (
                "SELECT DISTINCT upper(ticker) FROM ticker_tags",
                "SELECT DISTINCT upper(underlying) FROM book_positions "
                "WHERE underlying IS NOT NULL AND asset_class <> 'cash'",
                "SELECT DISTINCT upper(ticker) FROM mfr_snapshots",
                "SELECT DISTINCT upper(ticker) FROM hedgeye_risk_ranges",
                "SELECT DISTINCT upper(ticker) FROM hedgeye_etf_pro_ranges",
            ):
                cur.execute(sql)
                names |= {r[0] for r in cur.fetchall() if r[0]}
        _KNOWN, _KNOWN_AT = names, time.time()
    except Exception as e:
        log.warning("symbol_guard: known_universe refresh failed (%s) — "
                    "using %s", e, "stale cache" if _KNOWN else "alias names only")
        return _KNOWN or names
    return _KNOWN


# ─────────────────────────── live resolution ────────────────────────────────

def yf_symbol_for(t: str) -> str:
    """Storage symbol -> yfinance symbol. Shared alias map plus X_F -> X=F."""
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE
        s = HEDGEYE_TO_YFINANCE.get(t, t)
    except Exception:
        s = t
    if s == t and t.endswith("_F"):
        s = t[:-2] + "=F"
    return s


def resolve_live(t: str):
    """True: at least one daily close in the last month. False: quote source
    answered and knows nothing (the junk verdict). None: the probe itself
    failed — the caller must fail OPEN, not treat it as junk.

    Class-share dot forms (BF.B) retry as yahoo's dash form (BF-B) before a
    negative verdict — the dot form is how Hedgeye writes them."""
    try:
        import yfinance as yf
        sym = yf_symbol_for(t)
        h = yf.Ticker(sym).history(period="1mo", interval="1d")
        if h is not None and not h.empty:
            return True
        if "." in sym:
            h = yf.Ticker(sym.replace(".", "-")).history(period="1mo",
                                                         interval="1d")
            if h is not None and not h.empty:
                return True
        return False
    except Exception as e:
        log.warning("symbol_guard: live probe for %s errored (%s)", t, e)
        return None


# ─────────────────────────── the write gate ─────────────────────────────────

def validate_for_storage(tokens, source: str, probe_unknown: bool = True):
    """Gate a parser's extracted tokens before they are persisted.

    Returns (kept, dropped) preserving input order and duplicates in `kept`.
    Every drop is logged with the source tag; a summary line carries the
    count so the failure is visible in the parse log, never silent.
    """
    kept, dropped = [], []
    known = None            # resolved lazily — cheap parses stay cheap
    for raw in tokens:
        t = (raw or "").strip().upper()
        if not t or OPTION_RE.search(t):
            # Option contract strings are rejected unconditionally — even
            # membership can't launder one (a raw contract stored as a book
            # underlying must never self-validate).
            dropped.append(raw)
            log.warning("symbol_guard[%s]: dropped option-shaped/empty "
                        "token %r", source, raw)
            continue
        if known is None:
            known = known_universe()
        if t in known:
            # Membership beats shape: legit macro instruments the stores
            # already hold (USD/YEN, CAD/USD) are not equity-shaped and
            # must still record.
            kept.append(raw)
            continue
        if not plausible(t):
            dropped.append(raw)
            log.warning("symbol_guard[%s]: dropped malformed token %r",
                        source, raw)
            continue
        if not probe_unknown:
            dropped.append(raw)
            log.warning("symbol_guard[%s]: dropped unknown token %r "
                        "(probe disabled)", source, raw)
            continue
        live = resolve_live(t)
        if live:
            kept.append(raw)
            log.info("symbol_guard[%s]: NEW symbol %s accepted via live "
                     "quote", source, t)
        elif live is None:
            kept.append(raw)     # fail open — probe outage is not evidence
            log.warning("symbol_guard[%s]: kept UNVERIFIED token %r — live "
                        "probe unavailable", source, raw)
        else:
            dropped.append(raw)
            log.warning("symbol_guard[%s]: dropped unresolvable token %r "
                        "(unknown to all stores, no live quote)", source, raw)
    if dropped:
        log.warning("symbol_guard[%s]: dropped %d token(s): %s",
                    source, len(dropped),
                    " ".join(str(d) for d in dropped[:20]))
    return kept, dropped


def filter_rows(rows, source: str, key: str = "ticker",
                probe_unknown: bool = True):
    """Convenience for the parsers' row-dict shape: keep rows whose
    row[key] validates. Returns (kept_rows, dropped_symbols)."""
    kept_syms, _ = validate_for_storage([r.get(key) for r in rows], source,
                                        probe_unknown)
    allowed = set(s.strip().upper() for s in kept_syms if s)
    kept = [r for r in rows
            if (r.get(key) or "").strip().upper() in allowed]
    dropped = [r.get(key) for r in rows
               if (r.get(key) or "").strip().upper() not in allowed]
    return kept, dropped
