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

# NL sector phrases -> canonical ticker_tags.hedgeye_group (the Position
# Monitor's OWN 15 sectors). 2026-08-16: SCREEN moved off gics_sector, which was
# a data-provider taxonomy (standard GICS) that Hedgeye does not publish against.
# hedgeye_group IS Hedgeye's own sector, maintained by the PM ingest.
#
# ORDER IS LOAD-BEARING. parse_query takes the FIRST pattern that matches, so
# every multi-word name must sit above any single word that appears inside it.
# The bug this prevents: `\btech\b` used to match inside "GLOBAL TECH", setting
# sector=Technology and leaving 'global' as an unrecognized leftover — the same
# shape as the "financials signal strength" double-consume. _spans_overlap does
# NOT catch it (it only guards source-vs-sector), so the ordering below plus the
# whole-phrase anchors are the guard. _assert_sector_order() enforces it at
# import so a future edit cannot silently reintroduce the collision.
_SECTORS = [
    # --- multi-word names FIRST, longest phrase first ---
    (r"consumer\s+staples|\bstaples\b",               "CONSUMER STAPLES"),
    (r"digital\s+assets?|\bcrypto\b",                 "DIGITAL ASSETS"),
    (r"global\s+tech(?:nology)?",                     "GLOBAL TECH"),
    (r"small\s+caps?|smallcaps?",                     "SMALL CAPS"),
    # GLL is an acronym with no natural-language form. Hedgeye means Gaming,
    # Lodging & Leisure; a user will type one of those words, not "GLL".
    (r"gaming\s*,?\s*lodging\s*,?\s*(?:and\s+|&\s*)?leisure"
     r"|\bgll\b|\bgaming\b|\blodging\b|\bleisure\b",  "GLL"),
    # --- single-word names ---
    (r"restaurants?",                                 "RESTAURANTS"),
    (r"cannabis",                                     "CANNABIS"),
    (r"retail(?:ers?)?",                              "RETAIL"),
    # "health care" (two words) is the GICS spelling of the PM's HEALTHCARE.
    # Same sector, different spelling — an alias, not a retired name.
    (r"health\s*care|healthcare",                     "HEALTHCARE"),
    (r"financials?|\bbanks?\b",                       "FINANCIALS"),
    (r"industrials?",                                 "INDUSTRIALS"),
    (r"materials?",                                   "MATERIALS"),
    (r"\benergy\b",                                   "ENERGY"),
    (r"software",                                     "SOFTWARE"),
    # likewise "communication services" is GICS's spelling of COMMUNICATIONS.
    (r"communications?(?:\s+services)?",              "COMMUNICATIONS"),
]
_SECTOR_NAMES = [canon for _, canon in _SECTORS]

# GICS sector names with NO Position Monitor equivalent. Hedgeye does not
# publish a sector by these names, so a screen for one can never match a row.
# They are matched ONLY so the reply can say that explicitly — see
# tools/screener._retired_guard. Never silently return zero rows for these:
# "0 matches" reads as "nothing in that sector", which is a wrong conclusion
# presented as an answer.
#
# NOT listed here: "health care" and "communication services". Those are GICS
# SPELLINGS of live PM sectors (HEALTHCARE, COMMUNICATIONS) and resolve above.
_RETIRED_SECTORS = [
    (r"consumer\s+discretionary|\bdiscretionary\b", "Consumer Discretionary"),
    (r"information\s+technology|technology|\btech\b", "Technology"),
    (r"utilit(?:ies|y)",                             "Utilities"),
    (r"real\s+estate|\breits?\b",                    "Real Estate"),
]


def _assert_sector_order() -> None:
    """A single-word sector pattern must never match inside a multi-word sector
    NAME. Runs at import: the GLOBAL TECH collision was invisible until it
    silently mis-filed a query, so it is checked mechanically, not by review."""
    names = {c: n for n, c in ((r"consumer staples", "CONSUMER STAPLES"),
                               (r"digital assets", "DIGITAL ASSETS"),
                               (r"global tech", "GLOBAL TECH"),
                               (r"small caps", "SMALL CAPS"),
                               (r"gaming lodging leisure", "GLL"))}
    for canon, phrase in names.items():
        for pat, other in _SECTORS:
            if other == canon:
                break            # reached its own entry first -> correct order
            if re.search(pat, phrase):
                raise AssertionError(
                    "_SECTORS order bug: %r matches inside %r, so %r would win "
                    "over %s. Move the multi-word entry above it."
                    % (pat, phrase, other, canon))


_assert_sector_order()

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


def _spans_overlap(span, spans) -> bool:
    """Does (start, end) overlap any already-consumed span?"""
    a, b = span
    return any(a < e and s < b for s, e in spans)


