"""SCREEN v2 — natural-language screener over v_screener (ticker_tags + latest
mfr_snapshots + TREND). Python owns ALL math/filtering; no LLM.

trend_dir = COALESCE(Hedgeye RR, MFR trend_signal), OVERRIDDEN by BTC Quant for crypto
names — the SAME field the gate and display read. trend_source ('hdg'/'btcq'/'mfr')
shown per row. For crypto names BTC Quant is THE trend authority (operator doctrine):
BTC Quant > Hedgeye RR > MFR.

Telegram: `SCREEN <sentence>` e.g. `SCREEN energy shorts top of range`. Follow-up
replies (a bare direction like "longs", or a modifier like "show gated") merge into
the chat's pending query and execute immediately — the results message IS the
acknowledgment, never a bare "Got it". Pending state is in-memory (no DB), cleared
on execution or after 10 minutes. Unrecognized tokens get a "didn't recognize"
reply — the handler never exits silently.

Rules:
  range_pos   = (price - range_low)/(range_high-range_low)          [view]
  momentum_ok = MFR momentum_signal is momentumBullish              [view; no history]
  divergence  = MFR trade (trend_signal) vs momentum disagree — exhaustion-fade ⚡
  hurst       = MFR Hurst (>0.5 trending, <0.5 mean-reverting)      [view]
  iv/rv/ivpd  = MFR vol fields (authoritative)                      [view]
  corrSPY/corrUUP = bot-COMPUTED Pearson on daily returns vs SPY/UUP (calc, not MFR)
  near_bottom = range_pos <= 0.20 ; near_top = range_pos >= 0.80
  TREND gate MANDATORY, direction-tied (Rule-1 — evaluates the COALESCEd trend_dir):
    longs  -> active_long/top_idea_long/long_bench   AND trend_dir='BULLISH'
    shorts -> active_short/top_idea_short/short_bench AND trend_dir='BEARISH'
  Tier markers: ●● active · ● top-idea · · bench.  Sort range_pos ASC (longs)/DESC (shorts).
"""
from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)
SENTINEL = "SCREEN"
CORR_MIN_N = 20        # min overlapping daily returns to report a correlation
_PENDING_TTL = 600.0   # 10 min — pending SCREEN state per chat (in-memory)

# NL sector phrases -> canonical gics_sector (longest/most-specific first).
_SECTORS = [
    (r"health\s*care|healthcare",                    "Health Care"),
    (r"consumer\s+discretionary|discretionary",      "Consumer Discretionary"),
    (r"consumer\s+staples|staples",                  "Consumer Staples"),
    (r"communication(?:s)?(?:\s+services)?",         "Communication Services"),
    (r"information\s+technology|technology|\btech\b", "Technology"),
    (r"financials?|\bbanks?\b",                      "Financials"),
    (r"industrials?",                                 "Industrials"),
    (r"materials?",                                   "Materials"),
    (r"\benergy\b",                                   "Energy"),
    (r"utilit(?:ies|y)",                              "Utilities"),
    (r"real\s+estate|\breits?\b",                     "Real Estate"),
    (r"digital\s+assets?|crypto",                     "Digital Assets"),
]
_SECTOR_NAMES = [canon for _, canon in _SECTORS]

_NEAR_BOTTOM = r"bottom of (?:the )?range|near (?:the )?(?:low|bottom)|close to (?:the )?(?:low|bottom)"
_NEAR_TOP    = r"top of (?:the )?range|near (?:the )?(?:high|top)|close to (?:the )?(?:high|top)"
# "book" is filter-vocabulary (removed from _FILLER): my book / the book /
# book longs/shorts / bare "book" all flag held, alongside the ownership verbs.
_HELD        = (r"in my book|\bmy book\b|\bthe book\b|book\s+(?:longs?|shorts?)|\bbook\b"
                r"|that i own|\bi own\b|\bi hold\b|\bheld\b|that i hold"
                r"|that i'?m holding|\bholding\b")
_GATED       = r"show gated|show all|include gated|with gated|\bgated\b"
# Cloud = the MFR long-term / trend range (the yellow band). Price vs that band.
_CLOUD_ABOVE = r"above (?:the )?cloud|above (?:the )?trend range|over (?:the )?cloud"
_CLOUD_BELOW = r"below (?:the )?cloud|below (?:the )?trend range|under (?:the )?cloud"
_CLOUD_IN    = r"in (?:the )?cloud|inside (?:the )?cloud|in (?:the )?trend range"