def _sided_keiths(src, direction) -> bool:
    """Does this (source, direction) resolve to Keith's SIDED financials list?

    'financials signal strength' -> always (both sides are sided there).
    'signal strength'            -> SHORTS only; its LONGS stay on the broad
                                    ss_roster_current roster, which is the
                                    ~68-name product and keeps the TREND gate.
    """
    if src == "finsigstr":
        return True
    return src == "sigstr" and (direction or "").startswith("short")

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
               "everything": False, "unrecognized": [], "retired_sector": None,
               "raw": (text or "").strip()}
    consumed: list = []   # (start, end) spans of matched screen tokens

    # Source lens (etf pro / keiths / portfolio solutions / …). Consumed here so its
    # words never trip the unrecognized-token guard (the "signal strength" failure).
    for alias, tag in _source_phrase_list():
        m = re.search(r"\b" + re.escape(alias) + r"\b", s)
        if m:
            q["source"] = tag
            consumed.append(m.span())
            break

    # 'everything' lens — the explicit full-universe scope (vs the silent posmon
    # default). Consumed so the words don't trip the unrecognized-token guard.
    m = re.search(r"\b(everything|all assets|all names|all tickers|"
                  r"whole universe|full universe|entire universe|universe)\b", s)
    if m:
        q["everything"] = True
        consumed.append(m.span())

    # Sector must not re-match text the SOURCE lens already claimed: in
    # "financials signal strength longs" the word financials IS the source name,
    # not a sector filter. Without this, that query set sector=Financials too and
    # silently dropped Keith's non-financials-sector longs (COMP is Real Estate,
    # EXPN is Industrials) from a list that is financials-by-construction.
    for pat, canon in _SECTORS:
        hit = next((m for m in re.finditer(pat, s)
                    if not _spans_overlap(m.span(), consumed)), None)
        if hit:
            q["sector"] = canon
            consumed.append(hit.span())
            break
    # A GICS name with no PM equivalent. Recorded (and its span consumed, so it
    # does not also surface as an unrecognized token) purely so the caller can
    # REFUSE by name instead of running a query guaranteed to return nothing.
    if not q["sector"]:
        for pat, old in _RETIRED_SECTORS:
            hit = next((m for m in re.finditer(pat, s)
                        if not _spans_overlap(m.span(), consumed)), None)
            if hit:
                q["retired_sector"] = old
                consumed.append(hit.span())
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
    m = re.search(r"\bdecel(?:erating)?\b", s)
    if m:
        q["decel"] = True; consumed.append(m.span())
    m = re.search(r"\bdistribution\b", s)
    if m:
        q["distribution"] = True; consumed.append(m.span())
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


def _retired_guard(old_name) -> str:
    """Refuse a GICS sector Hedgeye does not publish, BY NAME.

    Never run the query. Before 2026-08-16 these parsed cleanly, executed, and
    rendered '0 matches — emptied by: tag match (sector+bucket)' with the dead
    name echoed as an applied filter. A reader takes that as "nothing in that
    sector right now". It actually means "that sector does not exist here", and
    the two conclusions lead opposite ways. If the answer is knowable before the
    query, say it instead of running the query."""
    return (f"🔎 '{old_name}' is not a Hedgeye sector — SCREEN now uses the "
            f"Position Monitor's own 15, so there is no such roster to search "
            f"(this is NOT an empty result).\n{_CMD_HINT}")


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


def _vol_for(tickers) -> dict:
    """{ticker: (real_dip, price_down_3d, decelerating, decel_streak)} from the
    latest volume_snapshots. Empty when volume hasn't been computed. Never raises."""
    out = {}
    if not tickers:
        return out
    import db_pg
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, real_dip, price_down_3d, decelerating, COALESCE(decel_streak, 0) "
                "FROM volume_snapshots WHERE ticker = ANY(%s) "
                "AND snapshot_date = (SELECT max(snapshot_date) FROM volume_snapshots)",
                (list(tickers),))
            for t, rd, pd, de, sk in cur.fetchall():
                out[t] = (rd, pd, de, sk)
    except Exception as e:
        log.warning("SCREEN: volume overlay unavailable: %s", e)
    return out


def _ivpd_pct_for(tickers, window=60) -> dict:
    """{ticker: percentile 0-100 of today's ivpd vs its own trailing `window`
    sessions} from mfr_snapshots.full_payload. None when <10 history pts."""
    out = {}
    if not tickers: return out
    import db_pg
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for t in tickers:
                cur.execute(
                    "SELECT (full_payload->>'ivpd')::numeric FROM mfr_snapshots "
                    "WHERE ticker=%s AND full_payload->>'ivpd' IS NOT NULL "
                    "ORDER BY snapshot_date DESC LIMIT %s", (t, window))
                vals = [float(r[0]) for r in cur.fetchall() if r[0] is not None]
                if len(vals) < 10: continue
                today = vals[0]
                below = sum(1 for v in vals if v < today)
                out[t] = round(100.0 * below / len(vals))
    except Exception as e:
        log.warning("SCREEN: ivpd pct unavailable: %s", e)
    return out