# Words that legitimately appear in a screen sentence but aren't screen tokens —
# excluded from the "unrecognized" check so we don't flag connective/position words.
_FILLER = {
    "all", "the", "a", "an", "of", "in", "my", "to", "for", "me", "up", "and", "or",
    "with", "that", "i", "range", "list", "them",   # own/book/hold/holding -> _HELD vocab
    "show", "near", "close", "top", "bottom", "low", "high", "at", "on", "side",
    "most", "down", "bring", "give", "screen", "please", "bullish", "bearish",
    "tickers", "names", "stocks", "im", "are", "is", "be", "want", "see",
}

# v2 — all three tiers screenable (bench included); TREND gate still applies.
_DIR_BUCKETS = {
    "longs":  (["active_long", "top_idea_long", "long_bench"],   "BULLISH"),
    "shorts": (["active_short", "top_idea_short", "short_bench"], "BEARISH"),
}

# In-memory pending SCREEN query per chat_id. No DB writes (the listener is a single
# long-running thread, so a module dict persists across messages within the process).
_PENDING: dict = {}

# Source phrases (etf pro / portfolio solutions / signal strength / …) -> registry tag,
# longest phrase first so "signal strength" wins over "strength". 'book' is intentionally
# excluded — it's handled by _HELD as a filter, not a base source.
_SOURCE_PHRASES = None


def _source_phrase_list():
    global _SOURCE_PHRASES
    if _SOURCE_PHRASES is None:
        from tools.source_registry import REGISTRY
        pairs = []
        for src in REGISTRY:
            if src.tag == "book":
                continue
            for alias in [src.tag] + list(src.aliases):
                pairs.append((alias, src.tag))
        _SOURCE_PHRASES = sorted(pairs, key=lambda p: -len(p[0]))
    return _SOURCE_PHRASES


def _source_label(tag) -> str:
    from tools.source_registry import BY_TAG
    s = BY_TAG.get(tag)
    return s.name if s else (tag or "")


def _tier(bucket) -> str:
    b = bucket or ""
    if b.startswith("active"):   return "●●"   # active
    if b.startswith("top_idea"): return "●"    # top idea
    if b:                        return "·"    # bench
    return "—"                                 # untagged (book-only, e.g. an ETF)


def parse_query(text: str) -> dict:
    """Parse a natural-language screen into a filter dict. Pure regex, no LLM.
    Also records any tokens that didn't resolve to a sector/direction/modifier in
    q['unrecognized'] so the handler can reply instead of silently ignoring them."""
    s = " " + (text or "").lower() + " "
    q: dict = {"sector": None, "direction": None, "near": None, "momentum": False,
               "held": False, "show_gated": False, "source": None, "cloud": None,
               "unrecognized": [], "raw": (text or "").strip()}
    consumed: list = []   # (start, end) spans of matched screen tokens

    # Source lens (etf pro / keiths / portfolio solutions / …). Consumed here so its
    # words never trip the unrecognized-token guard (the "signal strength" failure).
    for alias, tag in _source_phrase_list():
        m = re.search(r"\b" + re.escape(alias) + r"\b", s)
        if m:
            q["source"] = tag
            consumed.append(m.span())
            break

    for pat, canon in _SECTORS:
        m = re.search(pat, s)
        if m:
            q["sector"] = canon
            consumed.append(m.span())
            break
    m = re.search(r"\bshorts?\b", s)
    if m:
        q["direction"] = "shorts"; consumed.append(m.span())
    else:
        m = re.search(r"\blongs?\b", s)
        if m:
            q["direction"] = "longs"; consumed.append(m.span())
    m = re.search(_NEAR_BOTTOM, s)
    if m:
        q["near"] = "bottom"; consumed.append(m.span())
    else:
        m = re.search(_NEAR_TOP, s)
        if m:
            q["near"] = "top"; consumed.append(m.span())
    m = re.search(r"momentum", s)
    if m:
        q["momentum"] = True; consumed.append(m.span())
    m = re.search(_HELD, s)
    if m:
        q["held"] = True; consumed.append(m.span())
    m = re.search(_GATED, s)
    if m:
        q["show_gated"] = True; consumed.append(m.span())
    for pat, val in ((_CLOUD_ABOVE, "above"), (_CLOUD_BELOW, "below"), (_CLOUD_IN, "in")):
        m = re.search(pat, s)
        if m:
            q["cloud"] = val; consumed.append(m.span()); break

    # Whatever meaningful words are left after blanking the matched spans are unknown.
    chars = list(s)
    for a, b in consumed:
        for i in range(a, b):
            chars[i] = " "
    rest = "".join(chars)
    q["unrecognized"] = [w for w in re.findall(r"[a-z0-9][a-z0-9.\-]*", rest)
                         if w not in _FILLER and len(w) > 1]
    return q