_SLICE_COLS = ("SELECT ticker, subsector, hedgeye_bucket_0629, hedgeye_group, "
               "range_pos, momentum_ok, "
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
        sql += " AND hedgeye_group = %s"
        args.append(sector)
    return _rows(sql, args)


def sector_blind_spot(where_sql, args=None) -> int:
    """How many rows of a lens have NO PM sector, and so cannot appear in ANY
    sector-filtered screen of it.

    `hedgeye_group = %s` is SQL equality, which never matches NULL. A name that
    is not on the Position Monitor is therefore invisible to a sector screen —
    not filtered out, UNREACHABLE. Reporting a bounded result as if it were
    complete coverage is the failure this exists to prevent, so run_screen_q
    prints this count on every sector-filtered screen."""
    sql = ("SELECT count(*) FROM v_screener WHERE hedgeye_group IS NULL AND "
           + where_sql)
    rows = _rows(sql.replace("SELECT count(*)", "SELECT count(*) AS n", 1),
                 args or [])
    return int(rows[0]["n"]) if rows else 0


def _fetch_book_slice(sector):
    """The 'my book' universe: every held name (book_positions underlyings, latest
    snapshot — cash/zero-qty already excluded in the view), NOT bucket-filtered, so
    untagged holdings (sector ETFs like XLV) are included. Direction is enforced
    downstream by the mandatory TREND gate, not by bucket. Sector filter still applies
    where a name is tagged (names with no PM sector have NULL hedgeye_group and
    drop out of a sector screen — surfaced by sector_blind_spot, never silent)."""
    sql = _SLICE_COLS + " WHERE held = true"
    args = []
    if sector:
        sql += " AND hedgeye_group = %s"
        args.append(sector)
    return _rows(sql, args)


def _fetch_everything_slice(sector):
    """The 'everything' lens: the FULL enrolled universe — every v_screener row
    with a live MFR range (has_range), NOT bucket-filtered. This is the whole
    MFR-enrolled set, not the ~433 posmon roster the default screens. Self-
    maintaining: grows as names get tagged/enrolled. Direction is still enforced
    downstream by the mandatory TREND gate."""
    sql = _SLICE_COLS + " WHERE has_range = true"
    args = []
    if sector:
        sql += " AND hedgeye_group = %s"
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
       tt.gics_sector, tt.hedgeye_group
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
    book still appear (untagged '—', un-ranged -> DARK). Sector filter drops names
    with no PM sector (NULL hedgeye_group), matching the book-universe behavior —
    the funnel's own base count shows how many were dropped."""
    if not members:
        return []
    sql = _SOURCE_SLICE_SQL
    args = {"members": sorted(members)}
    if sector:
        sql += " WHERE tt.hedgeye_group = %(sector)s"
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


def _underlying_trends(tickers) -> dict:
    """{ticker: trend_dir} for wrapper underlyings — the underlying's own signal stack
    (Hedgeye RR > BTC Quant > MFR). Computed over _fetch_source_slice so underlyings with
    MFR data but NOT in v_screener (FXE/FXY currency proxies) still resolve. Read-only."""
    tickers = [t for t in set(tickers) if t]
    if not tickers:
        return {}
    rows = _fetch_source_slice(tickers, None)   # trend over ANY ticker with MFR/RR data
    _apply_btcquant_trend(rows)   # crypto underlyings carry the btcquant trend too
    return {r["ticker"]: r["trend_dir"] for r in rows}


_INV = {"BULLISH": "BEARISH", "BEARISH": "BULLISH", "NEUTRAL": "NEUTRAL"}


def _apply_wrapper_trend(rows):
    """Wrapper ETFs (METD->META inverse, SQQQ->QQQ …): the Rule-1 gate signal is the
    UNDERLYING's trend, INVERTED where the linkage is inverse — so METD reads BULLISH
    exactly when META = BEARISH (short-META thesis intact). Overrides the wrapper's own
    (thin) trend; tags trend_source 'undr' and stashes the underlying detail for
    rendering. If the underlying carries no trend, the wrapper keeps its own and is
    flagged uncovered."""
    from tools.wrapper_links import get_links
    try:
        links = get_links()
    except Exception as e:
        log.warning("wrapper links lookup failed: %s", e)
        return
    present = {r["ticker"] for r in rows} & set(links)
    if not present:
        return
    ut = _underlying_trends([links[w]["underlying"] for w in present])
    for r in rows:
        lk = links.get(r["ticker"])
        if not lk:
            continue
        utd = ut.get(lk["underlying"])
        r["_wrap"] = {"underlying": lk["underlying"], "u_trend": utd, "inverse": lk["inverse"]}
        if utd:
            r["trend_dir"] = _INV[utd] if lk["inverse"] else utd
            r["trend_source"] = "undr"


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
    src = {"hedgeye": "hdg", "mfr": "mfr", "btcq": "btcq", "undr": "undr"}.get(r.get("trend_source"), "")
    td = (r["trend_dir"] or "none") + (f"·{src}" if src else "")
    rp = _rp_str(r)
    return f"  {_tier(r['hedgeye_bucket_0629']):<2} {r['ticker']:<9} {(r['subsector'] or ''):<18} trend={td:<12} rp={rp} px={_px_str(r)} rng={_rng_str(r)}"


def _num(v, sign=False, nd=2) -> str:
    if v is None:
        return "?"
    return f"{float(v):+.{nd}f}" if sign else f"{float(v):.{nd}f}"


def _ivpd_tag(r, ivpct):
    p = (ivpct or {}).get(r["ticker"])
    return f"({p}%)" if p is not None else ""


def _fmt_row(r, corr, vol=None, ivpct=None) -> str:
    tier = _tier(r["hedgeye_bucket_0629"])
    rp = _rp_str(r)
    md = {"BULLISH": "BULL", "BEARISH": "BEAR", "NEUTRAL": "NEUT"}.get(r.get("momentum_dir"), "?")
    src = {"hedgeye": "hdg", "mfr": "mfr", "btcq": "btcq", "undr": "undr"}.get(r.get("trend_source"), "")
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
    if r.get("_wrap"):
        wd = r["_wrap"]
        ut = {"BULLISH": "BULL", "BEARISH": "BEAR", "NEUTRAL": "NEUT"}.get(wd["u_trend"], "?")
        wrap = f" u:{wd['underlying']}·{ut}" + ("↯inv" if wd["inverse"] else "")
    else:
        wrap = ""
    warn = " ⚠mfr-only" if _is_mfr_only_topidea(r) else ""
    th = " ⚠️trend-against" if r.get("_thesis") else ""
    v = (vol or {}).get(r["ticker"])
    if v and v[0]:
        volmark = f" vol=↓{v[3] or 0}d"
    elif v and v[1] and not v[2]:
        volmark = " vol=↑"
    else:
        volmark = ""
    return (f"  {tier:<2} {r['ticker']:<9} {(r['subsector'] or '—'):<18} {trend:<11} "
            f"rp={rp:<8} px={_px_str(r):<7} rng={_rng_str(r):<17} mom={md:<4} h={_num(r.get('hurst')):<5} "
            f"iv={_num(r.get('iv'))} rv={_num(r.get('rv'))} ivpd={_num(r.get('ivpd'), sign=True)}{_ivpd_tag(r, ivpct)} "
            f"cSPY={_num(cs, sign=True)} cUUP={_num(cu, sign=True)}{div}{book}{cloud}{side}{wrap}{th}{warn}{volmark}")


_RP_LIVE_MIN, _RP_LIVE_MAX = 0.2, 5.0

# ── snapshot-age staleness (the RANGE, not the price) ───────────────────────
# rp = live price ÷ MFR range. Yahoo keeps the PRICE fresh, but the RANGE comes
# from mfr_snapshots and only refreshes when the MFR backlog runs. A fresh price
# on a stale range = a confident-looking rp that's wrong (the USO 0.60 bug). So
# a name whose snapshot lags the freshest one in the screen — or, if the whole
# feed is dark, is simply too old — must FAIL LOUD (⚠STALE), never print a
# number. Self-calibrating + weekend-safe: on Monday the freshest snapshot is
# Friday for everyone, so nothing lags.
_SNAP_STALE_LAG_DAYS = 1     # snapshot lagging the freshest in the screen by >= this = stale
_SNAP_FEED_MAX_AGE   = 4     # if the freshest snapshot itself is older than this (days), whole feed is stale


def _snap_staleness(sd, batch_max_sd, today):
    """(is_stale, age_days) for one row's snapshot_date. Pure. No snapshot at
    all = stale. Otherwise stale if the whole feed is old OR this name lags the
    freshest snapshot in the batch."""
    if sd is None:
        return (True, None)
    feed_stale = (batch_max_sd is None) or ((today - batch_max_sd).days > _SNAP_FEED_MAX_AGE)
    lag = (batch_max_sd - sd).days if batch_max_sd else 0
    return (bool(feed_stale or lag >= _SNAP_STALE_LAG_DAYS), (today - sd).days)


def _st_ranges(tickers):
    tickers = [t for t in {t for t in tickers if t}]
    if not tickers:
        return {}
    import db_pg
    out = {}
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (ticker) ticker, range_low::float, range_high::float, "
                "price::float, snapshot_date, fetched_at "
                "FROM mfr_snapshots WHERE ticker = ANY(%s) "
                "ORDER BY ticker, snapshot_date DESC", (tickers,))
            for t, lo, hi, px, sd, fa in cur.fetchall():
                out[t] = (lo, hi, px, sd, fa)
    except Exception as e:
        log.warning("SCREEN: short-term range fetch failed: %s", e)
    return out


def _refresh_range_pos_live(rows):
    import datetime as _dt
    # 1) Range fetch — ALWAYS runs (its own guard) so the staleness flag is set
    #    even when the live-price feed is down.
    try:
        rng = _st_ranges([r["ticker"] for r in rows])
    except Exception as e:
        log.warning("SCREEN: range fetch failed (%s)", e)
        rng = {}
    # snapshot-age staleness of the RANGE — fail loud on old bands (the USO fix)
    sds = [sd for (_lo, _hi, _px, sd, _fa) in rng.values() if sd]
    batch_max_sd = max(sds) if sds else None
    today = _dt.date.today()
    for r in rows:
        sd = rng.get(r["ticker"], (None, None, None, None, None))[3]
        r["_snap_stale"], r["_snap_age_days"] = _snap_staleness(sd, batch_max_sd, today)

    # 2) Live price fetch (Yahoo). If unavailable, fall back to EOD — but the
    #    range-staleness flag above still stands.
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE, fetch_prices
        symmap = {r["ticker"]: (HEDGEYE_TO_YFINANCE.get(r["ticker"]) or r["ticker"]) for r in rows}
        live = fetch_prices(sorted({s for s in symmap.values() if s}))
    except Exception as e:
        log.warning("SCREEN: live range_pos refresh unavailable (%s) - using EOD", e)
        for r in rows:
            r["_rp_stale"] = True
            r["_price"] = None
            r["_rlo"] = r["_rhi"] = None
        return
    for r in rows:
        lo, hi, px_eod, sd, fa = rng.get(r["ticker"], (None, None, None, None, None))
        lp = live.get(symmap.get(r["ticker"]))
        good = (lp is not None and lp > 0 and lo is not None and hi is not None and hi > lo
                and (px_eod is None or (px_eod > 0 and _RP_LIVE_MIN <= lp / px_eod <= _RP_LIVE_MAX)))
        r["_rlo"], r["_rhi"] = lo, hi
        if good:
            r["range_pos"] = (lp - lo) / (hi - lo)
            r["_price"] = lp
            r["_rp_stale"] = False
        else:
            r["_price"] = px_eod
            r["_rp_stale"] = True
    _apply_shadow_failover(rows)