def _has_signal(q: dict) -> bool:
    return bool(q.get("sector") or q.get("direction") or q.get("near")
                or q.get("momentum") or q.get("held") or q.get("show_gated")
                or q.get("source") or q.get("cloud"))


def _is_modifier_only(q: dict) -> bool:
    return (bool(q.get("near") or q.get("momentum") or q.get("held") or q.get("show_gated"))
            and not q.get("direction") and not q.get("sector"))


# ─────────────────────────── pending state (in-memory) ───────────────────────────

def _get_pending(chat_id):
    if chat_id is None:
        return None
    p = _PENDING.get(chat_id)
    if not p:
        return None
    if time.time() - p["ts"] > _PENDING_TTL:
        _PENDING.pop(chat_id, None)
        return None
    return p["q"]


def _set_pending(chat_id, q):
    if chat_id is not None:
        _PENDING[chat_id] = {"q": q, "ts": time.time()}


def _clear_pending(chat_id):
    if chat_id is not None:
        _PENDING.pop(chat_id, None)


def _merge(pend: dict, fu: dict) -> dict:
    q = dict(pend)
    if fu.get("sector"):    q["sector"] = fu["sector"]
    if fu.get("direction"): q["direction"] = fu["direction"]
    if fu.get("near"):      q["near"] = fu["near"]
    if fu.get("momentum"):  q["momentum"] = True
    if fu.get("held"):      q["held"] = True
    if fu.get("show_gated"): q["show_gated"] = True
    if fu.get("source"):    q["source"] = fu["source"]
    if fu.get("cloud"):     q["cloud"] = fu["cloud"]
    q["unrecognized"] = []
    q["raw"] = (f"{pend.get('raw','')} {fu.get('raw','')}").strip()
    return q


def _describe(q: dict) -> str:
    bits = []
    if q.get("direction"): bits.append(q["direction"].upper())
    if q.get("source"):    bits.append(_source_label(q["source"]))
    if q.get("sector"):    bits.append(q["sector"])
    if q.get("near"):      bits.append(f"near_{q['near']}")
    if q.get("cloud"):     bits.append(f"{q['cloud']}-cloud")
    if q.get("momentum"):  bits.append("momentum")
    if q.get("held"):      bits.append("in-book")
    if q.get("show_gated"): bits.append("show-gated")
    return " · ".join(bits)


_CMD_HINT = ("Sectors: " + ", ".join(_SECTOR_NAMES) + ".\n"
             "Modifiers: longs/shorts · near the top / near the bottom · with momentum "
             "· in my book · show gated.")


def _token_guard(unknown) -> str:
    toks = ", ".join(f"'{u}'" for u in unknown)
    return f"🔎 Didn't recognize {toks}.\n{_CMD_HINT}"


def _orphan_hint() -> str:
    return ("🔎 That's a SCREEN modifier, but no screen is active (or it expired). "
            "Start one first, e.g. `SCREEN energy longs` — then follow-ups like "
            "`show gated` / `near the top` apply to it.\n" + _CMD_HINT)


# ─────────────── correlation (bot-computed, not MFR) ───────────────
# Pearson on daily returns aligned to the SPY/UUP session calendar. mfr_snapshots
# is the deeper source (SPY 46 / UUP 51 bars vs 1/1 in yahoo_snapshots).

def _daily_returns(cur, ticker) -> dict:
    cur.execute("SELECT snapshot_date, price FROM mfr_snapshots "
                "WHERE ticker=%s AND price IS NOT NULL ORDER BY snapshot_date", (ticker,))
    out, prev = {}, None
    for d, p in cur.fetchall():
        p = float(p)
        if prev is not None and prev > 0 and p > 0:
            out[d] = p / prev - 1.0
        prev = p
    return out


def _pearson(xs, ys):
    n = len(xs)
    if n < CORR_MIN_N:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / ((vx * vy) ** 0.5)


def _corr_for(tickers) -> dict:
    """{ticker: (corrSPY, corrUUP)} — None per leg when <CORR_MIN_N overlapping days."""
    out = {}
    if not tickers:
        return out
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        spy, uup = _daily_returns(cur, "SPY"), _daily_returns(cur, "UUP")
        for t in tickers:
            r = _daily_returns(cur, t)
            def c(bench):
                common = sorted(set(r) & set(bench))
                return _pearson([r[d] for d in common], [bench[d] for d in common])
            out[t] = (c(spy), c(uup))
    return out


_SLICE_COLS = ("SELECT ticker, subsector, hedgeye_bucket_0629, range_pos, momentum_ok, "
               "momentum_dir, divergence, hurst, iv, rv, ivpd, trend_dir, trend_source, "
               "held, has_range FROM v_screener")