def _apply_shadow_failover(rows) -> None:
    """Where MFR has no usable range (dark / stale / validation-flagged), publish
    the SHADOW band instead and tag the row 'shd' so every line shows which
    authority set the level.

    Equity + ETF only — the failover map is built from status='ok' shadow rows,
    and futures/crypto/fx are stored as class_uncalibrated. hdg is untouched:
    Hedgeye levels live in hedgeye_risk_ranges and feed decision_engine, not this
    display path. Best-effort — any failure leaves the MFR values exactly as they
    were.
    """
    try:
        from tools.shadow_ingest import shadow_failover_map
        fmap = shadow_failover_map({r["ticker"] for r in rows})
    except Exception as e:
        log.warning("SCREEN: shadow failover unavailable (%s) - MFR values stand", e)
        return
    if not fmap:
        return
    for r in rows:
        sh = fmap.get(r["ticker"])
        if not sh:
            continue
        usable_mfr = (r.get("_rlo") is not None and r.get("_rhi") is not None
                      and not r.get("_snap_stale"))
        if usable_mfr:
            continue                       # healthy MFR band wins — byte-identical
        lo, hi, srp, sh_hurst = sh
        r["_rlo"], r["_rhi"] = lo, hi
        r["_rsrc"] = "shd"
        r["_snap_stale"] = False           # a shadow band is current by construction
        px = r.get("_price")
        if px is not None and hi > lo:
            r["range_pos"] = (float(px) - lo) / (hi - lo)
        elif srp is not None:
            r["range_pos"] = srp
        if r.get("hurst") is None and sh_hurst is not None:
            r["hurst"] = sh_hurst


def _rp_str(r) -> str:
    # FAIL LOUD: a stale range (old snapshot) never prints a clean number — the
    # live price would be mapped onto a band that has moved (the USO 0.60 bug).
    if r.get("_snap_stale"):
        age = r.get("_snap_age_days")
        return f"⚠STALE({age}d)" if age is not None else "⚠STALE(no-snap)"
    if r.get("range_pos") is None:
        return "n/a"
    s = f"{float(r['range_pos']):.2f}"
    return s + "!eod" if r.get("_rp_stale") else s


def _px_str(r) -> str:
    px = r.get("_price")
    return f"{float(px):.2f}" if px is not None else "?"


def _rng_str(r) -> str:
    lo, hi = r.get("_rlo"), r.get("_rhi")
    if lo is None or hi is None:
        return "[?]"
    # range-source tag: only set when the shadow engine supplied the band, so
    # rows on a healthy MFR range render byte-identically to before.
    src = f"·{r['_rsrc']}" if r.get("_rsrc") else ""
    return f"[{float(lo):.2f}-{float(hi):.2f}]{src}"