def _rows(sql, args):
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fetch_tag_slice(sector, buckets):
    """All v_screener rows in the requested sector + direction buckets (tag filters
    only — no trend/range/momentum gates yet)."""
    sql = _SLICE_COLS + " WHERE hedgeye_bucket_0629 = ANY(%s)"
    args = [buckets]
    if sector:
        sql += " AND gics_sector = %s"
        args.append(sector)
    return _rows(sql, args)


def _fetch_book_slice(sector):
    """The 'my book' universe: every held name (book_positions underlyings, latest
    snapshot — cash/zero-qty already excluded in the view), NOT bucket-filtered, so
    untagged holdings (sector ETFs like XLV) are included. Direction is enforced
    downstream by the mandatory TREND gate, not by bucket. Sector filter still applies
    where a name is tagged (untagged ETFs have NULL sector and drop out of a sector
    screen, as intended)."""
    sql = _SLICE_COLS + " WHERE held = true"
    args = []
    if sector:
        sql += " AND gics_sector = %s"
        args.append(sector)
    return _rows(sql, args)


# v_screener's column logic, but over an ARBITRARY member list (a source can hold names
# that aren't in ticker_tags or the book — those still show, untagged/DARK, per the
# migration-050 pattern). Base is unnest(members) so EVERY member appears.
_SOURCE_SLICE_SQL = """
WITH mem AS (SELECT DISTINCT unnest(%(members)s::text[]) AS ticker),
     latest_mfr AS (
        SELECT DISTINCT ON (ticker) ticker, price, range_low, range_high,
               trend_signal, momentum_signal, hurst, iv, rv,
               (full_payload->>'ivpd')::numeric AS ivpd
        FROM mfr_snapshots WHERE ticker = ANY(%(members)s)
        ORDER BY ticker, snapshot_date DESC),
     hedgeye_trend AS (
        SELECT DISTINCT ON (ticker) ticker, trend FROM hedgeye_risk_ranges
        WHERE ticker = ANY(%(members)s) ORDER BY ticker, signal_date DESC),
     held AS (
        SELECT DISTINCT underlying AS ticker FROM book_positions
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
          AND asset_class <> 'cash' AND COALESCE(quantity, 0) <> 0)
SELECT m.ticker, tt.subsector, tt.hedgeye_bucket_0629,
       (lm.price - lm.range_low) / NULLIF(lm.range_high - lm.range_low, 0) AS range_pos,
       (lm.momentum_signal = 'momentumBullish') AS momentum_ok,
       CASE lm.momentum_signal WHEN 'momentumBullish' THEN 'BULLISH'
            WHEN 'momentumBearish' THEN 'BEARISH' WHEN 'momentumNeutral' THEN 'NEUTRAL'
            WHEN 'momentumNeutralDanger' THEN 'NEUTRAL' END AS momentum_dir,
       CASE WHEN lm.trend_signal='trendBullish' AND lm.momentum_signal='momentumBearish' THEN 'bull-trade/bear-mom'
            WHEN lm.trend_signal='trendBearish' AND lm.momentum_signal='momentumBullish' THEN 'bear-trade/bull-mom' END AS divergence,
       lm.hurst, lm.iv, lm.rv, lm.ivpd,
       COALESCE(ht.trend, CASE lm.trend_signal WHEN 'trendBullish' THEN 'BULLISH'
            WHEN 'trendBearish' THEN 'BEARISH' WHEN 'trendNeutral' THEN 'NEUTRAL' END) AS trend_dir,
       CASE WHEN ht.trend IS NOT NULL THEN 'hedgeye'
            WHEN lm.trend_signal IN ('trendBullish','trendBearish','trendNeutral') THEN 'mfr' END AS trend_source,
       (h.ticker IS NOT NULL) AS held,
       (lm.range_low IS NOT NULL) AS has_range,
       tt.gics_sector
FROM mem m
LEFT JOIN ticker_tags    tt ON tt.ticker = m.ticker
LEFT JOIN latest_mfr     lm ON lm.ticker = m.ticker
LEFT JOIN hedgeye_trend  ht ON ht.ticker = m.ticker
LEFT JOIN held           h  ON h.ticker  = m.ticker
"""


def _reg_members(tag) -> set:
    from tools.source_registry import BY_TAG
    s = BY_TAG.get(tag)
    return s.members() if s else set()


def _fetch_source_slice(members, sector):
    """The 'source=' universe: every member of the source, joined to v_screener's
    computed columns (range/trend/tags where they exist). Members not in ticker_tags/
    book still appear (untagged '—', un-ranged -> DARK). Sector filter drops untagged
    names (they have no gics_sector), matching the book-universe behavior."""
    if not members:
        return []
    sql = _SOURCE_SLICE_SQL
    args = {"members": sorted(members)}
    if sector:
        sql += " WHERE tt.gics_sector = %(sector)s"
        args["sector"] = sector
    return _rows(sql, args)


def _source_sides(tag) -> dict:
    """{ticker: 'long'|'short'} for SIDED sources: keiths (stored side, latest signal_date)
    and etfpro (bias column — only once it exists; guarded). {} for unsided sources."""
    import db_pg
    out = {}
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            if tag == "keiths":
                cur.execute("SELECT ticker, side FROM hedgeye_keiths_signals "
                            "WHERE signal_date=(SELECT max(signal_date) FROM hedgeye_keiths_signals)")
                for t, sd in cur.fetchall():
                    if t and sd:
                        out[t.strip().upper()] = sd.strip().lower()
            elif tag == "etfpro":
                cur.execute("SELECT column_name FROM information_schema.columns "
                            "WHERE table_name='hedgeye_etf_pro_ranges' AND column_name='bias'")
                if cur.fetchone():
                    cur.execute("SELECT DISTINCT ON (ticker) ticker, bias "
                                "FROM hedgeye_etf_pro_ranges "
                                "WHERE week_of=(SELECT max(week_of) FROM hedgeye_etf_pro_ranges) "
                                "ORDER BY ticker, week_of DESC")
                    for t, b in cur.fetchall():
                        if t and b:
                            out[t.strip().upper()] = b.strip().lower()
            elif tag == "btcquant":
                # BTC Quant sentiment (bullish/bearish/neutral) from hedgeye_crypto_quant,
                # normalized to long/short so the shared sided filter works (neutral maps
                # to neither, dropping from a longs/shorts screen). Coin tokens get the
                # *USD suffix to match the canonical ticker.
                from tools.source_registry import BTCQ_NORM
                cur.execute("SELECT DISTINCT ON (asset) asset, sentiment FROM hedgeye_crypto_quant "
                            "WHERE sentiment IS NOT NULL ORDER BY asset, signal_date DESC")
                _map = {"bullish": "long", "bearish": "short", "neutral": "neutral"}
                for a, sd in cur.fetchall():
                    if a and sd:
                        tk = BTCQ_NORM.get(a.strip().upper(), a.strip().upper())
                        out[tk] = _map.get(sd.strip().lower(), sd.strip().lower())
    except Exception as e:
        log.warning("source sides lookup failed for %s: %s", tag, e)
    return out


def _lt_ranges(tickers) -> dict:
    """{ticker: (price, lt_low, lt_high)} from the latest mfr_snapshot's ltRangeData —
    the MFR trend range (the 'cloud'/yellow band). Read-only; {} on none."""
    if not tickers:
        return {}
    import db_pg
    out = {}
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (ticker) ticker, price::float, "
            "(full_payload->'ltRangeData'->>'lowerRange')::float, "
            "(full_payload->'ltRangeData'->>'upperRange')::float "
            "FROM mfr_snapshots WHERE ticker = ANY(%s) ORDER BY ticker, snapshot_date DESC",
            (list(tickers),))
        for t, px, lo, hi in cur.fetchall():
            out[t] = (px, lo, hi)
    return out


def _btcquant_trends() -> dict:
    """{canonical_ticker: BULLISH|BEARISH|NEUTRAL} — latest BTC Quant sentiment per name
    (hedgeye_crypto_quant), coin tokens normalized to *USD. The crypto trend authority
    for Rule-1, ranked between Hedgeye RR and MFR. Read-only."""
    import db_pg
    from tools.source_registry import BTCQ_NORM
    m = {"bullish": "BULLISH", "bearish": "BEARISH", "neutral": "NEUTRAL"}
    out = {}
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT DISTINCT ON (asset) asset, sentiment FROM hedgeye_crypto_quant "
                        "WHERE sentiment IS NOT NULL ORDER BY asset, signal_date DESC")
            for a, sd in cur.fetchall():
                if a and sd and sd.strip().lower() in m:
                    out[BTCQ_NORM.get(a.strip().upper(), a.strip().upper())] = m[sd.strip().lower()]
    except Exception as e:
        log.warning("btcquant trends lookup failed: %s", e)
    return out


def _apply_btcquant_trend(rows):
    """Rule-1 doctrine (operator-ruled): for crypto names BTC Quant is THE trend
    authority — it overrides both Hedgeye RR and MFR (BTC Quant > RR > MFR for crypto).
    Override trend_dir with the BTC Quant call for any name it designates; tag
    trend_source 'btcq' so it's visible which names gate on it. Non-crypto names (no
    btcquant entry) are untouched."""
    bt = _btcquant_trends()
    if not bt:
        return
    for r in rows:
        td = bt.get(r["ticker"])
        if td:
            r["trend_dir"] = td
            r["trend_source"] = "btcq"