def run_screen_q(q: dict) -> str:
    """Execute a fully-parsed query (direction required). On empty, names the FIRST
    funnel stage that hit 0. The TREND gate reads the same COALESCEd trend_dir the
    rows display (Hedgeye primary, MFR fallback)."""
    buckets, req_trend = _DIR_BUCKETS[q["direction"]]
    src = q.get("source")
    # BOOK MODE (queue item 5 / the SHY-TUA-HEFT-AGGH fix): on a pure book screen,
    # direction means POSITION SIDE (what you actually hold), not trend. TREND is
    # shown per row and mismatches carry a ⚠️ thesis flag instead of relabeling a
    # long as a "short". Source∩book composites keep source semantics unchanged.
    book_mode = bool(q["held"] and not src)
    # Membership from Keith's sided list already answers the direction, so the
    # TREND gate must NOT be re-applied on top of it — that gate WAS the shorts
    # bug. The BROAD roster (signal strength longs) keeps its mandatory gate.
    sigstr_trend_exempt = _sided_keiths(src, q["direction"])
    try:
        # Base universe: source= (whole source, incl. names not in ticker_tags/book)
        # > my book (book holdings) > default (tagged roster, bucket-filtered). Direction
        # is enforced by the mandatory TREND gate; bucket filtering only applies to the
        # tagged universe (default / posmon). posmon == the default tagged universe.
        if src and src != "posmon":
            # SIGNAL-STRENGTH ROUTING — four cases, two different products:
            #   signal strength longs             -> BROAD ss_roster_current, TREND gate ON
            #   signal strength shorts            -> Keith's side=short, no gate
            #   financials signal strength longs  -> Keith's side=long,  no gate
            #   financials signal strength shorts -> Keith's side=short, no gate
            # The broad roster is side-less, so only its LONGS reading is
            # meaningful; its shorts direction routes to Keith's sided list.
            if _sided_keiths(src, q["direction"]):
                from tools.source_registry import sigstr_side
                members = sigstr_side(q["direction"])
            else:
                members = _reg_members(src)
            slice_ = _fetch_source_slice(members, q["sector"])
            if q["held"]:                        # source ∩ book (composable)
                slice_ = [r for r in slice_ if r["held"]]
        elif q["held"]:
            slice_ = _fetch_book_slice(q["sector"])
        elif q.get("everything"):                # explicit full-universe lens
            slice_ = _fetch_everything_slice(q["sector"])
        else:
            slice_ = _fetch_tag_slice(q["sector"], buckets)
    except Exception as e:
        log.exception("screen query failed")
        return f"🛑 SCREEN error: {e}"

    # Book mode: derive position sides (Python math over book_positions legs,
    # wrapper-linkage exposure-adjusted) and filter the slice on the WANTED side.
    # A failure here is LOUD — never silently fall back to trend semantics.
    psides: dict = {}
    no_side: list = []
    pre_side_n = len(slice_)
    if book_mode:
        try:
            from tools.book_direction import book_sides
            psides = book_sides()
        except Exception as e:
            log.exception("book position sides lookup failed")
            return (f"🛑 SCREEN error: book position sides unavailable ({e}) — "
                    f"refusing to run a book {q['direction']} screen on trend semantics.")
        want_side = "short" if q["direction"] == "shorts" else "long"
        for r in slice_:
            r["_pside"] = (psides.get(r["ticker"]) or {}).get("side")
        no_side = [r for r in slice_ if r["_pside"] not in ("long", "short")]
        slice_ = [r for r in slice_ if r["_pside"] == want_side]

    # Sided sources (keiths always; etfpro once the bias column exists): scope the slice
    # to the direction's stored side up front — "keiths shorts" = keiths' short book —
    # then the BEARISH TREND gate still applies on top. Side is rendered per row.
    sides = _source_sides(src) if (src and src != "posmon") else {}
    if sides:
        want = "short" if q["direction"] == "shorts" else "long"
        slice_ = [r for r in slice_ if sides.get(r["ticker"]) == want]
    for r in slice_:
        # Sided-source side wins on source screens; book mode renders the
        # POSITION side through the same side: field.
        r["_side"] = sides.get(r["ticker"]) if sides else r.get("_pside")

    # Rule-1: BTC Quant is the crypto trend authority (RR > BTC Quant > MFR) — override
    # trend_dir for crypto names BEFORE the gate, on every screen.
    _apply_btcquant_trend(slice_)
    # Wrapper ETFs gate on their UNDERLYING's trend (inverted where inverse).
    _apply_wrapper_trend(slice_)

    # Cloud position (price vs the MFR trend range) — annotate every slice row, then
    # optionally filter on above/in/below.
    _attach_cloud(slice_)

    dark = sorted([r for r in slice_ if not r["has_range"]], key=lambda r: r["ticker"])
    ranged = [r for r in slice_ if r["has_range"]]
    _refresh_range_pos_live(ranged)

    # TREND gate — evaluates COALESCE(hedgeye, mfr) via v_screener.trend_dir, the
    # identical field _fmt_row displays. An MFR-bullish name with no Hedgeye RR
    # passes a LONGS gate (and shows ·mfr).
    # DOCTRINE (open, operator's call): should a BTC Quant trend call feed this gate for
    # crypto names — i.e. extend v_screener.trend_dir's COALESCE to include btcquant the
    # way Hedgeye RR is authoritative for equities? NOT wired today: btcquant side is a
    # SEPARATE, filterable field (side:...), never gate-eligible. Flip only after ruling.
    if book_mode:
        # Book screens: TREND never drops a row you actually hold — it renders
        # per row, and trend-against-position gets a ⚠️ thesis flag. raw_side vs
        # the (linkage-adjusted) trend_dir: both flip together on inverse
        # wrappers, so the verdict is frame-invariant (SBIT long + BTC bearish
        # -> adjusted trend BULLISH -> intact, no flag).
        for r in ranged:
            raw = (psides.get(r["ticker"]) or {}).get("raw_side")
            td = r["trend_dir"] or ""
            r["_thesis"] = ((raw == "long" and td == "BEARISH") or
                            (raw == "short" and td == "BULLISH"))
        after_trend = ranged
    elif sigstr_trend_exempt:
        # Keith's list IS the directional call. Re-applying trend_dir=='BEARISH'
        # on top was the bug: it silently dropped Keith's shorts whose COALESCEd
        # trend had not yet turned (and admitted unrelated bearish names). Side
        # comes from the source; the ranges still screen the names below.
        after_trend = ranged
    else:
        after_trend = [r for r in ranged if (r["trend_dir"] or "") == req_trend]
    if q["near"] == "bottom":
        after_near = [r for r in after_trend if r["range_pos"] is not None and float(r["range_pos"]) <= 0.20]
    elif q["near"] == "top":
        after_near = [r for r in after_trend if r["range_pos"] is not None and float(r["range_pos"]) >= 0.80]
    else:
        after_near = after_trend
    after_mom = [r for r in after_near if r["momentum_ok"] is True] if q["momentum"] else after_near
    after_cloud = [r for r in after_mom if r.get("_cloud") == q["cloud"]] if q["cloud"] else after_mom

    vol = _vol_for([r["ticker"] for r in after_cloud]) if after_cloud else {}
    def _real_dip(t):     v = vol.get(t); return bool(v and v[0])
    def _distribution(t): v = vol.get(t); return bool(v and v[1] and not v[2])
    after_vol = after_cloud
    if q.get("decel"):
        after_vol = [r for r in after_cloud if _real_dip(r["ticker"])]
    elif q.get("distribution"):
        after_vol = [r for r in after_cloud if _distribution(r["ticker"])]
    result = sorted(
        after_vol,
        key=lambda r: (r["range_pos"] is None, float(r["range_pos"]) if r["range_pos"] is not None else 0),
        reverse=(q["direction"] == "shorts"),
    )
    corr = _corr_for([r["ticker"] for r in result]) if result else {}
    ivpct = _ivpd_pct_for([r["ticker"] for r in result]) if result else {}

    filt = [q["direction"].upper()]
    if src:
        filt.append(_source_label(src))
    elif q.get("everything"):
        filt.append("EVERYTHING · enrolled universe")
    elif not q["held"]:
        filt.append("posmon roster (default — say 'everything' for all)")
    if q["sector"]:   filt.append(q["sector"])
    if q["near"]:     filt.append(f"near_{q['near']}")
    if q["cloud"]:    filt.append(f"{q['cloud']}-cloud")
    if q["momentum"]: filt.append("momentum_ok")
    if q.get("decel"):        filt.append("decel-vol")
    if q.get("distribution"): filt.append("distribution")
    if q["held"]:     filt.append("in-book")
    if book_mode:
        head = (f"🔎 SCREEN — {' · '.join(filt)}  (side = POSITION side; "
                f"TREND shown per row, ⚠️ = trend against position)")
    elif sigstr_trend_exempt:
        head = (f"🔎 SCREEN — {' · '.join(filt)}  (side from Keith's Signal "
                f"Longs/Shorts; no TREND gate — the list IS the call)")
    else:
        head = f"🔎 SCREEN — {' · '.join(filt)}  (TREND gate: {req_trend}, mandatory)"

    lines = [head, ""]
    # BOOK AGE. Any screen that reads the book — book mode, an in-book filter,
    # or the `held` marker on ordinary rows — is answering from the last broker
    # export, which is operator-uploaded and goes stale silently.
    if book_mode or q["held"]:
        try:
            from tools.book_freshness import book_banner
            lines.append(book_banner())
            lines.append("")
        except Exception as e:
            lines.append(f"⚠️ BOOK AGE UNKNOWN ({e}) — holdings below unverified.")
            lines.append("")
    # SECTOR BLIND SPOT. A sector filter is SQL equality on hedgeye_group, which
    # cannot match NULL, so every name absent from the Position Monitor is
    # unreachable — not zero-matching, invisible. Say so on the screen itself:
    # a bounded result that reads as complete coverage is a wrong answer
    # presented confidently.
    if q["sector"]:
        try:
            if book_mode or q["held"]:
                blind = sector_blind_spot("held = true")
                scope = "held names"
            elif q.get("everything"):
                blind = sector_blind_spot("has_range = true")
                scope = "enrolled names"
            elif src and src != "posmon":
                blind = None      # source lens: reported via its own funnel
                scope = ""
            else:
                blind = sector_blind_spot("hedgeye_bucket_0629 = ANY(%s)",
                                          [buckets])
                scope = "roster names"
            if blind:
                lines.append(
                    f"ℹ️ {blind} {scope} in this lens have NO Position Monitor "
                    f"sector and cannot appear in ANY sector screen. This list "
                    f"is not the whole universe.")
                lines.append("")
        except Exception as e:
            log.warning("sector blind-spot count failed: %s", e)
            lines.append("⚠️ could not determine how many names lack a PM sector "
                         "— treat this list as incomplete.")
            lines.append("")
    if result:
        lines.append(f"{len(result)} match(es)   tier: ●●active ●top-idea ·bench")
        lines.append("[tier·ticker·subsector·trend·rp·mom·hurst·iv·rv·ivpd·cSPY·cUUP·vol]")
        lines += [_fmt_row(r, corr, vol, ivpct) for r in result]
        _stale = [r for r in result if r.get("_snap_stale")]
        if _stale:
            names = " ".join(sorted(r["ticker"] for r in _stale))
            lines.append(f"⚠STALE = MFR range snapshot is old (days shown) — rp is SUPPRESSED, "
                         f"NOT a number. Do NOT trade off it; the range moved. "
                         f"{len(_stale)} name(s): {names}")
        if any(r.get("divergence") for r in result):
            lines.append("⚡ = MFR trade vs momentum divergence (momentum-exhaustion fade setup).")
        if any(r.get("_thesis") for r in result):
            lines.append("⚠️trend-against = TREND opposes your position side — thesis check, "
                         "not a signal to flip.")
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
        elif q.get("everything"):
            base_lbl = "everything (enrolled universe)"
        else:
            base_lbl = "tag match (sector+bucket)"
        side_lbl = f"position side = {q['direction'][:-1]}"
        funnel = [
            (f"{base_lbl}: {pre_side_n}" if book_mode else f"{base_lbl}: {len(slice_)}"),
            (f"→ {side_lbl}:         {len(slice_)}" if book_mode else None),
            f"→ has MFR range:           {len(ranged)}",
            (None if book_mode else
             (f"→ side={q['direction'][:-1]} (Keith's list): {len(after_trend)}"
              if sigstr_trend_exempt
              else f"→ TREND={req_trend} (Rule-1): {len(after_trend)}")),
            f"→ {near_lbl}:              {len(after_near)}",
            (f"→ momentum_ok:             {len(after_mom)}" if q["momentum"] else None),
            (f"→ {q['cloud']}-cloud:            {len(after_cloud)}" if q["cloud"] else None),
        ]
        funnel = [f for f in funnel if f]
        # FIRST funnel stage that hit 0.
        if book_mode:
            stages = [(base_lbl, pre_side_n), (side_lbl, len(slice_)),
                      ("has-range", len(ranged))]
        else:
            stages = [(base_lbl, len(slice_)), ("has-range", len(ranged)),
                      ((f"side={q['direction'][:-1]}" if sigstr_trend_exempt
                        else f"TREND={req_trend}"), len(after_trend))]
        if q["near"]:     stages.append((near_lbl, len(after_near)))
        if q["momentum"]: stages.append(("momentum_ok", len(after_mom)))
        if q["cloud"]:    stages.append((f"{q['cloud']}-cloud", len(after_cloud)))
        if q.get("decel"):        stages.append(("decel-vol", len(after_vol)))
        if q.get("distribution"): stages.append(("distribution", len(after_vol)))
        culprit = next((name for name, n in stages if n == 0), "unknown")
        lines.append(f"0 matches — emptied by: **{culprit}**")
        lines.append("")
        lines += funnel

    # ⛔ gated-by-TREND — matched the tier but failed Rule-1. Full list on "show
    # gated"; otherwise a one-line breadcrumb so nothing disappears silently.
    # Book mode: TREND never gates a held row — mismatches are ⚠️-flagged inline.
    # sigstr is trend-exempt, so nothing was gated — reporting a gated count
    # here would contradict the rows actually returned.
    gated = [] if (book_mode or sigstr_trend_exempt) else sorted(
        [r for r in ranged if (r["trend_dir"] or "") != req_trend],
        key=lambda r: (r["range_pos"] is None,
                       float(r["range_pos"]) if r["range_pos"] is not None else 0),
        reverse=(q["direction"] == "shorts"))
    if q["show_gated"]:
        lines.append("")
        if book_mode:
            lines.append("⛔ TREND does not gate book screens — trend-against-position "
                         "rows are listed with ⚠️trend-against inline.")
        elif gated:
            lines.append(f"⛔ GATED BY TREND — matched tier, trend != {req_trend} ({len(gated)}):")
            lines += [_fmt_gated(r) for r in gated]
        else:
            lines.append(f"⛔ GATED BY TREND: none — every tag-matched name is {req_trend}.")
    elif gated:
        lines.append("")
        lines.append(f"⛔ {len(gated)} matched but gated by TREND (need {req_trend}) "
                     f"— reply 'show gated' to list them.")

    # Book mode loud-failure: held names whose side is flat/unjudgeable are never
    # silently dropped — name them.
    if book_mode and no_side:
        lines.append("")
        names = ", ".join(sorted(f"{r['ticker']}({r.get('_pside') or '?'})" for r in no_side))
        lines.append(f"◻️ side-indeterminate — held but no long/short verdict: {names} "
                     f"(flat spread or unjudgeable legs; check ingest).")

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