def _attach_cloud(rows):
    """Annotate rows in place with _cloud ('above'/'in'/'below'/None) and _ltp (position
    within the LT band when inside), from the latest ltRangeData."""
    lt = _lt_ranges([r["ticker"] for r in rows])
    for r in rows:
        px, lo, hi = lt.get(r["ticker"], (None, None, None))
        r["_cloud"] = None
        r["_ltp"] = None
        if px is None or lo is None or hi is None:
            continue
        if px > hi:
            r["_cloud"] = "above"
        elif px < lo:
            r["_cloud"] = "below"
        else:
            r["_cloud"] = "in"
            if hi > lo:
                r["_ltp"] = (px - lo) / (hi - lo)


def _is_mfr_only_topidea(r) -> bool:
    return ((r.get("hedgeye_bucket_0629") or "").startswith("top_idea")
            and r.get("trend_source") == "mfr")


def _fmt_gated(r) -> str:
    src = {"hedgeye": "hdg", "mfr": "mfr", "btcq": "btcq"}.get(r.get("trend_source"), "")
    td = (r["trend_dir"] or "none") + (f"·{src}" if src else "")
    rp = "n/a" if r["range_pos"] is None else f"{float(r['range_pos']):.2f}"
    return f"  {_tier(r['hedgeye_bucket_0629']):<2} {r['ticker']:<9} {(r['subsector'] or ''):<18} trend={td:<12} rp={rp}"


def _num(v, sign=False, nd=2) -> str:
    if v is None:
        return "?"
    return f"{float(v):+.{nd}f}" if sign else f"{float(v):.{nd}f}"


def _fmt_row(r, corr) -> str:
    tier = _tier(r["hedgeye_bucket_0629"])
    rp = "n/a" if r["range_pos"] is None else f"{float(r['range_pos']):.2f}"
    md = {"BULLISH": "BULL", "BEARISH": "BEAR", "NEUTRAL": "NEUT"}.get(r.get("momentum_dir"), "?")
    src = {"hedgeye": "hdg", "mfr": "mfr", "btcq": "btcq"}.get(r.get("trend_source"), "")
    trend = f"{r['trend_dir'] or '-'}" + (f"·{src}" if src else "")
    cs, cu = corr.get(r["ticker"], (None, None))
    div = f" ⚡DIV({r['divergence']})" if r.get("divergence") else ""
    book = " 📗own" if r["held"] else ""
    if r.get("_cloud") == "in" and r.get("_ltp") is not None:
        cloud = f" lt:in={float(r['_ltp']):.2f}"
    elif r.get("_cloud"):
        cloud = f" lt:{r['_cloud']}"
    else:
        cloud = ""
    side = f" side:{r['_side']}" if r.get("_side") else ""
    warn = " ⚠mfr-only" if _is_mfr_only_topidea(r) else ""
    return (f"  {tier:<2} {r['ticker']:<9} {(r['subsector'] or '—'):<18} {trend:<11} "
            f"rp={rp:<5} mom={md:<4} h={_num(r.get('hurst')):<5} "
            f"iv={_num(r.get('iv'))} rv={_num(r.get('rv'))} ivpd={_num(r.get('ivpd'), sign=True)} "
            f"cSPY={_num(cs, sign=True)} cUUP={_num(cu, sign=True)}{div}{book}{cloud}{side}{warn}")


def run_screen_q(q: dict) -> str:
    """Execute a fully-parsed query (direction required). On empty, names the FIRST
    funnel stage that hit 0. The TREND gate reads the same COALESCEd trend_dir the
    rows display (Hedgeye primary, MFR fallback)."""
    buckets, req_trend = _DIR_BUCKETS[q["direction"]]
    src = q.get("source")
    try:
        # Base universe: source= (whole source, incl. names not in ticker_tags/book)
        # > my book (book holdings) > default (tagged roster, bucket-filtered). Direction
        # is enforced by the mandatory TREND gate; bucket filtering only applies to the
        # tagged universe (default / posmon). posmon == the default tagged universe.
        if src and src != "posmon":
            slice_ = _fetch_source_slice(_reg_members(src), q["sector"])
            if q["held"]:                        # source ∩ book (composable)
                slice_ = [r for r in slice_ if r["held"]]
        elif q["held"]:
            slice_ = _fetch_book_slice(q["sector"])
        else:
            slice_ = _fetch_tag_slice(q["sector"], buckets)
    except Exception as e:
        log.exception("screen query failed")
        return f"🛑 SCREEN error: {e}"

    # Sided sources (keiths always; etfpro once the bias column exists): scope the slice
    # to the direction's stored side up front — "keiths shorts" = keiths' short book —
    # then the BEARISH TREND gate still applies on top. Side is rendered per row.
    sides = _source_sides(src) if (src and src != "posmon") else {}
    if sides:
        want = "short" if q["direction"] == "shorts" else "long"
        slice_ = [r for r in slice_ if sides.get(r["ticker"]) == want]
    for r in slice_:
        r["_side"] = sides.get(r["ticker"]) if sides else None

    # Rule-1: BTC Quant is the crypto trend authority (RR > BTC Quant > MFR) — override
    # trend_dir for crypto names BEFORE the gate, on every screen.
    _apply_btcquant_trend(slice_)

    # Cloud position (price vs the MFR trend range) — annotate every slice row, then
    # optionally filter on above/in/below.
    _attach_cloud(slice_)

    dark = sorted([r for r in slice_ if not r["has_range"]], key=lambda r: r["ticker"])
    ranged = [r for r in slice_ if r["has_range"]]

    # TREND gate — evaluates COALESCE(hedgeye, mfr) via v_screener.trend_dir, the
    # identical field _fmt_row displays. An MFR-bullish name with no Hedgeye RR
    # passes a LONGS gate (and shows ·mfr).
    # DOCTRINE (open, operator's call): should a BTC Quant trend call feed this gate for
    # crypto names — i.e. extend v_screener.trend_dir's COALESCE to include btcquant the
    # way Hedgeye RR is authoritative for equities? NOT wired today: btcquant side is a
    # SEPARATE, filterable field (side:...), never gate-eligible. Flip only after ruling.
    after_trend = [r for r in ranged if (r["trend_dir"] or "") == req_trend]
    if q["near"] == "bottom":
        after_near = [r for r in after_trend if r["range_pos"] is not None and float(r["range_pos"]) <= 0.20]
    elif q["near"] == "top":
        after_near = [r for r in after_trend if r["range_pos"] is not None and float(r["range_pos"]) >= 0.80]
    else:
        after_near = after_trend
    after_mom = [r for r in after_near if r["momentum_ok"] is True] if q["momentum"] else after_near
    after_cloud = [r for r in after_mom if r.get("_cloud") == q["cloud"]] if q["cloud"] else after_mom

    result = sorted(
        after_cloud,   # held already applied at the base scope above
        key=lambda r: (r["range_pos"] is None, float(r["range_pos"]) if r["range_pos"] is not None else 0),
        reverse=(q["direction"] == "shorts"),
    )
    corr = _corr_for([r["ticker"] for r in result]) if result else {}

    filt = [q["direction"].upper()]
    if src:           filt.append(_source_label(src))
    if q["sector"]:   filt.append(q["sector"])
    if q["near"]:     filt.append(f"near_{q['near']}")
    if q["cloud"]:    filt.append(f"{q['cloud']}-cloud")
    if q["momentum"]: filt.append("momentum_ok")
    if q["held"]:     filt.append("in-book")
    head = f"🔎 SCREEN — {' · '.join(filt)}  (TREND gate: {req_trend}, mandatory)"

    lines = [head, ""]
    if result:
        lines.append(f"{len(result)} match(es)   tier: ●●active ●top-idea ·bench")
        lines.append("[tier·ticker·subsector·trend·rp·mom·hurst·iv·rv·ivpd·cSPY·cUUP]")
        lines += [_fmt_row(r, corr) for r in result]
        if any(r.get("divergence") for r in result):
            lines.append("⚡ = MFR trade vs momentum divergence (momentum-exhaustion fade setup).")
        if any(_is_mfr_only_topidea(r) for r in result):
            lines.append("⚠ = top idea on MFR trend only, no Hedgeye TREND — lower-confidence.")
        lines.append("cSPY/cUUP = bot-computed Pearson vs SPY/UUP daily returns (calc, not MFR); "
                     "? = <20 overlapping days.")
    else:
        near_lbl = f"near_{q['near']}" if q["near"] else "range gate (none)"
        if src and src != "posmon":
            base_lbl = _source_label(src) + (" (in book)" if q["held"] else "")
        elif q["held"]:
            base_lbl = "book holdings"
        else:
            base_lbl = "tag match (sector+bucket)"
        funnel = [
            f"{base_lbl}: {len(slice_)}",
            f"→ has MFR range:           {len(ranged)}",
            f"→ TREND={req_trend} (Rule-1): {len(after_trend)}",
            f"→ {near_lbl}:              {len(after_near)}",
            (f"→ momentum_ok:             {len(after_mom)}" if q["momentum"] else None),
            (f"→ {q['cloud']}-cloud:            {len(after_cloud)}" if q["cloud"] else None),
        ]
        funnel = [f for f in funnel if f]
        # FIRST funnel stage that hit 0.
        stages = [(base_lbl, len(slice_)), ("has-range", len(ranged)),
                  (f"TREND={req_trend}", len(after_trend))]
        if q["near"]:     stages.append((near_lbl, len(after_near)))
        if q["momentum"]: stages.append(("momentum_ok", len(after_mom)))
        if q["cloud"]:    stages.append((f"{q['cloud']}-cloud", len(after_cloud)))
        culprit = next((name for name, n in stages if n == 0), "unknown")
        lines.append(f"0 matches — emptied by: **{culprit}**")
        lines.append("")
        lines += funnel

    # ⛔ gated-by-TREND — matched the tier but failed Rule-1. Full list on "show
    # gated"; otherwise a one-line breadcrumb so nothing disappears silently.
    gated = sorted([r for r in ranged if (r["trend_dir"] or "") != req_trend],
                   key=lambda r: (r["range_pos"] is None,
                                  float(r["range_pos"]) if r["range_pos"] is not None else 0),
                   reverse=(q["direction"] == "shorts"))
    if q["show_gated"]:
        lines.append("")
        if gated:
            lines.append(f"⛔ GATED BY TREND — matched tier, trend != {req_trend} ({len(gated)}):")
            lines += [_fmt_gated(r) for r in gated]
        else:
            lines.append(f"⛔ GATED BY TREND: none — every tag-matched name is {req_trend}.")
    elif gated:
        lines.append("")
        lines.append(f"⛔ {len(gated)} matched but gated by TREND (need {req_trend}) "
                     f"— reply 'show gated' to list them.")

    lines.append("")
    src_noun = _source_label(src) if (src and src != "posmon") else None
    if dark:
        dark_hdr = (f"{src_noun}, NO MFR range" if src_noun
                    else "held, NO MFR range" if q["held"]
                    else "passed tag filters, NO MFR range")
        tag_fallback = src if (src and src != "posmon") else "book"
        lines.append(f"🌑 DARK — {dark_hdr} ({len(dark)}):")
        lines += [f"  {_tier(r['hedgeye_bucket_0629'])} {r['ticker']:<9} "
                  f"{(r['subsector'] or '—')} [{r['hedgeye_bucket_0629'] or tag_fallback}]"
                  for r in dark]
    else:
        base = src_noun or ("held" if q["held"] else "tag-matched")
        lines.append(f"🌑 DARK: none — every {base} name has an MFR range.")
    return "\n".join(lines)