# Long SCREEN output → .txt attachment (REPORT UPLOAD pattern) rather than an
# inline wall. Telegram caps at 4096 chars; the 'everything' lens easily exceeds
# it. Thresholds match the DAYPACK/REPORT convention.
_SCREEN_INLINE_MAX_LINES = 40
_SCREEN_INLINE_MAX_CHARS = 3500


def _maybe_attach(result, q):
    """Short SCREEN result → inline str (unchanged). Long result → a document-reply
    dict {document_name, document_text, caption} the telegram listener sends as a
    .txt. Non-str results (guards/prompts) pass through untouched."""
    if not isinstance(result, str):
        return result
    n_lines = result.count("\n") + 1
    if n_lines <= _SCREEN_INLINE_MAX_LINES and len(result) <= _SCREEN_INLINE_MAX_CHARS:
        return result
    from datetime import date
    scope = ("everything" if q.get("everything")
             else q.get("source") or ("book" if q.get("held") else "posmon"))
    direction = q.get("direction") or "screen"
    return {"document_name": f"screen_{scope}_{direction}_{date.today()}.txt",
            "document_text": result,
            "caption": (f"🔎 SCREEN {scope} {direction} — {n_lines} rows, "
                        f"{len(result):,} chars (full list attached)")}


def _resolve(q: dict, chat_id):
    """Token-guard, ask-for-direction (storing pending), or execute. The reply is
    always the acknowledgment — a follow-up is never acked without running."""
    # Retired sector first: it is a MORE specific answer than "didn't recognize",
    # and it must beat the direction prompt too — asking for longs/shorts on a
    # sector that cannot exist just defers the same false zero by one message.
    if q.get("retired_sector"):
        return _retired_guard(q["retired_sector"])
    if q.get("unrecognized"):
        return _token_guard(q["unrecognized"])
    if not q.get("direction"):
        _set_pending(chat_id, q)   # remember filters so a "longs"/"shorts" reply completes it
        got = _describe(q)
        return ("🔎 SCREEN needs a direction — reply **longs** or **shorts** "
                "(the TREND gate is tied to it)."
                + (f"\nFilters so far: {got}." if got else ""))
    _set_pending(chat_id, q)       # remember the executed query so "show gated" etc. re-run it
    return _maybe_attach(run_screen_q(q), q)


def run_screen(text: str) -> str:
    """Convenience one-shot (no chat/pending) — used by tests and CLI."""
    q = parse_query(text)
    if q.get("retired_sector"):
        return _retired_guard(q["retired_sector"])
    if q.get("unrecognized"):
        return _token_guard(q["unrecognized"])
    if not q.get("direction"):
        return ("🔎 SCREEN needs a direction — longs or shorts. E.g. "
                "`SCREEN energy longs`.")
    return run_screen_q(q)


def handle_screen_command(text, chat_id=None):
    """Telegram listener hook. Handles the SCREEN sentinel AND follow-up replies to
    a pending query for this chat. Returns a reply string (results / guard / prompt)
    or None if the message isn't screen-related. Never a bare ack for a follow-up.
    """
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