def _resolve(q: dict, chat_id) -> str:
    """Token-guard, ask-for-direction (storing pending), or execute. The reply is
    always the acknowledgment — a follow-up is never acked without running."""
    if q.get("unrecognized"):
        return _token_guard(q["unrecognized"])
    if not q.get("direction"):
        _set_pending(chat_id, q)   # remember filters so a "longs"/"shorts" reply completes it
        got = _describe(q)
        return ("🔎 SCREEN needs a direction — reply **longs** or **shorts** "
                "(the TREND gate is tied to it)."
                + (f"\nFilters so far: {got}." if got else ""))
    _set_pending(chat_id, q)       # remember the executed query so "show gated" etc. re-run it
    return run_screen_q(q)


def run_screen(text: str) -> str:
    """Convenience one-shot (no chat/pending) — used by tests and CLI."""
    q = parse_query(text)
    if q.get("unrecognized"):
        return _token_guard(q["unrecognized"])
    if not q.get("direction"):
        return ("🔎 SCREEN needs a direction — longs or shorts. E.g. "
                "`SCREEN energy longs`.")
    return run_screen_q(q)


def handle_screen_command(text, chat_id=None):
    """Telegram listener hook. Handles the SCREEN sentinel AND follow-up replies to
    a pending query for this chat. Returns a reply string (results / guard / prompt)
    or None if the message isn't screen-related. Never a bare ack for a follow-up."""
    if not text:
        return None
    s = text.strip()
    if s.upper().startswith(SENTINEL):
        return _resolve(parse_query(s[len(SENTINEL):].lstrip(": ").strip()), chat_id)

    # Non-sentinel: only a short message with a recognized screen token can be a
    # follow-up (keeps normal chat / trade-decision messages from being hijacked).
    fu = parse_query(s)
    if _has_signal(fu) and len(s.split()) <= 6:
        pend = _get_pending(chat_id)
        if pend is not None:
            if fu.get("unrecognized"):
                return _token_guard(fu["unrecognized"])
            return _resolve(_merge(pend, fu), chat_id)
        # No active screen but a bare modifier ("show gated", "near the top") arrived.
        if _is_modifier_only(fu):
            return _orphan_hint()
    return None
