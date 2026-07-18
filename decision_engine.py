# spotgamma stripped from live path 2026-05-24; historical snapshots still ingested for ML
"""Bot's decision brain — Claude API call over the multi-source corpus.

The trade decision is NOT a Python rule lookup. It's a Claude API call that
synthesizes:
  - Hedgeye Quad + VIX bucket            (monitor_context.get_hedgeye_ctx)
  - Hedgeye Risk Range zone              (latest hedgeye_risk_ranges row)
  - MFR range + Hurst + trend            (mfr_snapshots latest)
  - Yahoo live price                     (yfinance_client.latest)
  - Recent corpus snippets (FTS)         (HU transcripts, Macro Show, VolSignals)
SpotGamma snapshots are no longer read in the live decision path
(2026-05-24); the ingestion pipeline still writes spotgamma_snapshots /
spotgamma_* corpus_documents rows for future ML training.

Output is constrained to the bps sizing schema defined in recommender.py
(ALLOWED_BPS, PER_FILL_CEILING_USD). Claude proposes a tier; Python validates
+ persists.

See memory: project_decision_engine_architecture.md for the full rationale.
The bps + $1K ceiling is the CONSTRAINT, not what we calculate.

CLI smoke test:
    py decision_engine.py --ticker OIH
    py decision_engine.py --ticker OIH --signal-origin rta --signal-conviction "Best Idea"
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


CLAUDE_MODEL = os.environ.get("DECISION_ENGINE_MODEL", "claude-sonnet-4-5")
CORPUS_SNIPPET_LIMIT = 4   # how many corpus snippets to fold into the prompt
CORPUS_MAX_CHARS = 2400    # cap total corpus text in prompt


# ─────────────────────────── LLM cost ledger (mig 033) ───────────────
#
# Per-million-token Anthropic published rates (2026-Q2). cache_read is
# typically ~10x cheaper than fresh input; cache_creation is the same
# as input for the first call then never recurs for a TTL window.
# Update here when Anthropic changes pricing; the ledger column stores
# the dollar amount computed at insert-time so old rows aren't
# retroactively rewritten.
_LLM_PRICING_PER_MTOK = {
    # Haiku 4.5: $1 / $5 / $0.10 (in / out / cache-read)
    "claude-haiku-4-5":        {"in": 1.00,  "out": 5.00,  "cache_read": 0.10, "cache_creation": 1.25},
    "claude-haiku-4-5-20251001": {"in": 1.00,  "out": 5.00,  "cache_read": 0.10, "cache_creation": 1.25},
    # Sonnet 4.5: $3 / $15 / $0.30
    "claude-sonnet-4-5":       {"in": 3.00,  "out": 15.00, "cache_read": 0.30, "cache_creation": 3.75},
    "claude-sonnet-4-6":       {"in": 3.00,  "out": 15.00, "cache_read": 0.30, "cache_creation": 3.75},
    # Opus 4.x: $15 / $75 / $1.50
    "claude-opus-4-7":         {"in": 15.00, "out": 75.00, "cache_read": 1.50, "cache_creation": 18.75},
}


def _llm_cost_estimate(model: str, *, input_tokens: int, output_tokens: int,
                       cache_read_tokens: int = 0,
                       cache_creation_tokens: int = 0) -> float:
    """USD estimate for one Claude API call. Returns 0.0 on unknown
    model so a pricing-table miss never breaks the call site."""
    rates = _LLM_PRICING_PER_MTOK.get(model)
    if not rates:
        # Try a prefix match for date-suffixed model IDs we forgot to pin.
        for k, v in _LLM_PRICING_PER_MTOK.items():
            if model.startswith(k):
                rates = v
                break
    if not rates:
        log.debug("llm_calls: no pricing for model %r; cost=0", model)
        return 0.0
    cost = (
        (input_tokens          * rates["in"]            )
        + (output_tokens         * rates["out"]           )
        + (cache_read_tokens     * rates["cache_read"]    )
        + (cache_creation_tokens * rates["cache_creation"])
    ) / 1_000_000.0
    return round(cost, 6)


def _log_llm_call(*, model: str, caller: str, ticker: Optional[str],
                  input_tokens: int = 0, output_tokens: int = 0,
                  cache_read_tokens: int = 0,
                  cache_creation_tokens: int = 0,
                  notes: Optional[str] = None) -> None:
    """Append one row to llm_calls. Best-effort — never lets the ledger
    raise back into the caller (the call already happened, the cost is
    already accrued, we just want to record it)."""
    try:
        cost = _llm_cost_estimate(
            model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )
        import db_pg
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO llm_calls
                        (model, caller, ticker,
                         input_tokens, output_tokens,
                         cache_read_tokens, cache_creation_tokens,
                         est_cost_usd, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (model, caller, ticker,
                     int(input_tokens), int(output_tokens),
                     int(cache_read_tokens), int(cache_creation_tokens),
                     cost, notes),
                )
            conn.commit()
    except Exception as e:
        log.debug("llm_calls: ledger insert failed (%s)", e)

# Path to the operating-rules canon. Edits here propagate without code change.
from pathlib import Path as _Path
FRAMEWORK_CANON_PATH = _Path(__file__).parent / "data" / "reference" / "framework_canon.md"


def _load_framework_canon() -> str:
    """Read framework_canon.md. Returns empty string if missing — engine will
    still function on the system-prompt rules alone, just with less SpotGamma
    and market-maker-mechanics context."""
    try:
        if FRAMEWORK_CANON_PATH.exists():
            return FRAMEWORK_CANON_PATH.read_text(encoding="utf-8")
    except Exception as e:
        log.warning("framework_canon load failed: %s", e)
    return ""


def _doctrine_summary_block() -> str:
    """Static Hedgeye-doctrine summary for the cached framework block:
    Quad definitions, position-sizing caps, long/short ratio. Identical
    across all tickers/calls (changes only with the doctrine YAML), so it
    belongs in the cached prefix, not the per-ticker context."""
    try:
        from tools.doctrine import load_doctrine
        d = load_doctrine()
        lines = ["## Hedgeye GIP Quad doctrine (reference)"]
        for q, desc in (d.get("quad_definitions") or {}).items():
            lines.append(f"- {q}: {desc}")
        caps = d.get("position_sizing_caps") or {}
        lines.append("Position-sizing caps (max % of account per position): "
                      + ", ".join(
                          f"{k}={v.get('max_pct', v.get('max_long_pct'))}%"
                          for k, v in caps.items()))
        lsr = (d.get("long_short_ratio") or {}).get("max_long_short_ratio")
        if lsr:
            lines.append(f"Max long:short equity allocation ratio = {lsr}.")
        fd = d.get("formation_doctrine") or {}
        if fd:
            lines.append(f"TRADE/TREND/TAIL: {fd.get('bullish','')} "
                          f"{fd.get('bearish','')}")
        return "\n".join(lines)
    except Exception as e:
        log.debug("doctrine summary block skipped: %s", e)
        return ""


def _static_framework_block() -> str:
    """The cacheable static prefix sent on EVERY decide() call: the
    framework canon (operating rulebook) + Hedgeye doctrine summary.
    Identical across tickers and calls until the canon file or doctrine
    YAML changes — so Anthropic prompt-caching gives a ~90% input-token
    discount on it after the first call (~40% total cost cut)."""
    parts = []
    canon = _load_framework_canon()
    if canon:
        parts.append("## Framework canon (authoritative operating rules)")
        parts.append(canon)
    dsum = _doctrine_summary_block()
    if dsum:
        parts.append(dsum)
    return "\n\n".join(parts).strip()


# ─────────────────────────── Context gathering ───────────────────────────

def _get_risk_range(ticker: str) -> Optional[dict]:
    """Most recent Hedgeye Risk Range row for the ticker, as dict.
    Includes buy_trade, sell_trade, trend, prev_close, signal_date.

    Staleness-gated (2026-07-06): the row is passed through
    db_pg._gate_rr_row, which stamps `age_tdays` + `stale` and — when the row
    is older than RR_MAX_AGE_DAYS trading days — BLANKS buy_trade/sell_trade/
    trend (moving the raw values to stale_buy_trade/stale_sell_trade/
    stale_trend). Downstream percent-of-RR math, zone, side/narrative and the
    RR/MFR divergence flag therefore all see a stale range as absent — never
    as a live/current range. See db_pg._gate_rr_row for the HARD RULE."""
    try:
        import db_pg
        import psycopg2.extras
        with db_pg.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT ticker, signal_date, trend, buy_trade, sell_trade,
                           prev_close, description
                      FROM hedgeye_risk_ranges
                     WHERE ticker = %s
                     ORDER BY signal_date DESC
                     LIMIT 1
                    """,
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                return db_pg._gate_rr_row(dict(row)) if row else None
    except Exception as e:
        log.warning("decision_engine: risk-range fetch failed for %s: %s", ticker, e)
        return None


def _get_etf_pro_range(ticker: str) -> Optional[dict]:
    """Most recent Hedgeye ETF Pro Range for the ticker (Monday emails, weekly
    cadence, 18 tickers). Goes stale by Wednesday — caller should treat
    week_of >= current_monday-7d as fresh."""
    try:
        import db_pg
        import psycopg2.extras
        with db_pg.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT ticker, week_of, range_low, range_high, description, bias
                      FROM hedgeye_etf_pro_ranges
                     WHERE ticker = %s
                     ORDER BY week_of DESC
                     LIMIT 1
                    """,
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        log.warning("decision_engine: etf-pro range fetch failed for %s: %s", ticker, e)
        return None


def _get_mfr_latest(ticker: str) -> Optional[dict]:
    """Most recent mfr_snapshots row for the ticker, typed columns only.

    Includes the gamma-wall columns (call_wall_mfr / put_wall_mfr /
    zero_gamma / absolute_gamma / iv30_mfr) added in migration 028 — they
    are None for tickers MFR doesn't price options on (commodities, FX,
    VIX) and the notifier omits the wall line entirely in that case.
    """
    try:
        import db_pg
        import psycopg2.extras
        with db_pg.get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute(
                    """
                    SELECT ticker, snapshot_date, price, range_low, range_high,
                           trend_signal, momentum_signal, hurst, hurst_3mo,
                           iv, rv, daily_pct_change, previous_day_volume,
                           call_wall_mfr, put_wall_mfr, zero_gamma,
                           absolute_gamma, iv30_mfr,
                           fetched_at
                      FROM mfr_snapshots
                     WHERE ticker = %s
                     ORDER BY snapshot_date DESC, fetched_at DESC
                     LIMIT 1
                    """,
                    (ticker.upper(),),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        log.warning("decision_engine: mfr fetch failed for %s: %s", ticker, e)
        return None


def _get_spotgamma_latest(ticker: str) -> Optional[dict]:
    """SpotGamma stripped from live decision path 2026-05-24. Always None.
    Ingestion still writes spotgamma_snapshots rows for future ML training,
    but the live decision path no longer reads them. To restore: delete
    this stub and uncomment the spotgamma_client.latest() call below."""
    # try:
    #     import spotgamma_client
    #     return spotgamma_client.latest(ticker)
    # except Exception as e:
    #     log.warning("decision_engine: spotgamma fetch failed for %s: %s", ticker, e)
    return None


def _get_yahoo_latest(ticker: str) -> Optional[dict]:
    """Most recent yahoo_snapshots row via yfinance_client.latest."""
    try:
        import yfinance_client
        return yfinance_client.latest(ticker)
    except Exception as e:
        log.warning("decision_engine: yahoo fetch failed for %s: %s", ticker, e)
        return None


def _get_hedgeye_ctx() -> dict:
    """Current Quad + VIX bucket via monitor_context."""
    try:
        import monitor_context
        return monitor_context.get_hedgeye_ctx() or {}
    except Exception as e:
        log.warning("decision_engine: hedgeye_ctx fetch failed: %s", e)
        return {}


def _get_corpus_snippets(ticker: str, *, signal_conviction: Optional[str]) -> str:
    """Pull relevant corpus snippets via FTS — HU transcripts, Macro Show
    summary notes, VolSignals. Includes ticker + theme keywords derived from
    the signal. Returns a formatted prompt block or empty string."""
    try:
        import corpus_rag
        # Build search terms: the ticker itself, the conviction, and a few
        # framework keywords that bias toward the most relevant lessons.
        seed_terms = [ticker.upper()]
        if signal_conviction:
            seed_terms.append(signal_conviction)
        # Extra theme tokens — let corpus_rag's extract_query_terms expand
        # from a longer text by joining a synthetic prompt.
        synthetic = " ".join(seed_terms + ["risk range", "position sizing", "buy incrementally"])
        block, _terms, _hits = corpus_rag.fetch_and_format(
            synthetic, limit=CORPUS_SNIPPET_LIMIT,
            snippet_chars=500, max_prompt_chars=CORPUS_MAX_CHARS,
        )
        return block or ""
    except Exception as e:
        log.warning("decision_engine: corpus FTS failed for %s: %s", ticker, e)
        return ""


def _get_flowpatrol_for_ticker(ticker: str) -> str:
    """SpotGamma stripped from live decision path 2026-05-24. Always empty.
    Flow Patrol corpus docs are still ingested for ML training."""
    return ""
    # Disabled. Original implementation kept below for restore.
    if not ticker:
        return ""
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT full_text, source_date
                  FROM corpus_documents
                 WHERE source = 'spotgamma_flowpatrol'
                 ORDER BY source_date DESC, id DESC
                 LIMIT 1
                """
            )
            row = cur.fetchone()
        if not row or not row[0]:
            return ""
        text, sdate = row[0], row[1]
        lines = text.splitlines()

        # Market-wide header: keep through the Executive Summary section.
        head, in_exec = [], False
        for ln in lines:
            low = ln.strip().lower()
            if low.startswith("## "):
                in_exec = ("headline" in low or "executive summary" in low
                           or "summary" in low)
            if in_exec or ln.strip().startswith("#"):
                head.append(ln)
            if len(head) >= 25:
                break

        t = ticker.upper()
        hits = [ln.strip() for ln in lines
                if t in ln.upper() and len(ln.strip()) > 3][:8]

        parts = [f"## SpotGamma Flow Patrol ({sdate})"]
        if head:
            parts.append("\n".join(head).strip())
        if hits:
            parts.append(f"\nFlow Patrol mentions of {t}:")
            parts += [f"- {h}" for h in hits]
        elif not head:
            return ""
        out = "\n".join(parts).strip()
        return out[:1800]
    except Exception as e:
        log.debug("flowpatrol lookup failed for %s: %s", ticker, e)
        return ""


def _get_macro_commentary_for_ticker(ticker: str) -> str:
    """Keith's per-ticker Macro Show take from the latest
    per_ticker_commentary.json (built by
    tools.extract_macro_show_commentary). Empty string if absent."""
    if not ticker:
        return ""
    try:
        import glob as _glob
        from pathlib import Path as _P
        base = _P(__file__).resolve().parent / "data" / "snapshots" / "hedgeye"
        dirs = sorted(_glob.glob(str(base / "20*")), reverse=True)
        for d in dirs:
            j = _P(d) / "macro_show" / "per_ticker_commentary.json"
            if j.is_file():
                data = json.loads(j.read_text(encoding="utf-8"))
                snip = data.get(ticker.upper())
                if snip:
                    return ("## Macro Show commentary (Keith's daily take):\n"
                            + snip)
                return ""
        return ""
    except Exception as e:
        log.debug("macro commentary lookup failed for %s: %s", ticker, e)
        return ""


def gather_context(ticker: str, *, signal_conviction: Optional[str] = None) -> dict:
    """Assemble every available context piece for a ticker. Returns a dict
    with each source's data plus a `_corpus_block` formatted prompt string.
    Every field is independently None-safe — missing sources don't break."""
    return {
        "ticker":         ticker.upper(),
        "as_of":          datetime.utcnow().isoformat() + "Z",
        "hedgeye_macro":  _get_hedgeye_ctx(),
        "risk_range":     _get_risk_range(ticker),
        "etf_pro_range":  _get_etf_pro_range(ticker),
        "spotgamma":      _get_spotgamma_latest(ticker),
        "mfr":            _get_mfr_latest(ticker),
        "yahoo":          _get_yahoo_latest(ticker),
        "_corpus_block":  _get_corpus_snippets(ticker, signal_conviction=signal_conviction),
    }


# ─────────────────────────── Prompt construction ───────────────────────────

_SYSTEM_PROMPT = """You are the decision brain of a personal trading bot operating
inside Keith McCullough's HEDGEYE framework. You must reason FROM the Hedgeye
framework defined below, NOT from generic macro intuition. The Hedgeye Quad
model and sector favorability tables override any conflicting generic priors
you might have. If your reasoning ever conflicts with the framework below,
defer to the framework.

═══════════════════════════════════════════════════════════════════════════
HEDGEYE GIP MODEL — GROUND TRUTH (memorize this)
═══════════════════════════════════════════════════════════════════════════

The GIP (Growth, Inflation, Policy) model classifies the economy into FOUR
QUADS based on the year-over-year RATE OF CHANGE of growth and inflation:

  QUAD 1: Growth ↑ AND Inflation ↓   ("Goldilocks")
  QUAD 2: Growth ↑ AND Inflation ↑   ("Reflation / Pro-cyclical")
  QUAD 3: Growth ↓ AND Inflation ↑   ("Stagflation")
  QUAD 4: Growth ↓ AND Inflation ↓   ("Disinflation / Deflation")

Memorize this. Quad 2 is GROWTH UP, INFLATION UP — NOT growth down.

═══════════════════════════════════════════════════════════════════════════
SECTOR FAVORABILITY MATRIX (Keith's canonical mapping)
═══════════════════════════════════════════════════════════════════════════

LONGS (favored / overweight) in each Quad:

  QUAD 1: Tech (XLK), Consumer Discretionary (XLY), Financials (XLF),
          Communications (XLC), Industrials (XLI), Small-Caps (IWM)
          -> Risk-on, growth-equity, cyclicals

  QUAD 2: ENERGY (XLE, XOP, OIH), Materials (XLB), Industrials (XLI),
          Financials (XLF), Tech (selective), Bitcoin (BTC), Small-Caps,
          Commodities broadly -> Pro-cyclical + inflation hedges. Energy
          is BULLISH in Quad 2.

  QUAD 3: Energy (XLE), Gold (GLD), Gold Miners (GDX), Silver (SLV),
          Utilities (XLU), Staples (XLP), TIPS -> Inflation hedges + defensives

  QUAD 4: Bonds (TLT, IEF, AGG), Utilities (XLU), Staples (XLP), Healthcare
          (XLV), Gold, US Dollar (UUP) -> Defensives + duration

SHORTS (avoid / underweight) in each Quad:

  QUAD 1: Bonds, Gold, Staples, Utilities (defensive sectors are dead weight)
  QUAD 2: Bonds (especially long duration), Utilities, Staples
  QUAD 3: Consumer Discretionary, Tech (growth gets crushed by rising rates),
          Small-Caps, Long-duration Bonds
  QUAD 4: Energy, Materials, Small-Caps, Industrials (cyclicals collapse)

If the user's context says "Quad 2" and the ticker is energy (XLE, XOP, OIH,
BNO, energy single-names), that is FRAMEWORK-ALIGNED LONG. Do not call it
a "headwind." Quad 2 is energy's best regime alongside Quad 3.

═══════════════════════════════════════════════════════════════════════════
VIX BUCKET RULES (Keith's volatility regime framework)
═══════════════════════════════════════════════════════════════════════════

  VIX 11-15:   "Uninvestable" (complacency, tops form here)
  VIX 16-19:   "Investable"   (normal trending tape, longs work)
  VIX 20+:     "Level Break"  (volatility expansion, defensive)

═══════════════════════════════════════════════════════════════════════════
HARD CONSTRAINTS — your output MUST respect these
═══════════════════════════════════════════════════════════════════════════

1. `bps` must be one of: 50 or 100.
   - 100 = starter position (first BUY for a ticker) OR aggressive add
   - 50  = conservative add to an existing position
2. `conviction` must be one of: "Best Idea", "Adding", "Reducing", "Remove", "Monitor".
3. `direction` must be one of: "long", "short", "close".
4. `action` must be one of: "BUY", "ADD", "TRIM", "SELL", "WATCH".
5. The bps × account_value (provided in user message), clamped at $1,000,
   IS the dollar size. Your reasoning should reflect this constraint.
6. Build incrementally: any single fill ≤ ~33% of full target position.
   If your conviction is high, propose multiple legs over time.

═══════════════════════════════════════════════════════════════════════════
DECISION ALGORITHM
═══════════════════════════════════════════════════════════════════════════

STEP 1 — Is the ticker FRAMEWORK-ALIGNED for the current Quad?
  Check ticker against the sector favorability matrix above. Long-favorable,
  short-favorable, or neutral?

STEP 2 — What does Hedgeye's range say? (Hedgeye is the master range source)
  Range source priority — use the FIRST that's available:
    (a) Hedgeye Risk Range (daily RTA, fields buy_trade/sell_trade) — PRIMARY.
    (b) Hedgeye ETF Pro Range (weekly Monday email, fields range_low/range_high) — SECONDARY,
        only for the 18 ETF Pro tickers. Goes stale by Wednesday, so check
        week_of >= today-7d.
    (c) MFR fractal range — TERTIARY, only when neither Hedgeye source exists.
        MFR is a useful confirmation tool but its ranges are weaker than
        Hedgeye's — never override a Hedgeye source with MFR.
  Once you've picked the range source, interpret price position relative to it:
    Bottom-third of range = scale-in zone (favorable for adds on framework-aligned longs).
    Top-third of range = trim zone.
    Middle third = hold, no action.
    Below low boundary  = breach low (deeper opportunity for framework-aligned long; risk-off for framework-aligned short).
    Above high boundary = breach high (trim aggressively; or short on framework-aligned shorts).
  If ALL three range sources are missing -> Monitor.

STEP 3 — Does SpotGamma corroborate?
  Bottom-third + below Put Wall = strong dealer support (negative gamma below -> magnet up).
  Top-third + above Call Wall = strong dealer resistance (negative gamma above -> ceiling).
  Hedge Wall is far-OTM, usually not actionable.
  Negative net gamma regime = expect amplified moves, size smaller.

  CITATION REQUIREMENT: When SpotGamma data is present in the user context,
  your reasoning MUST reference at least one specific level (call wall, put
  wall, key gamma strike, hedge wall) by name AND value. Do not say "SG looks
  supportive" — say "price $X sits above put wall $Y, which limits downside
  hedging pressure" or "price $X is $Z below the call wall at $W, ceiling
  intact." If SG data is absent, say so explicitly in reasoning rather than
  inventing levels.

STEP 4 — Does MFR corroborate? (MFR is a CONFIRMATION tool, not a primary signal)
  Hurst > 0.5 = trending; favor trend continuation.
  Hurst < 0.5 = mean-reverting; favor fading extremes.
  MFR trend_signal aligned with framework direction = added confirmation vote.
  If Hedgeye Risk Range or ETF Pro Range is the active range source, MFR's
  range numbers are IGNORED — only its Hurst + trend_signal count as a vote.

STEP 5 — Synthesize:
  Four votes are: (1) Quad/sector alignment, (2) Hedgeye range zone (Risk Range
  or ETF Pro Range; MFR substitutes ONLY when both are missing), (3) SpotGamma
  walls/regime, (4) MFR Hurst + trend_signal.
  All four agree -> Best Idea, 100 bps starter or 100 bps add.
  Three of four agree -> Adding, 50 bps.
  Two or fewer agree, or sharp disagreement -> Monitor, no trade.

  HARD CEILING RULES (NOT OVERRIDABLE under any circumstance, no matter how
  strong the framework alignment, no matter what other layers say, no matter
  what HU doctrine you cite):

    R1. ALL Hedgeye range sources missing AND MFR also unavailable -> Monitor.
        Force conviction='Monitor', action='WATCH', bps=null. NO EXCEPTIONS.

    R2. Hedgeye range sources missing but MFR present -> max conviction='Adding',
        max bps=50, action='ADD' or 'BUY'. NO EXCEPTIONS. Hedgeye is the master
        signal source; without it we never go full-size. If you find yourself
        about to write 'Best Idea' or '100 bps' here, STOP and downshift to
        'Adding' / 50 bps regardless of how strong the other votes are.

    R3. Account value $0 or unknown -> Monitor. Can't size without a denominator.

  These ceilings exist because the bot's edge IS Keith's signal. Acting full-
  size without a Hedgeye range means trading on subordinate data — that's
  exactly the kind of off-process behavior the canon's "rules-based + incremental"
  doctrine prohibits.

═══════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT — return ONLY a single JSON object, no prose around it
═══════════════════════════════════════════════════════════════════════════

{
  "conviction":  "<Best Idea|Adding|Reducing|Remove|Monitor>",
  "direction":   "<long|short|close>",
  "action":      "<BUY|ADD|TRIM|SELL|WATCH>",
  "bps":         <50 or 100, or null if action is TRIM/SELL/WATCH/no-trade>,
  "reasoning":   "<one paragraph showing the STEP 1-5 walkthrough in plain prose>",
  "evidence":    ["<bullet 1>", "<bullet 2>", ...],
  "framework_alignment_check": "<one sentence: 'Quad N favors/disfavors this sector because...'>",
  "confidence":  <0.0 to 1.0>
}

The `framework_alignment_check` field is mandatory. If you write something that
contradicts the framework above (e.g. "Quad 2 is bad for energy"), the bot's
output validator will REJECT the decision and you will have produced no value.
Triple-check that your sector view matches the matrix above.
"""


def _spotgamma_framing_line(sg: dict, price) -> str:
    """Deterministic plain-English summary of where price sits relative to
    SG levels. Forces the LLM prompt to carry the exact wording it must
    reference (call_wall / put_wall / key_gamma_strike / hedge_wall) so the
    post-validator can confirm citation."""
    if not sg or not isinstance(sg, dict):
        return ("SpotGamma framing: no data available for this ticker "
                "(not in tier-1 watchlist or sweep failed).")
    try:
        p = float(price) if price is not None else None
    except (TypeError, ValueError):
        p = None
    if p is None:
        return "SpotGamma framing: price unavailable; cannot anchor levels."

    def _side(level):
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return None, None, None
        diff = p - lv
        pct = (diff / lv * 100.0) if lv else 0.0
        if abs(pct) < 0.05:
            side = "at"
        elif diff > 0:
            side = "above"
        else:
            side = "below"
        return lv, side, abs(pct)

    lines = [f"SpotGamma framing for price ${p}:"]
    cw, pw = sg.get("call_wall"), sg.get("put_wall")
    kg = sg.get("key_gamma_strike")
    hw = sg.get("hedge_wall")

    lv, side, pct = _side(cw)
    if lv is not None:
        lines.append(f"  call wall ${lv}   ->  price is {side} call wall by {pct:.1f}%")
    lv, side, pct = _side(pw)
    if lv is not None:
        lines.append(f"  put wall ${lv}    ->  price is {side} put wall by {pct:.1f}%")
    lv, side, pct = _side(kg)
    if lv is not None:
        regime = ("gamma support active" if side == "above"
                  else "below gamma — momentum risk")
        lines.append(f"  key gamma strike ${lv}  ->  price is {side} key gamma strike by {pct:.1f}% ({regime})")
    lv, side, pct = _side(hw)
    if lv is not None:
        lines.append(f"  hedge wall ${lv}  ->  defended {('below' if side=='above' else 'above')} "
                     "(when price crosses, dealer hedging flips)")

    if len(lines) == 1:
        return "SpotGamma framing: SG row exists but no numeric levels populated."
    return "\n".join(lines)


MFR_SNAPSHOT_MAX_AGE_HOURS = float(os.getenv("MFR_SNAPSHOT_MAX_AGE_HOURS", "20"))


def _snapshot_stale_note(snapshot_date, fetched_at, *, now=None,
                         max_age_hours: float = MFR_SNAPSHOT_MAX_AGE_HOURS):
    if now is None:
        now = datetime.now(timezone.utc)
    ref = None
    if fetched_at is not None:
        ref = fetched_at
        if getattr(ref, "tzinfo", None) is None:
            ref = ref.replace(tzinfo=timezone.utc)
    elif snapshot_date is not None:
        d = snapshot_date
        ref = datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=timezone.utc)
    else:
        return "age unknown"
    age_hours = (now - ref).total_seconds() / 3600.0
    if age_hours < max_age_hours:
        return None
    if age_hours >= 48:
        return f"{int(round(age_hours / 24))}d old"
    return f"{int(round(age_hours))}h old"


def _mfr_line_stale_note(price_note, snapshot_date, fetched_at, *, now=None):
    """Stale note for the MFR zone line specifically. The MFR range_low/high (and
    any rp computed off them) come from the MFR snapshot, so the MFR rp is stale
    whenever that snapshot is old — EVEN when a live price replaced the snapshot
    price. Returns the price-source note when present (price staleness dominates),
    else a 'MFR range <age>' note when the MFR snapshot itself is stale, else None.
    Pure; no DB, no money logic; now injectable for tests. Only the MFR line uses
    this — the Hedgeye RR / ETF Pro ranges are separately sourced and must NOT
    inherit the MFR snapshot's age."""
    if price_note:
        return price_note
    age = _snapshot_stale_note(snapshot_date, fetched_at, now=now)
    return f"MFR range {age}" if age else None


def _zone_summary_line(label: str, price, low, high, *, stale_note=None) -> str:
    """Deterministic one-liner the LLM cannot misread.

    Pre-computes 'where is price within [low, high]' using price_monitor.compute_zone
    so the LLM doesn't get to invent 'above range high' / 'breach' framing from
    raw JSON numbers. Mirrors the wording in alert text so contradictions are
    obvious to the post-validator.
    """
    try:
        p = float(price) if price is not None else None
        lo = float(low) if low is not None else None
        hi = float(high) if high is not None else None
    except (TypeError, ValueError):
        return f"{label} zone: unavailable (non-numeric inputs price={price!r} low={low!r} high={high!r})"
    if p is None or lo is None or hi is None:
        return f"{label} zone: unavailable (missing price/low/high)"
    if lo >= hi:
        return f"{label} zone for price {p} in range [{lo}, {hi}]: invalid range (low >= high)"
    try:
        from price_monitor import compute_zone
    except Exception:
        return f"{label} zone: compute_zone import failed"
    zone = compute_zone(p, lo, hi)
    pct = (p - lo) / (hi - lo) * 100.0
    if zone == "above_range":
        verdict = "BREACH HIGH"
    elif zone == "below_range":
        verdict = "BREACH LOW"
    else:
        verdict = "NOT a breach"
    line = (f"{label} zone for price {p} in range [{lo}, {hi}]: "
            f"{zone} ({pct:.0f}% through range; {verdict})")
    if stale_note:
        line = f"[STALE: {stale_note}] {line}"
    return line


def _hurst_regime_line(value) -> Optional[str]:
    """Classify MFR Hurst exponent into a trading regime + framing.

    H >= 0.6 = trending, H <= 0.4 = mean-reverting, in between = random walk.
    Returns None when Hurst is missing/non-numeric so callers can skip the line
    the same way they skip an absent trend_signal.
    """
    try:
        h = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if h is None:
        return None
    if h >= 0.6:
        regime, framing = "trending", "trend persistence high — breakouts more likely to extend"
    elif h <= 0.4:
        regime, framing = "mean_reverting", "mean-reverting regime — fade extremes, range edges hold"
    else:
        regime, framing = "random_walk", "no clear regime — give signals less conviction"
    return f"Hurst regime: {regime} (H={h:.2f}) — {framing}"


def _vol_premium_line(iv, rv) -> Optional[str]:
    """Implied-vs-realized vol premium framing.

    premium% = (IV - RV) / RV * 100.
      > +15%        sellers' market  (options rich — favor premium selling /
                                      expect mean reversion of IV)
      -10% .. +15%  fair             (no strong vol edge)
      < -10%        buyers' market   (options cheap — favor owning convexity)
    Returns None when IV/RV missing or RV<=0.
    """
    try:
        ivf = float(iv) if iv is not None else None
        rvf = float(rv) if rv is not None else None
    except (TypeError, ValueError):
        return None
    if ivf is None or rvf is None or rvf <= 0:
        return None
    pct = (ivf - rvf) / rvf * 100.0
    if pct > 15.0:
        cat = "sellers_market"
    elif pct < -10.0:
        cat = "buyers_market"
    else:
        cat = "fair_market"
    return f"Vol premium: IV {ivf:g} vs RV {rvf:g} = {pct:+.0f}% ({cat})"


def _xs_fmt(v) -> str:
    """Compact number format for the cross-source line: ints render bare,
    fractionals keep up to 2 decimals with trailing zeros stripped."""
    if v is None:
        return "?"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "?"
    if abs(f - round(f)) < 1e-9:
        return f"{int(round(f))}"
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _cross_source_eval(decision_context: Optional[dict]) -> Optional[dict]:
    """Core cross-source range math shared by the LLM-prompt summary and the
    price_monitor short tag.

    Accepts a normalized context with any of these sub-dicts (flat key OR the
    ctx key used by _format_user_message):
        rr / risk_range   -> buy_trade, sell_trade
        mfr               -> range_low, range_high
        etf_pro / etf_pro_range -> range_low, range_high
        sg / spotgamma    -> put_wall, call_wall, hedge_wall, key_gamma_strike

    Returns None when fewer than 2 sources are present. Otherwise a dict:
        parts            list[str]  per-source display fragments
        low_spread       float      % spread across source lows
        high_spread      float      % spread across source highs
        n_sources        int        sources with any range data
        high_conviction  bool       lows AND highs within 1%
        divergence       bool       low OR high spread > 3%
        clusters         list[dict] {center, n_sources, sources, side}
                                    for levels >=3 sources cite within 0.5%
    """
    ctx = decision_context or {}

    def _grp(*keys):
        for k in keys:
            v = ctx.get(k)
            if isinstance(v, dict):
                return v
        return {}

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    rr  = _grp("rr", "risk_range")
    mfr = _grp("mfr")
    etf = _grp("etf_pro", "etf_pro_range")
    sg  = _grp("sg", "spotgamma")

    sources: dict[str, tuple] = {}   # name -> (low, high)
    parts: list[str] = []
    levels: list[tuple] = []         # (source_name, value)

    rr_lo, rr_hi = _f(rr.get("buy_trade")), _f(rr.get("sell_trade"))
    if rr_lo is not None or rr_hi is not None:
        sources["RR"] = (rr_lo, rr_hi)
        parts.append(f"RR {_xs_fmt(rr_lo)}-{_xs_fmt(rr_hi)}")
        if rr_lo is not None: levels.append(("RR", rr_lo))
        if rr_hi is not None: levels.append(("RR", rr_hi))

    mfr_lo, mfr_hi = _f(mfr.get("range_low")), _f(mfr.get("range_high"))
    if mfr_lo is not None or mfr_hi is not None:
        sources["MFR"] = (mfr_lo, mfr_hi)
        parts.append(f"MFR {_xs_fmt(mfr_lo)}-{_xs_fmt(mfr_hi)}")
        if mfr_lo is not None: levels.append(("MFR", mfr_lo))
        if mfr_hi is not None: levels.append(("MFR", mfr_hi))

    etf_lo, etf_hi = _f(etf.get("range_low")), _f(etf.get("range_high"))
    if etf_lo is not None or etf_hi is not None:
        sources["ETF Pro"] = (etf_lo, etf_hi)
        parts.append(f"ETF Pro {_xs_fmt(etf_lo)}-{_xs_fmt(etf_hi)}")
        if etf_lo is not None: levels.append(("ETF Pro", etf_lo))
        if etf_hi is not None: levels.append(("ETF Pro", etf_hi))

    sg_pw = _f(sg.get("put_wall"))
    sg_cw = _f(sg.get("call_wall"))
    sg_hw = _f(sg.get("hedge_wall"))
    sg_kg = _f(sg.get("key_gamma_strike"))
    if sg_pw is not None or sg_cw is not None:
        sources["SG"] = (sg_pw, sg_cw)   # put_wall ~ range low, call_wall ~ range high
        sg_bits = []
        if sg_pw is not None: sg_bits.append(f"put_wall {_xs_fmt(sg_pw)}")
        if sg_cw is not None: sg_bits.append(f"call_wall {_xs_fmt(sg_cw)}")
        if sg_hw is not None: sg_bits.append(f"hedge_wall {_xs_fmt(sg_hw)}")
        if sg_kg is not None: sg_bits.append(f"key_gamma {_xs_fmt(sg_kg)}")
        parts.append("SG " + " / ".join(sg_bits))
        for v in (sg_pw, sg_cw, sg_hw, sg_kg):
            if v is not None:
                levels.append(("SG", v))

    if len(sources) < 2:
        return None

    def _spread_pct(vals):
        vals = [x for x in vals if x is not None]
        if len(vals) < 2:
            return 0.0
        base = sum(vals) / len(vals)
        if base == 0:
            return 0.0
        return (max(vals) - min(vals)) / abs(base) * 100.0

    lows  = [v[0] for v in sources.values() if v[0] is not None]
    highs = [v[1] for v in sources.values() if v[1] is not None]
    low_spread  = _spread_pct(lows)
    high_spread = _spread_pct(highs)
    high_conviction = (
        len(lows) >= 2 and len(highs) >= 2
        and low_spread <= 1.0 and high_spread <= 1.0
    )
    divergence = low_spread > 3.0 or high_spread > 3.0

    # Greedy cluster on sorted level values: members within 0.5% of the
    # cluster anchor. A cluster qualifies when >=3 DISTINCT sources cite it.
    def _median(xs):
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0

    clusters = []
    all_vals = [lv for _, lv in levels]
    if all_vals:
        mid = (min(all_vals) + max(all_vals)) / 2.0
        ordered = sorted(levels, key=lambda t: t[1])
        i = 0
        while i < len(ordered):
            anchor = ordered[i][1]
            members = [ordered[i]]
            j = i + 1
            while j < len(ordered):
                if anchor == 0:
                    break
                if abs(ordered[j][1] - anchor) / abs(anchor) * 100.0 <= 0.5:
                    members.append(ordered[j])
                    j += 1
                else:
                    break
            names = sorted({m[0] for m in members})
            if len(names) >= 3:
                center = _median([m[1] for m in members])
                clusters.append({
                    "center": center,
                    "n_sources": len(names),
                    "sources": names,
                    "side": "UPPER" if center >= mid else "LOWER",
                })
            i = j if j > i + 1 else i + 1

    return {
        "parts": parts,
        "low_spread": low_spread,
        "high_spread": high_spread,
        "n_sources": len(sources),
        "high_conviction": high_conviction,
        "divergence": divergence,
        "clusters": clusters,
    }


def _cross_source_summary(ticker: str, decision_context: Optional[dict]) -> Optional[str]:
    """One-line cross-source range alignment summary for the LLM prompt.

    Example:
      Cross-source: RR 738-745 | MFR 740-748 | ETF Pro 735-745 | SG put_wall
      730 / call_wall 745 / key_gamma 740 — UPPER level cluster at 745
      (4 sources: ETF Pro, MFR, RR, SG)
    """
    ev = _cross_source_eval(decision_context)
    if not ev or not ev["parts"]:
        return None
    line = "Cross-source: " + " | ".join(ev["parts"])
    tail = []
    if ev["high_conviction"]:
        tail.append(f"HIGH-CONVICTION (lows within {ev['low_spread']:.1f}%, "
                    f"highs within {ev['high_spread']:.1f}%)")
    elif ev["divergence"]:
        tail.append(f"sources disagree (low spread {ev['low_spread']:.1f}%, "
                    f"high spread {ev['high_spread']:.1f}%)")
    for c in ev["clusters"]:
        tail.append(f"{c['side']} level cluster at {_xs_fmt(c['center'])} "
                    f"({c['n_sources']} sources: {', '.join(c['sources'])})")
    if tail:
        line += " — " + ", ".join(tail)
    return line


def _quad_doctrine_line(ticker: str) -> Optional[str]:
    """Deterministic Hedgeye Quad-doctrine line for the LLM prompt:
    active quarterly/monthly Quad, whether the ticker is favored
    long/short, its historical quarterly EV, and the asset-class
    position cap. None on any doctrine error (prompt stays clean)."""
    try:
        from tools.doctrine import (
            current_quarterly_quad, current_monthly_quad,
            universe_for_quad, expected_return, asset_class_for,
        )
        t = (ticker or "").upper()
        qq = current_quarterly_quad()
        mq = current_monthly_quad()
        qn = qq.split()[-1]
        longs = set(universe_for_quad(qq, "longs"))
        shorts = set(universe_for_quad(qq, "shorts"))
        if t in longs:
            favored = f"{t} is a Q{qn} FAVORED LONG"
        elif t in shorts:
            favored = f"{t} is a Q{qn} FAVORED SHORT"
        else:
            favored = f"{t} is NOT in the Q{qn} favored long/short universe"
        bits = [f"Hedgeye doctrine: quarterly {qq}, monthly {mq}. {favored}."]
        ev = expected_return(t, qq)
        if ev is not None:
            bits.append(f"Historical {qq} avg return for {t}: {ev:+.1f}% per quarter.")
        ac = asset_class_for(t)
        if ac:
            bits.append(f"Asset class: {ac} (Hedgeye position-sizing cap applies).")
        return "## Hedgeye Quad doctrine\n" + " ".join(bits)
    except Exception:
        return None


def _recent_alerts_line(ticker: str) -> str:
    """Per-call dynamic: alerts_fired for this ticker in the last hour."""
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT boundary, suggested_action, fired_at
                  FROM alerts_fired
                 WHERE ticker = %s AND fired_at >= NOW() - INTERVAL '1 hour'
                 ORDER BY fired_at DESC LIMIT 5
                """,
                ((ticker or "").upper(),),
            )
            rows = cur.fetchall()
        if not rows:
            return "Recent alerts (last hour): none"
        return "Recent alerts (last hour): " + "; ".join(
            f"{b}/{a} @ {ts:%H:%M}" for b, a, ts in rows)
    except Exception as e:
        log.debug("recent-alerts lookup skipped for %s: %s", ticker, e)
        return "Recent alerts (last hour): unavailable"


def _format_user_message(ctx: dict, *, signal_origin: str,
                         signal_conviction: Optional[str],
                         account_value: float) -> tuple[str, str]:
    """Return (per_ticker_static, dynamic_per_call).

    per_ticker_static  — RR/ETF Pro/SG/MFR/macro/corpus/quad-doctrine; the
                          same for a ticker all day (daily snapshot refresh),
                          so it is sent as a 1h-TTL cache_control block.
    dynamic_per_call   — ticker framing, account value, live Yahoo price,
                          recent alerts, cycle timestamp; changes every call.
    """
    # Strip None / empty / heavy fields for prompt clarity, keep typed numbers.
    def _trim(d):
        if not isinstance(d, dict):
            return d
        return {k: v for k, v in d.items()
                if v not in (None, "", {}, [])
                and not str(k).startswith("raw_")
                and not str(k).startswith("_")
                and not str(k) == "full_payload"
                and not str(k) == "raw_text"}

    # ---- dynamic, per-call framing ----
    dyn = []
    dyn.append(f"## Ticker: {ctx['ticker']}")
    dyn.append(f"As of: {ctx['as_of']}")
    dyn.append(f"Account value (for bps sizing): ${account_value:,.0f}")
    dyn.append(f"Signal origin: {signal_origin}")
    if signal_conviction:
        dyn.append(f"Hedgeye-tagged conviction (input): {signal_conviction}")
    dyn.append(_recent_alerts_line(ctx.get("ticker")))
    dyn.append("## Yahoo (live snapshot at decision time)")
    dyn.append(json.dumps(_trim(ctx.get("yahoo") or {}), indent=2, default=str))
    dyn.append("")

    # ---- per-ticker static (daily-stable) ----
    sections = []
    sections.append("## Hedgeye macro context")
    sections.append(json.dumps(_trim(ctx.get("hedgeye_macro") or {}), indent=2, default=str))
    sections.append("")
    # Resolve a "current price" we can use for deterministic zone math. Prefer
    # MFR's own price (its range was computed against it); fall back to Yahoo.
    mfr_block = ctx.get("mfr") or {}
    yahoo_block = ctx.get("yahoo") or {}
    rr_block = ctx.get("risk_range") or {}
    etf_block = ctx.get("etf_pro_range") or {}
    _ticker = (ctx.get("ticker") or "").upper()
    _live_price = None
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE, fetch_prices
        _yf = HEDGEYE_TO_YFINANCE.get(_ticker) or _ticker
        if _yf:
            _live_price = fetch_prices([_yf]).get(_yf)
    except Exception as e:
        log.warning("decision_engine: live price fetch failed for %s: %s", _ticker, e)
    if _live_price is not None:
        _pz = _live_price
        _pnote = None
        _psrc = f"{_live_price} (live yfinance @ decision time)"
    else:
        _pz = mfr_block.get("price") or yahoo_block.get("price")
        _age = _snapshot_stale_note(mfr_block.get("snapshot_date"), mfr_block.get("fetched_at"))
        _pnote = (f"live fetch failed; last snapshot {_age}" if _age else "live fetch failed; using last snapshot")
        _psrc = f"{_pz} (!! {_pnote})"
    # The MFR range comes from the MFR snapshot, so a stale snapshot means a stale
    # MFR rp even when the price above is live — flag it on the MFR line only.
    _mfr_note = _mfr_line_stale_note(_pnote, mfr_block.get("snapshot_date"), mfr_block.get("fetched_at"))
    dyn.append("## Position-in-range - LIVE (Python-computed at decision time; use THESE, not the range JSON below)")
    dyn.append(f"Price used for all range math: {_psrc}")
    dyn.append(_zone_summary_line("Hedgeye Risk Range", _pz, rr_block.get("buy_trade"), rr_block.get("sell_trade"), stale_note=_pnote))
    dyn.append(_zone_summary_line("ETF Pro Range", _pz, etf_block.get("range_low"), etf_block.get("range_high"), stale_note=_pnote))
    dyn.append(_zone_summary_line("MFR", _pz, mfr_block.get("range_low"), mfr_block.get("range_high"), stale_note=_mfr_note))
    dyn.append("")

    # Cross-source range alignment FIRST, above the per-source dumps, so the
    # LLM reads the consensus/divergence verdict before the raw numbers.
    xs_line = _cross_source_summary(ctx.get("ticker"), ctx)
    if xs_line:
        sections.append(xs_line)
        sections.append("")

    # Hedgeye Quad doctrine — strategic/tactical regime + favored side +
    # historical EV tilt, so the LLM weights the framework before ranges.
    quad_line = _quad_doctrine_line(ctx.get("ticker"))
    if quad_line:
        sections.append(quad_line)
        sections.append("")

    sections.append("## Hedgeye Risk Range (latest daily signal — PRIMARY range source)")
    sections.append("(Range levels below are the daily signal. Current position-in-range is computed live in the per-call 'Position-in-range - LIVE' section.)")
    sections.append(json.dumps(_trim(rr_block), indent=2, default=str))
    sections.append("")
    sections.append("## Hedgeye ETF Pro Range (Monday weekly — SECONDARY range source for ETFs)")
    sections.append(json.dumps(_trim(etf_block), indent=2, default=str))
    sections.append("")
    # SpotGamma stripped from live path 2026-05-24 — block intentionally absent.
    # Snapshots and Flow Patrol corpus docs still ingest for future ML training.
    mc_block = _get_macro_commentary_for_ticker(ctx.get("ticker"))
    if mc_block:
        sections.append(mc_block)
        sections.append("")
    sections.append("## MFR (latest fractal range — TERTIARY range source; secondary to Hedgeye)")
    # Deterministic zone + trend so the LLM can't hallucinate "above range high"
    # or "pullback" out of the raw JSON. Anchor wording must match price_monitor.
    mfr_trend = mfr_block.get("trend_signal")
    if mfr_trend:
        sections.append(f"Recent trend: {mfr_trend} (from MFR trend_signal)")
    hurst_line = _hurst_regime_line(mfr_block.get("hurst"))
    if hurst_line:
        sections.append(hurst_line)
    vol_line = _vol_premium_line(mfr_block.get("iv"), mfr_block.get("rv"))
    if vol_line:
        sections.append(vol_line)
    sections.append(json.dumps(_trim(mfr_block), indent=2, default=str))
    sections.append("")
    # NOTE: the live Yahoo snapshot moved to the dynamic per-call block
    # above (price changes every cycle — must NOT be in the 1h cache).

    if ctx.get("_corpus_block"):
        sections.append("## Relevant corpus snippets (Hedgeye U, Macro Show, VolSignals)")
        sections.append(ctx["_corpus_block"])
        sections.append("")

    sections.append("## Task")
    sections.append("Propose the sized recommendation as JSON per the system prompt's schema.")
    return "\n".join(sections), "\n".join(dyn)


# ─────────────────────────── Output validation ───────────────────────────

_UNVERIFIED_TAG = "[REASONING UNVERIFIED] "
_SG_NOT_CITED_TAG = "[SG NOT CITED] "
_SG_CITATION_HINTS = ("call wall", "put wall", "gamma strike",
                      "hedge wall", "vol trigger")

# Substrings worth flagging when they appear in LLM evidence/reasoning but
# don't match the deterministic zone we computed pre-prompt. Lowercased.
_HIGH_BREACH_HINTS = (
    "above mfr range high",
    "above range high",
    "above the range high",
    "above the range's high",
    "above the high",
    "breach high",
    "breached high",
    "breaching high",
    "above the upper band",
    "above the top of the range",
)
_LOW_BREACH_HINTS = (
    "below mfr range low",
    "below range low",
    "below the range low",
    "below the range's low",
    "below the low",
    "breach low",
    "breached low",
    "breaching low",
    "below the lower band",
    "below the bottom of the range",
)


def _zone_from_ctx(ctx: Optional[dict]) -> Optional[str]:
    """Return the deterministic MFR zone for ctx, or None if unknown."""
    if not ctx:
        return None
    mfr = ctx.get("mfr") or {}
    yahoo = ctx.get("yahoo") or {}
    price = mfr.get("price") or yahoo.get("price")
    low = mfr.get("range_low")
    high = mfr.get("range_high")
    try:
        if price is None or low is None or high is None:
            return None
        from price_monitor import compute_zone
        return compute_zone(float(price), float(low), float(high))
    except Exception:
        return None


def _flag_contradictions(decision: dict, ctx: Optional[dict]) -> None:
    """Mutate decision in place: prepend [REASONING UNVERIFIED] to any evidence
    or reasoning text whose breach framing contradicts compute_zone()."""
    zone = _zone_from_ctx(ctx)
    if zone is None:
        return

    def _flag_text(s: str) -> str:
        if not isinstance(s, str):
            return s
        low_s = s.lower()
        if zone != "above_range" and any(h in low_s for h in _HIGH_BREACH_HINTS):
            return _UNVERIFIED_TAG + s
        if zone != "below_range" and any(h in low_s for h in _LOW_BREACH_HINTS):
            return _UNVERIFIED_TAG + s
        return s

    ev = decision.get("evidence")
    if isinstance(ev, list):
        decision["evidence"] = [_flag_text(item) if isinstance(item, str) else item for item in ev]
    elif isinstance(ev, str):
        decision["evidence"] = _flag_text(ev)

    if isinstance(decision.get("reasoning"), str):
        decision["reasoning"] = _flag_text(decision["reasoning"])


def _flag_sg_not_cited(decision: dict, ctx: Optional[dict]) -> None:
    """If ctx has SpotGamma data but the LLM's reasoning doesn't cite any of
    the canonical level names, prepend [SG NOT CITED] to reasoning so the
    alert visibly carries the gap. Don't reject."""
    if not ctx:
        return
    sg = ctx.get("spotgamma") or {}
    if not isinstance(sg, dict):
        return
    # Only flag when SG actually has numeric levels worth citing
    has_levels = any(sg.get(k) is not None
                     for k in ("call_wall", "put_wall",
                               "key_gamma_strike", "hedge_wall",
                               "vol_trigger"))
    if not has_levels:
        return
    reasoning = decision.get("reasoning")
    if not isinstance(reasoning, str):
        return
    blob = reasoning.lower()
    if any(h in blob for h in _SG_CITATION_HINTS):
        return
    # Avoid double-tagging if [REASONING UNVERIFIED] already prepended.
    if not reasoning.startswith(_SG_NOT_CITED_TAG):
        decision["reasoning"] = _SG_NOT_CITED_TAG + reasoning


def _parse_and_validate(text: str, ctx: Optional[dict] = None) -> dict:
    """Extract the JSON object from Claude's response and validate against
    the schema constraints. Raises ValueError on hard violations.

    When ctx is provided, also post-validate the LLM's breach framing against
    the deterministic MFR zone — contradictions get a [REASONING UNVERIFIED]
    prefix on the offending evidence/reasoning string (alert is not rejected).
    """
    # Tolerate prose around the JSON — find first { ... } block.
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in response: {text[:200]}")
    blob = text[start:end + 1]
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON parse failed: {e}; blob: {blob[:200]}")

    from recommender import ALLOWED_BPS

    convictions = {"Best Idea", "Adding", "Reducing", "Remove", "Monitor"}
    directions  = {"long", "short", "close"}
    actions     = {"BUY", "ADD", "TRIM", "SELL", "WATCH"}

    if data.get("conviction") not in convictions:
        raise ValueError(f"conviction {data.get('conviction')!r} not in {convictions}")
    if data.get("direction") not in directions:
        raise ValueError(f"direction {data.get('direction')!r} not in {directions}")
    if data.get("action") not in actions:
        raise ValueError(f"action {data.get('action')!r} not in {actions}")

    bps = data.get("bps")
    if data["action"] in ("BUY", "ADD"):
        if bps not in ALLOWED_BPS:
            raise ValueError(f"bps {bps!r} must be one of {ALLOWED_BPS} for action={data['action']}")
    elif bps is not None and bps not in ALLOWED_BPS:
        # TRIM/SELL/WATCH: bps should be null but tolerate ALLOWED values silently
        pass

    confidence = data.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    data["confidence"] = max(0.0, min(1.0, confidence))

    # Always present these fields even if model omitted
    data.setdefault("reasoning", "")
    data.setdefault("evidence", [])

    # If we have ctx, flag breach-framing that contradicts compute_zone.
    _flag_contradictions(data, ctx)
    # Flag when SG was available but the LLM didn't cite any level by name.
    _flag_sg_not_cited(data, ctx)
    return data


# ─────────────────────────── Main entry point ───────────────────────────

NOTIFIER_MODEL = os.environ.get("DECISION_ENGINE_MODEL", "claude-haiku-4-5-20251001")


def _in_ss_roster_current(ticker: str) -> bool:
    """SOURCE GATE for [Signal Strength]: True only if the ticker has an active
    row in ss_roster_current. Read-only. Prevents borrowing the Signal Strength
    name for another product (e.g. Keith's Signal Longs/Shorts)."""
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM ss_roster_current WHERE ticker = %s LIMIT 1", (ticker,))
            return cur.fetchone() is not None
    except Exception as e:
        log.debug("ss_roster_current check failed for %s: %s", ticker, e)
        return False


def _ticker_bucket(ticker: str) -> str | None:
    """Direction bucket from ticker_tags.hedgeye_bucket_0629 (read-only), or None.
    Rendered as a separate 'bucket:' field — never fused into the source label."""
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT hedgeye_bucket_0629 FROM ticker_tags WHERE ticker = %s LIMIT 1", (ticker,))
            r = cur.fetchone()
            return r[0] if r and r[0] else None
    except Exception as e:
        log.debug("ticker_tags bucket lookup failed for %s: %s", ticker, e)
        return None


def _compute_source_label(flags: list[str], rr_trend: str | None = None,
                          ss_member: bool = False) -> str:
    """Deterministic [SourceLabel] prefix derived from source_flags.

    2026-06-10 operator architectural directive: Hedgeye is the north star;
    Risk Range with directional bias is the PRIMARY source label, not the
    last-resort fallback. Cascade reordered:

      [Risk Range Bullish/Bearish/Neutral] — top priority when RR is present
      [Keith's Signals]                    — secondary (side rendered separately)
      [Portfolio Solutions]                — tertiary
      [ETF Pro Short/Long]                 — quaternary
      [Investing Ideas]                    — quinary
      [Quad]                               — fallback
      [Operator]                           — manual override

    The XME 2026-05-26 bug guard still applies — Python strips whatever
    bracket Haiku emits and prepends this one. Returns "" when no flag
    matches (omit prefix entirely).

    rr_trend (optional) is the latest RR.trend string for the ticker. When
    provided AND 'risk_range' is in flags, the label includes the bias.
    Callers in the legacy/long-form decision path can omit it; the notifier
    path (decide_notifier) supplies it so the operator sees Keith's
    direction at a glance.
    """
    fset = set(flags or [])
    # Tier 1 — Hedgeye Risk Range with directional bias. Operator framework:
    # RR is the daily authoritative Hedgeye signal and outranks every other
    # source. The bias suffix tells the operator which side Keith's on
    # before the verb is even read.
    if "risk_range" in fset:
        t = (rr_trend or "").strip().lower()
        if t in ("bullish", "up"):    return "[Risk Range Bullish]"
        if t in ("bearish", "down"):  return "[Risk Range Bearish]"
        return "[Risk Range Neutral]"
    cascade = [
        # Tier 2 — Keith's Signal Longs/Shorts (hedgeye_keiths_signals). A
        # DIFFERENT product from the Signal Strength roster — labeled by its true
        # origin, never "[Signal Strength]". The side is a stored fact rendered
        # SEPARATELY by the caller ("keith's side: ..."), never fused here.
        ("signal_strength_short", "[Keith's Signals]"),
        ("signal_strength_long",  "[Keith's Signals]"),
        # Tier 3 — Portfolio Solutions (Keith holds it = long bias)
        ("portfolio_solutions",   "[Portfolio Solutions]"),
        # Tier 4 — ETF Pro explicit-side (Monday weekly publication)
        ("etf_pro_short",         "[ETF Pro Short]"),
        ("etf_pro_long",          "[ETF Pro Long]"),
        # Tier 5 — Signal Strength roster. SOURCE-GATED: only a genuine
        # ss_roster_current member may carry this label (see ss_member).
        ("signal_strength",       "[Signal Strength]"),
        # Tier 6 — Investing Ideas (Top-21 long leaderboard)
        ("investing_ideas",       "[Investing Ideas]"),
        # Fallback — Quad map (static categorization) / manual override
        ("quad_aligned",          "[Quad]"),
        ("operator",              "[Operator]"),
    ]
    for key, label in cascade:
        if key in fset:
            if key == "signal_strength" and not ss_member:
                continue  # SOURCE GATE — [Signal Strength] needs ss_roster_current membership
            return label
    return ""


# ─────────────────── Trend + Momentum gate (2026-05-26 v3) ───────────────────

def _normalize_mfr_signal(s: str | None) -> str:
    """Reduce MFR signal strings to one of {'bullish','bearish','neutral'}.

    MFR vocabulary observed in mfr_snapshots:
      trend_signal    -> trendBullish / trendBearish / trendNeutral
      momentum_signal -> momentumBullish / momentumBearish / momentumNeutral
                         / momentumNeutralDanger
    `momentumNeutralDanger` is a warning state — momentum is neutral but
    teetering toward bearish. Treat it as neutral for the gate (not a
    positive confirmation).
    """
    if not s:
        return "neutral"
    low = str(s).lower()
    if "bullish" in low:
        return "bullish"
    if "bearish" in low:
        return "bearish"
    return "neutral"


def _ticker_side(source_flags: list[str], rr_trend: str | None = None) -> str:
    """Resolve ticker side from source_flags + RR.trend.

    2026-06-10 operator architectural directive (Hedgeye-first):
    Risk Range bias is PRIMARY. MFR/SG/T1A never set side.

    Priority cascade (first tier with a signal wins):

      Tier 1: Risk Range trend (bullish/bearish) — Keith's daily directional
              bias. AUTHORITATIVE. Overrides everything below.

      Tier 2: Signal Strength explicit-side (signal_strength_long /
              signal_strength_short) — Keith's weekly Best Idea Longs/Shorts.

      Tier 3: Portfolio Solutions (portfolio_solutions) — Keith holds it
              = long bias.

      Tier 4: ETF Pro explicit-side (etf_pro_long / etf_pro_short) — Monday
              weekly publication.

      Tier 5: implicit-long Hedgeye products — signal_strength (daily SS
              Stocks bucket, image-only so we default to long),
              investing_ideas (Top-21 long-only).

      Tier 6: Quad map (quad_long / quad_short) — static categorization
              fallback.

      Else:   'undetermined' — defaults to long behavior downstream.

    Stale flags are already stripped by tools.active_slice.source_flags_for
    before reaching here, so anything we see is fresh.

    Conflict resolution: under the Hedgeye-first cascade RR.trend simply
    overrides whatever lower tiers say — there is no 'conflict' outcome
    for RR-vs-other-source disagreements (RR wins). 'conflict' is still
    returned for the rare both-sides-set-in-the-same-tier case (a ticker
    that somehow ends up in both SS-long and SS-short, etc.).
    """
    fset = set(source_flags or [])

    # Tier 1 — Risk Range bias is the north star.
    t = (rr_trend or "").strip().lower()
    if t in ("bullish", "up"):    return "long"
    if t in ("bearish", "down"):  return "short"

    # Tier 2 — Signal Strength explicit-side
    has_ss_long  = "signal_strength_long"  in fset
    has_ss_short = "signal_strength_short" in fset
    if has_ss_long and has_ss_short: return "conflict"
    if has_ss_long:  return "long"
    if has_ss_short: return "short"

    # Tier 3 — Portfolio Solutions (Keith holds it = long)
    if "portfolio_solutions" in fset: return "long"

    # Tier 4 — ETF Pro explicit-side
    has_etf_long  = "etf_pro_long"  in fset
    has_etf_short = "etf_pro_short" in fset
    if has_etf_long and has_etf_short: return "conflict"
    if has_etf_long:  return "long"
    if has_etf_short: return "short"

    # Tier 5 — implicit-long Hedgeye products
    if fset & {"signal_strength", "investing_ideas"}: return "long"

    # Tier 6 — Quad map (lowest priority, static)
    qlong  = "quad_long"  in fset
    qshort = "quad_short" in fset
    if qlong and qshort: return "conflict"
    if qshort: return "short"
    if qlong:  return "long"

    return "undetermined"


# ─────────────────────────── RR-trend gating (2026-06-10) ────────────
#
# Tickers Keith calls out for regime context but never as standalone
# trades — break above range = "risk-off", that's the alert, no verb.
# Pre-fix the bot was emitting BUY/SELL/SHORT/COVER for these.
INFORMATIONAL_TICKERS: frozenset[str] = frozenset({
    "VIX", "UST2Y", "UST10Y", "UST30Y", "USD",
})


def _is_informational(ticker: str) -> bool:
    return (ticker or "").upper().strip() in INFORMATIONAL_TICKERS


def _informational_phrasing(ticker: str, zone: str,
                            price: float | None,
                            rr_lo: float | None,
                            rr_hi: float | None) -> str:
    """Regime-only sentence for INFORMATIONAL_TICKERS. No verbs, no sizing.

    Maps a zone + ticker to the macro reading Keith wants surfaced:
        VIX above_range  → "risk-off"
        VIX below_range  → "risk-on"
        UST*Y above_range → "yields breaking out — duration pressure"
        UST*Y below_range → "yields breaking down — duration bid"
        USD above_range  → "dollar strength accelerating"
        USD below_range  → "dollar weakness accelerating"
    """
    t = (ticker or "").upper().strip()
    pos = ""
    if price is not None and rr_lo is not None and rr_hi is not None:
        pos = f" at {price} vs RR {rr_lo}-{rr_hi}"
    headline = {
        "VIX":    {"above_range": "risk-off",
                   "below_range": "risk-on",
                   "top_edge":    "vol expanding",
                   "bottom_edge": "vol compressing"},
        "UST2Y":  {"above_range": "front-end yields breaking out",
                   "below_range": "front-end yields breaking down"},
        "UST10Y": {"above_range": "10Y yield breaking out — duration pressure",
                   "below_range": "10Y yield breaking down — duration bid"},
        "UST30Y": {"above_range": "long-bond yield breaking out",
                   "below_range": "long-bond yield breaking down"},
        "USD":    {"above_range": "dollar strength accelerating",
                   "below_range": "dollar weakness accelerating"},
    }.get(t, {}).get(zone)
    if headline:
        return f"{t} {headline}{pos} — regime read, not a trade."
    return f"{t}{pos} — informational, regime read only."


def _rr_framework_alignment(rr_trend_norm: str, zone: str) -> str:
    """Mirror of price_monitor._framework_alignment, lifted into the
    decision_engine path so the counter-trend downgrade can run here too.
    Returns 'aligned' / 'counter' / 'neutral'.
    """
    t = (rr_trend_norm or "").lower()
    if t == "bullish":
        if zone in ("bottom_edge", "above_range"): return "aligned"
        if zone in ("top_edge",    "below_range"): return "counter"
    if t == "bearish":
        if zone in ("top_edge",    "below_range"): return "aligned"
        if zone in ("bottom_edge", "above_range"): return "counter"
    return "neutral"


# Verbs that OPEN or ADD-to risk. Counter-trend downgrade applies to
# these. Exits (SELL/TRIM/COVER) are always allowed regardless of
# alignment — closing a position never needs Keith's blessing.
_ENTRY_VERBS = frozenset({"BUY", "ADD", "SHORT"})


def _apply_rr_trend_guards(
    ticker: str,
    zone: str,
    rr_trend_raw: str | None,
    gated_action: str,
    gated_ctx: str,
) -> tuple[str, str]:
    """Apply the three 2026-06-10 RR-trend guards on top of the existing
    trend+momentum gate result:

      1. INFORMATIONAL ticker → force WATCH with regime phrasing, never
         a trade verb.
      2. RR.trend NULL/neutral → force WATCH ('no Hedgeye trend — no
         trade'), regardless of MFR trend/momentum (kills the NVDA-class
         bug where bullish MFR fired ADD with no RR direction).
      3. RR.trend counter to zone AND action is BUY/ADD/SHORT →
         downgrade to WATCH ('counter to RR trend — informational').
         Exits (SELL/TRIM/COVER) pass through untouched.

    Returns the possibly-downgraded (action, context) pair.
    """
    # 1. INFORMATIONAL class — handled upstream with full price context
    # (need rr_lo/rr_hi/price to render). Here we only enforce the verb
    # block as a defense in depth.
    if _is_informational(ticker):
        return ("WATCH", f"{ticker.upper()} — informational, regime read only")

    rr_norm = _normalize_mfr_signal(rr_trend_raw)

    # 2. No Hedgeye trend, no trade.
    if rr_norm == "neutral":
        return ("WATCH", "no Hedgeye trend — no trade")

    # 3. Counter-trend entry downgrade.
    if gated_action in _ENTRY_VERBS:
        align = _rr_framework_alignment(rr_norm, zone)
        if align == "counter":
            return ("WATCH", "counter to RR trend — informational")

    return (gated_action, gated_ctx)


def _trend_momentum_gate(zone: str, side: str, trend_dir: str,
                         momentum_dir: str) -> tuple[str, str]:
    """Map (zone, side, trend, momentum) to (action_verb, context_phrase).

    Two mirror-image frameworks (2026-05-28 — adds side awareness on top
    of the 2026-05-26 v3 long-side gate):

      side='long' or 'undetermined' (defaults — current bot behavior):
        below_range → SELL  trend breakdown — exit longs
        above_range → BUY   trend continuation — add to longs
        bottom_edge: gate trend+momentum confirms before BUY
            bullish+bullish → BUY    clean mean revert up
            bullish+other   → WATCH  momentum not confirming
            neutral         → WATCH  no trend confirmation
            bearish         → AVOID  don't catch the knife
        top_edge: gate against trimming into strength
            bullish+bullish → HOLD   continuation likely
            other           → SELL   trim (momentum failing or mean revert)

      side='short' (mirror image — for ETF Pro Short / quad_short etc):
        below_range → SHORT  trend continuation — add to short
        above_range → COVER  trend invalidated — close short
        top_edge: gate trend+momentum confirms before SHORT
            bearish+bearish → SHORT  clean short entry
            bearish+other   → WATCH  momentum diverging
            neutral         → WATCH  no trend confirmation
            bullish         → AVOID  shorting into strength
        bottom_edge: gate against covering into weakness
            bearish+bearish → HOLD   short continuation
            other           → COVER  take profit on short

      mid_range / unknown — never reached (upstream short-circuit).
    """
    trend_dir    = (trend_dir or "neutral").lower()
    momentum_dir = (momentum_dir or "neutral").lower()
    side = (side or "long").lower()
    if side not in ("long", "short", "undetermined", "conflict"):
        side = "long"

    # 2026-05-29: conflict sentinel from _ticker_side. Fresh Hedgeye
    # sources disagreed (e.g. SS Long + ETF Pro Short for the same
    # ticker). Don't try to take a position — surface the conflict and
    # let the operator decide.
    if side == "conflict":
        return ("WATCH",
                "side conflict — Hedgeye sources disagree, "
                "wait for resolution")

    # ─── SHORT-side framework ────────────────────────────────────
    if side == "short":
        if zone == "below_range":
            # Marginal-breakdown gate (2026-05-28): when both trend AND
            # momentum point UP, a price slip below range is more likely
            # noise in a low-vol asset than a trend-following SHORT
            # opportunity. Operator's BUXX-style case — bullish trend +
            # bullish momentum + thin range → microscopic "breakdown" is
            # actually a noise tick, treat as COVER (the short thesis is
            # weakening, take profit) rather than ADD-SHORT.
            if trend_dir == "bullish" and momentum_dir == "bullish":
                return ("COVER",
                        "marginal breakdown — trend + momentum bullish, "
                        "short thesis weakening")
            return ("SHORT",
                    "trend continuation — add to short")
        if zone == "above_range":
            # Above range = bad news for shorts. Marginal-breakout gate:
            # if trend AND momentum are STILL bearish, the price slip
            # above range may be noise — WATCH for confirmation rather
            # than force-COVER. Anything else (mixed or bullish
            # indicators) = real break = COVER.
            if trend_dir == "bearish" and momentum_dir == "bearish":
                return ("WATCH",
                        "marginal breakout — trend + momentum still confirm "
                        "short; wait for confirmation")
            return ("COVER",
                    "trend invalidated — short thesis broken, close short")
        if zone == "top_edge":
            if trend_dir == "bearish" and momentum_dir == "bearish":
                return ("SHORT",
                        "top-edge short entry — "
                        "bearish trend + bearish momentum")
            if trend_dir == "bearish":
                return ("WATCH",
                        "top-edge but momentum diverging — "
                        "wait for momentum to confirm")
            if trend_dir == "bullish":
                return ("AVOID",
                        "top-edge in bullish trend — "
                        "don't short into strength")
            return ("WATCH",
                    "top-edge but trend neutral — wait for trend confirmation")
        if zone == "bottom_edge":
            if trend_dir == "bearish" and momentum_dir == "bearish":
                return ("HOLD",
                        "bottom-edge but trend + momentum confirming — "
                        "short continuation, not a cover")
            if trend_dir == "bullish":
                return ("COVER",
                        "bottom-edge in bullish trend — "
                        "short thesis broken, take profit")
            return ("COVER",
                    "bottom-edge — mean revert up, take profit on short")
        return ("WATCH", "")

    # ─── LONG-side framework ──────────────────────────────────────
    if zone == "below_range":
        # Marginal-breakdown gate (2026-05-28): for low-vol assets
        # (BUXX-class — range width <1% of price), a few-cent slip below
        # MFR's tight range is noise, not a real trend breakdown.
        # When trend AND momentum are BOTH bullish, downgrade SELL to
        # WATCH ("trend + momentum supportive — wait for confirmation").
        # Bearish trend OR bearish momentum AT a breakdown = real break,
        # SELL stands. Operator-caught BUXX case (price 20.20 vs MFR
        # 20.255-20.299, 4.4-cent range, both indicators bullish).
        if trend_dir == "bullish" and momentum_dir == "bullish":
            return ("WATCH",
                    "marginal breakdown — trend + momentum supportive, "
                    "wait for confirmation")
        return ("SELL", "trend breakdown — avoid longs, consider shorts")
    if zone == "above_range":
        # Marginal-breakout gate: if trend AND momentum both turned
        # bearish despite price breaking up, the breakout may be a
        # blow-off / noise — WATCH instead of forcing BUY. Anything else
        # (mixed or bullish) = real continuation = BUY.
        if trend_dir == "bearish" and momentum_dir == "bearish":
            return ("WATCH",
                    "marginal breakout — trend + momentum bearish, "
                    "wait for confirmation")
        return ("BUY",  "trend continuation — add to longs")
    if zone == "bottom_edge":
        if trend_dir == "bullish" and momentum_dir == "bullish":
            return ("BUY",
                    "bottom-edge scale-in — bullish trend + positive momentum")
        if trend_dir == "bullish":
            return ("WATCH",
                    "bottom-edge but momentum not confirming — "
                    "wait for momentum to turn")
        if trend_dir == "bearish":
            return ("AVOID",
                    "bottom-edge in bearish trend — likely breakdown, "
                    "don't catch the knife")
        return ("WATCH",
                "bottom-edge but trend neutral — wait for trend confirmation")
    if zone == "top_edge":
        if trend_dir == "bullish" and momentum_dir == "bullish":
            return ("HOLD",
                    "top-edge but trend + momentum supportive — "
                    "continuation likely, not a trim")
        if trend_dir == "bullish":
            return ("SELL", "top-edge trim — momentum failing at resistance")
        if trend_dir == "bearish":
            return ("SELL", "top-edge trim — mean revert with trend confirming")
        return ("SELL", "top-edge trim — mean revert")
    return ("WATCH", "")


_NOTIFIER_PROMPT = """You are a terse trading notifier. The user message
gives you a deterministic `Zone:`, `Ticker side:`, `MFR trend`, and
`MFR momentum` — all computed by Python. Your ACTION is derived
strictly from the (Zone, Side, Trend, Momentum) tuple per the gate
below. The SAME zone produces OPPOSITE actions for a long-side ticker
vs a short-side ticker; the side comes from the ticker's product
flags (etf_pro_short / quad_short → short; etf_pro_long / quad_long
or no side → long).

Keith McCullough's framework (Hedgeye Risk Range), 2026-05-28 side-aware:

═══════════════ LONG-side tickers (default; ETF Pro Long, Quad long) ═══════════════

  OUTSIDE-RANGE
    below_range  → ACTION = SELL  (trend BREAKDOWN — exit longs,
                                   consider shorts; NOT a buy-the-dip)
    above_range  → ACTION = BUY   (trend CONTINUATION — add to longs,
                                   NOT a fade)

  INSIDE-RANGE bottom_edge — buy only when trend AND momentum confirm
    bullish + positive momentum → BUY    (clean mean revert)
    bullish + not-positive      → WATCH  (momentum not confirming)
    neutral trend               → WATCH  (no trend confirmation)
    bearish trend               → AVOID  (broken trend, don't catch knife)

  INSIDE-RANGE top_edge — trim only when momentum is failing
    bullish + positive momentum → HOLD   (continuation likely)
    bullish + not-positive      → SELL   (momentum failing)
    neutral trend               → SELL   (mean revert)
    bearish trend               → SELL   (mean revert + trend)

═══════════════ SHORT-side tickers (ETF Pro Short, Quad short) ═══════════════════

  OUTSIDE-RANGE — mirror of long
    below_range  → ACTION = SHORT  (trend CONTINUATION — add to short,
                                    more downside)
    above_range  → ACTION = COVER  (short thesis INVALIDATED — close short)

  INSIDE-RANGE top_edge — short only when trend AND momentum confirm
    bearish + bearish momentum → SHORT  (clean entry)
    bearish + not-negative     → WATCH  (momentum diverging)
    neutral trend              → WATCH  (no trend confirmation)
    bullish trend              → AVOID  (shorting into strength)

  INSIDE-RANGE bottom_edge — cover only when momentum is failing
    bearish + bearish momentum → HOLD   (short continuation, not a cover)
    everything else            → COVER  (mean revert up; take profit)

  Zone = mid_range / unknown → never sent to you (suppressed upstream)

    Zone = unknown      → ACTION = SKIP

This is the critical framework rule. Breakouts and breakdowns mean the
range has been INVALIDATED — momentum is taking over. Going CONTRARIAN
to a breakout fights Keith's trend-following methodology. Never say
"contrarian add" or "fade strength" on an out-of-range case.

The polling universe is broader than the Quad-aligned slice — it includes
ETF Pro, Signal Strength, Portfolio Solutions, Risk Range and any
operator overrides regardless of Quad. The user message tells you which
Hedgeye source(s) surfaced the ticker via `Source flags`, and whether
the ticker is Quad-aligned via `Quad aligned`. Use that for the
[SourceLabel] prefix and the action context, but do NOT use it to
override the Zone-derived action.

Output EXACTLY one line, this shape:
  ACTION TICKER — price <X> (<RangePosition>), MFR <trend>, <wall info if present> — <one-line action context>

NOTE on the source label: Python rewrites the leading "[SourceLabel] "
on your output — you can omit it, or emit a placeholder like "[X]" and
Python will strip+replace it. Don't waste tokens trying to pick the
right source label from the Source flags; that decision is made
deterministically in Python from the flags list, NOT from the
action/zone pattern. (Verified bug 2026-05-26: Haiku tagged ticker XME
as "[ETF Pro Short]" purely because the prompt's top_edge example used
that label — even though XME's flags didn't include etf_pro_short.)

The <RangePosition> segment is whatever the user message's
`Range position:` line shows — copy it VERBATIM, including any
"⚠️ RR/MFR divergence" suffix. Possible shapes:
  - "37% of RR $260-$273 / 51% of MFR $258-$275"        (both sources)
  - "37% of RR $260-$273 / 65% of MFR $258-$275 ⚠️ RR/MFR divergence"
  - "78% of RR $176-$182"                               (RR only)
  - "14% of MFR $53.13-$55.57"                          (MFR only — AMLP-style)
You will NEVER see this message without a `Range position:` line at an
edge/breakout zone; Python suppresses unknown-zone tickers upstream.

Rules:
  - Use the EXACT `Range position` line from the user message; do NOT
    say "bottom third", "middle third", or "top third", and do NOT
    compute your own percentages.
  - Include the wall fragment (e.g. "call wall $185") only when MFR walls
    are provided; omit the segment otherwise.
  - Do NOT include any dollar amount, bps, or position-sizing recommendation
    anywhere in the output. The operator handles sizing themselves.
  - Approved verbs:
      LONG-side:  BUY / SELL / HOLD / WATCH / AVOID
      SHORT-side: SHORT / COVER / HOLD / WATCH / AVOID
    The Python-computed `Recommended action:` line in the user message
    tells you which verb to use — copy it verbatim. Do not invent
    LONG-side verbs for SHORT-side tickers (or vice versa).
  - The action context phrase should be tight (≤ 14 words) and convey
    exactly WHY the gate produced this verb. Use the phrasing the
    `Recommended action:` line dictates — Python computes it
    deterministically per side+zone+trend+momentum cell.
    When the Range position line carries the divergence flag, append
    ", RR/MFR disagree" to the context.
  - Never use "contrarian", "fade", or "let it run". Breakouts and
    breakdowns are trend signals, not fade setups.

Examples (Side+Zone shown in [brackets] for illustration — your output
never includes that; Python prepends the source label after you respond):
  [long  bottom_edge bullish/positive] BUY NVDA — price 207.50 (5% of RR $206-$236 / 5% of MFR $205.63-$243.49), MFR bullish trend + positive momentum — bottom-edge scale-in — bullish trend + positive momentum
  [long  bottom_edge bullish/negative] WATCH BITCOIN — price 72500 (3% of MFR $72365-$76744), MFR bullish trend but bearish momentum — bottom-edge but momentum not confirming — wait for momentum to turn
  [long  bottom_edge bearish]          AVOID HD — price 305.10 (4% of RR $304-$320), MFR bearish trend — bottom-edge in bearish trend — likely breakdown, don't catch the knife
  [long  top_edge    bullish/positive] HOLD NVDA — price 240.00 (90% of MFR $205.63-$243.49), MFR bullish trend + positive momentum, call wall $245 — top-edge but trend + momentum supportive — continuation likely, not a trim
  [long  top_edge    bullish/negative] SELL HYG — price 80.42 (93% of MFR $79.28-$80.49), MFR bullish trend but neutral-danger momentum, call wall $81 — top-edge trim — momentum failing at resistance
  [long  above_range bullish]          BUY CAD/USD — price 0.7340 (>100% of RR $0.7250-$0.7320), MFR bullish trend — trend continuation — add to longs
  [long  below_range bullish]          SELL TSN — price 62.67 (-73% of MFR $64.44-$66.87), MFR bullish trend but range breakdown — trend breakdown — avoid longs, consider shorts
  [short top_edge    bearish/bearish]  SHORT TLT — price 95.10 (88% of RR $90-$95.80 / 92% of MFR $89-$96), MFR bearish trend + bearish momentum, put wall $90 — top-edge short entry — bearish trend + bearish momentum
  [short top_edge    bearish/neutral]  WATCH XLU — price 45.50 (87% of RR $42-$46), MFR bearish trend but neutral-danger momentum — top-edge but momentum diverging — wait for momentum to confirm
  [short top_edge    bullish]          AVOID INDA — price 52.10 (89% of RR $48-$53), MFR bullish trend — top-edge in bullish trend — don't short into strength
  [short bottom_edge bearish/bearish]  HOLD EWZ — price 27.10 (5% of RR $27-$31), MFR bearish trend + bearish momentum — bottom-edge but trend + momentum confirming — short continuation, not a cover
  [short bottom_edge bearish/neutral]  COVER EWZ — price 27.10 (5% of RR $27-$31), MFR bearish trend but neutral momentum — bottom-edge — mean revert up, take profit on short
  [short above_range bullish]          COVER TLT — price 96.50 (>100% of RR), MFR bullish trend — trend invalidated — short thesis broken, close short
  [short below_range bearish]          SHORT TLT — price 89.20 (<0% of RR), MFR bearish trend — trend continuation — add to short
"""


def decide_notifier(
    ticker: str,
    *,
    signal_origin: str = "proactive_scan",
    signal_conviction: Optional[str] = None,
    account_value_usd: Optional[float] = None,
) -> Optional[dict]:
    """Cheap Haiku-4.5 notifier (rollback architecture, 2026-05-24).

    Single API call. ~10-line prompt. 1-line output. No prompt caching, no
    SG context, no doctrine block — the active-slice resolver already encoded
    Quad alignment by selecting this ticker in the first place.

    Returns a decision dict that satisfies the same contract as legacy
    `decide()` — proactive_scanner._persist_recommendation,
    _was_recently_alerted, and _format_alert only read these keys:
      conviction, direction, action, bps, recommended_dollars,
      confidence, reasoning, evidence, context_summary, ticker,
      signal_origin, signal_conviction, account_value_usd, decided_at.

    None on fatal error (missing API key, network).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set; cannot run notifier")
        return None
    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed")
        return None

    # Pull the minimal context the notifier needs.
    rr  = _get_risk_range(ticker)  or {}     # staleness-gated: stale → levels blanked
    ep  = _get_etf_pro_range(ticker) or {}
    mfr = _get_mfr_latest(ticker)  or {}
    yh  = _get_yahoo_latest(ticker) or {}

    price = mfr.get("price") or yh.get("price")
    # rr_lo/rr_hi are None when the RR row is stale (>RR_MAX_AGE_DAYS td) —
    # _get_risk_range blanked them. So every rr_lo/rr_hi consumer below (zone,
    # pct, divergence) sees a stale range as absent by construction.
    rr_lo = rr.get("buy_trade")
    rr_hi = rr.get("sell_trade")
    mfr_lo = mfr.get("range_low")
    mfr_hi = mfr.get("range_high")

    # Precedence for the range we PRESENT + run zone/percent against:
    #   fresh RR  >  fresh ETF Pro levels  >  MFR-only (no RR/ETF-Pro clause).
    # (operator directive 2026-07-06). ETF Pro carries current product levels
    # (weekly Monday cadence) for the ~18 etfpro names; when the last RR is
    # stale but ETF Pro is fresh we surface "ETF Pro levels $150-$157" instead
    # of a stale range. ETF Pro drives terrain (zone/percent) only — it does
    # NOT supply a Keith trend, so a stale-RR name still hits the RR-trend
    # guard below and stays WATCH (a stale range can never trigger a trade).
    import db_pg as _db_pg
    _ep_age, _ep_stale = _db_pg.risk_range_age(ep.get("week_of")) if ep else (None, True)
    ep_fresh = bool(ep) and ep.get("range_low") is not None and not _ep_stale
    if rr_lo is not None and rr_hi is not None:
        prange_lo, prange_hi, prange_src = rr_lo, rr_hi, "RR"
    elif ep_fresh:
        prange_lo, prange_hi, prange_src = (
            ep.get("range_low"), ep.get("range_high"), "ETF Pro")
    else:
        prange_lo, prange_hi, prange_src = None, None, None

    # Effective directional authority — the Hedgeye trend that drives the gate,
    # side and guards. Mirrors the levels precedence: a fresh RR uses Keith's
    # RR trend; when the RR is stale/absent but ETF Pro Plus is fresh, ETF Pro
    # Plus IS the Hedgeye directional call for that name (its bias is an
    # explicit long/short trade rec), so its bias drives the alert — an ETF Pro
    # name at an edge fires a real BUY/SELL, not a WATCH. MFR alone still never
    # supplies direction (the NVDA-class guard). long→BULLISH / short→BEARISH.
    _ep_bias = (ep.get("bias") or "").strip().lower() if ep else ""
    ep_trend = {"long": "BULLISH", "short": "BEARISH"}.get(_ep_bias)
    if prange_src == "RR":
        eff_trend = rr.get("trend")
    elif prange_src == "ETF Pro":
        eff_trend = ep_trend
    else:
        eff_trend = None

    # Dated breadcrumb for a stale RR — informational only, never a live range
    # or a percentage. Appended to the range-position line / mid_range reason
    # so the operator can see the last RR without it decorating the alert as
    # current.
    stale_rr_note = ""
    if rr.get("stale") and rr.get("stale_buy_trade") is not None:
        _sd = rr.get("signal_date")
        _sd_str = f"{_sd.month}/{_sd.day}" if _sd else "?"
        try:
            _slo = f"{float(rr['stale_buy_trade']):.2f}"
            _shi = f"{float(rr['stale_sell_trade']):.2f}"
        except (TypeError, ValueError):
            _slo, _shi = rr['stale_buy_trade'], rr['stale_sell_trade']
        stale_rr_note = (f" · last RR {_sd_str}: ${_slo}-${_shi} (stale)")
    trend  = mfr.get("trend_signal")
    momentum = mfr.get("momentum_signal")
    hurst  = mfr.get("hurst")
    # MFR gamma walls (migration 028). Populated on ~55% of rows — US
    # equities with listed options. None on commodities/FX/VIX/thin tickers.
    call_wall  = mfr.get("call_wall_mfr")
    put_wall   = mfr.get("put_wall_mfr")
    zero_gamma = mfr.get("zero_gamma")

    # Deterministic zone — the FIRING GATE under edge-only triggers
    # (2026-05-25). Computed against the Hedgeye Risk Range, not MFR,
    # because the operator trades against RR and asked alerts to fire
    # only at the bottom/top edges of THAT range. Falls back to MFR if
    # RR isn't published for the ticker (rare; happens on the first day
    # a ticker appears in the inventory). `compute_zone` reads the
    # RR_EDGE_PERCENT env var to decide the edge band width.
    # Computed against the primary range (fresh RR, else fresh ETF Pro), with
    # MFR as the final fallback. A stale RR never reaches here — prange_* is
    # already resolved to ETF Pro / None in that case.
    zone = "unknown"
    try:
        from price_monitor import compute_zone
        if price is not None and prange_lo is not None and prange_hi is not None:
            zone = compute_zone(float(price), float(prange_lo), float(prange_hi))
        elif price is not None and mfr_lo is not None and mfr_hi is not None:
            zone = compute_zone(float(price), float(mfr_lo), float(mfr_hi))
    except Exception:
        pass

    # Dual range-position percentages: one against the Hedgeye Risk Range
    # and one against the MFR range. Side-by-side surfacing in the alert
    # body so the operator can spot disagreement between the two sources
    # (2026-05-26 retune — AMLP bug: RR has no rows for AMLP ever, but
    # MFR has clean bounds; the original "RR-only %" path emitted
    # "unable to calculate % without RR bounds" and Haiku improvised).
    #
    # `range_position_pct` is kept as the legacy primary % field for
    # backward-compat downstream — primary range if present, MFR fallback.
    #
    # pct_primary is the % against the primary presentation range (fresh RR,
    # else fresh ETF Pro). pct_rr retains its STRICT meaning — the % against a
    # FRESH Hedgeye Risk Range only — so a stale range can never populate a
    # "% of RR" number anywhere downstream (HARD RULE). When the primary range
    # is ETF Pro, pct_rr stays None and the presentation labels it "ETF Pro".
    pct_primary: Optional[float] = None
    pct_mfr: Optional[float] = None
    try:
        if (price is not None and prange_lo is not None and prange_hi is not None
                and float(prange_hi) > float(prange_lo)):
            pct_primary = ((float(price) - float(prange_lo))
                           / (float(prange_hi) - float(prange_lo))) * 100.0
    except Exception:
        pct_primary = None
    try:
        if (price is not None and mfr_lo is not None and mfr_hi is not None
                and float(mfr_hi) > float(mfr_lo)):
            pct_mfr = ((float(price) - float(mfr_lo))
                       / (float(mfr_hi) - float(mfr_lo))) * 100.0
    except Exception:
        pct_mfr = None
    pct_rr: Optional[float] = pct_primary if prange_src == "RR" else None
    range_position_pct: Optional[float] = (
        pct_primary if pct_primary is not None else pct_mfr)

    # Divergence: >20pp gap between RR-% and MFR-% means the two sources
    # disagree on where the price sits in the range. GATED ON RR FRESHNESS
    # (2026-07-06): only fires off a FRESH Risk Range (prange_src == "RR").
    # "Staleness is not divergence" — a stale RR that drifted from MFR must
    # NOT raise the ⚠️ flag; that was the false-signal the operator flagged.
    divergence = (prange_src == "RR"
                  and pct_rr is not None and pct_mfr is not None
                  and abs(pct_rr - pct_mfr) > 20.0)

    # Optional wall line — only emit when at least one wall value exists.
    # Keeps the prompt clean for tickers without options coverage.
    wall_line = ""
    wall_bits = []
    if call_wall  is not None: wall_bits.append(f"call=${call_wall}")
    if put_wall   is not None: wall_bits.append(f"put=${put_wall}")
    if zero_gamma is not None: wall_bits.append(f"zero-gamma=${zero_gamma}")
    if wall_bits:
        wall_line = "MFR walls: " + " / ".join(wall_bits) + "\n"

    # Side-by-side range position line. Examples:
    #   Range position: 37% of RR $260-$273 / 51% of MFR $258-$275
    #   Range position: 14% of MFR $53.13-$55.57          (RR missing — AMLP case)
    #   Range position: 78% of RR $176-$182                (MFR missing)
    #   Range position: 37% of RR $260-$273 / 65% of MFR $258-$275 ⚠️ RR/MFR divergence
    #   (omitted entirely when neither source has bounds — caller will then
    #    hit the mid_range/unknown short-circuit and suppress the alert)
    range_pos_parts: list[str] = []
    if pct_primary is not None:
        range_pos_parts.append(
            f"{pct_primary:.0f}% of {prange_src} ${prange_lo}-${prange_hi}")
    if pct_mfr is not None:
        range_pos_parts.append(f"{pct_mfr:.0f}% of MFR ${mfr_lo}-${mfr_hi}")
    range_pos_line = ""
    if range_pos_parts:
        divergence_tag = " ⚠️ RR/MFR divergence" if divergence else ""
        range_pos_line = ("Range position: " + " / ".join(range_pos_parts)
                          + divergence_tag + stale_rr_note + "\n")

    # Source flags — which Hedgeye product surfaced this ticker. Surfaced
    # into the prompt as context (which source-prefix to emit, whether the
    # ticker is Quad-aligned). Best-effort; an empty list just suppresses
    # the [SourceLabel] prefix in the output line.
    source_flags: list[str] = []
    try:
        from tools.active_slice import source_flags_for
        source_flags = source_flags_for(ticker)
    except Exception as e:
        log.debug("notifier: source_flags lookup failed for %s: %s", ticker, e)
    quad_aligned = "quad_aligned" in source_flags
    source_flags_line = (
        f"Source flags: {', '.join(source_flags) if source_flags else '(none)'}\n"
        f"Quad aligned: {quad_aligned}\n"
    )

    # Cost short-circuit: under the edge-only firing policy (2026-05-25),
    # mid_range and unknown tickers never produce a tradable alert. Skip
    # the Haiku API call entirely for these — saves the bulk of the
    # per-cycle token spend since ~70% of the polling universe sits in
    # mid_range at any moment. Returns the same decision shape with
    # action='WATCH', conviction='Monitor' so the scanner's dedup +
    # persistence pipeline still sees a uniform record.
    if zone in ("mid_range", "unknown"):
        if account_value_usd is None:
            try:
                from portfolio import account_value, hedgeye_target_account
                acct = hedgeye_target_account("Long")
                account_value_usd = float(account_value(acct) or 0)
            except Exception:
                account_value_usd = 50_000.0
        if range_pos_parts:
            reason = ("mid_range — " + " / ".join(range_pos_parts)
                      + stale_rr_note + "; no edge, no Haiku call")
        else:
            reason = (f"zone={zone}; no RR or MFR bounds available, "
                      "alert suppressed (no Haiku call)")
        return {
            "ticker":            ticker.upper(),
            "signal_origin":     signal_origin,
            "signal_conviction": signal_conviction,
            "account_value_usd": account_value_usd,
            "conviction":        "Monitor",
            "direction":         "long",
            "action":            "WATCH",
            "bps":               None,
            "recommended_dollars": None,
            "confidence":        0.0,
            "reasoning":         f"WATCH {ticker.upper()} — {reason}",
            "evidence":          [],
            "decided_at":        datetime.utcnow().isoformat() + "Z",
            "source_flags":      source_flags,
            "quad_aligned":      quad_aligned,
            # Resolve side for short-circuited tickers too — purely for
            # diagnostic consistency; the zone is mid_range/unknown so
            # no action will fire either way. Pass RR.trend (north-star
            # input per 2026-06-10 architectural directive).
            "side":              _ticker_side(
                source_flags,
                rr_trend=(rr.get("trend") if isinstance(rr, dict) else None),
            ),
            "pct_rr":            pct_rr,
            "pct_mfr":           pct_mfr,
            "rr_mfr_divergence": divergence,
            "context_summary": {
                "hedgeye_quad":     None,
                "vix_bucket":       None,
                "risk_range_low":   rr_lo,
                "risk_range_high":  rr_hi,
                "mfr_range_low":    mfr_lo,
                "mfr_range_high":   mfr_hi,
                "mfr_hurst":        hurst,
                "yahoo_price":      price,
                "mfr_zone":         zone,
                "range_position_pct": range_position_pct,
                "pct_rr":           pct_rr,
                "pct_mfr":          pct_mfr,
                "rr_mfr_divergence": divergence,
                "mfr_call_wall":    call_wall,
                "mfr_put_wall":     put_wall,
                "mfr_zero_gamma":   zero_gamma,
                "spotgamma_call_wall": None,
                "spotgamma_put_wall":  None,
                "source_flags":     source_flags,
                "quad_aligned":     quad_aligned,
            },
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens":     0,
            "input_tokens":                0,
            "output_tokens":               0,
            "prompt_context_full": {
                "model":      NOTIFIER_MODEL,
                "system":     "[skipped — mid_range/unknown short-circuit]",
                "user":       "[skipped — mid_range/unknown short-circuit]",
                "raw_output": reason,
                "decided_at": datetime.utcnow().isoformat() + "Z",
            },
        }

    # INFORMATIONAL ticker short-circuit (2026-06-10). VIX / UST*Y / USD
    # are regime reads, never trades — emit deterministic regime phrasing
    # and skip the Haiku call entirely. Same shape as the mid_range
    # short-circuit so scanner dedup + persistence stay uniform.
    if _is_informational(ticker):
        if account_value_usd is None:
            try:
                from portfolio import account_value, hedgeye_target_account
                acct = hedgeye_target_account("Long")
                account_value_usd = float(account_value(acct) or 0)
            except Exception:
                account_value_usd = 50_000.0
        regime = _informational_phrasing(ticker, zone, price, rr_lo, rr_hi)
        return {
            "ticker":            ticker.upper(),
            "signal_origin":     signal_origin,
            "signal_conviction": signal_conviction,
            "account_value_usd": account_value_usd,
            "conviction":        "Monitor",
            "direction":         "long",
            "action":            "WATCH",
            "bps":               None,
            "recommended_dollars": None,
            "confidence":        0.0,
            "reasoning":         f"WATCH {ticker.upper()} — {regime}",
            "evidence":          [],
            "decided_at":        datetime.utcnow().isoformat() + "Z",
            "source_flags":      source_flags,
            "quad_aligned":      quad_aligned,
            "side":              "informational",
            "pct_rr":            pct_rr,
            "pct_mfr":           pct_mfr,
            "rr_mfr_divergence": divergence,
            "context_summary": {
                "hedgeye_quad":     None,
                "vix_bucket":       None,
                "risk_range_low":   rr_lo,
                "risk_range_high":  rr_hi,
                "mfr_range_low":    mfr_lo,
                "mfr_range_high":   mfr_hi,
                "mfr_hurst":        hurst,
                "yahoo_price":      price,
                "mfr_zone":         zone,
                "range_position_pct": range_position_pct,
                "pct_rr":           pct_rr,
                "pct_mfr":          pct_mfr,
                "rr_mfr_divergence": divergence,
                "mfr_call_wall":    call_wall,
                "mfr_put_wall":     put_wall,
                "mfr_zero_gamma":   zero_gamma,
                "spotgamma_call_wall": None,
                "spotgamma_put_wall":  None,
                "source_flags":     source_flags,
                "quad_aligned":     quad_aligned,
                "informational":    True,
            },
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens":     0,
            "input_tokens":                0,
            "output_tokens":               0,
            "prompt_context_full": {
                "model":      NOTIFIER_MODEL,
                "system":     "[skipped — informational ticker]",
                "user":       "[skipped — informational ticker]",
                "raw_output": regime,
                "decided_at": datetime.utcnow().isoformat() + "Z",
            },
        }

    # Normalize trend + momentum + side for the gate. Python computes the
    # (zone, side, trend, momentum) → action mapping deterministically;
    # Haiku's job is only the narrative wording. Side comes primarily from
    # RR.trend (operator architectural directive 2026-06-10: Hedgeye is
    # north star — RR direction overrides every Hedgeye-product flag).
    trend_dir    = _normalize_mfr_signal(trend)
    momentum_dir = _normalize_mfr_signal(momentum)
    rr_trend_val = (rr.get("trend") if isinstance(rr, dict) else None)
    # ETF Pro Plus names (RR stale/absent, ETF Pro fresh): the ETF Pro bias is
    # the Hedgeye trend authority, so it drives the gate's trend slot in place
    # of MFR trend — an ETF Pro Long at its bottom edge with confirming MFR
    # momentum fires BUY. Fresh-RR names keep the existing MFR-trend gate +
    # RR-trend veto (untouched). `eff_trend` also feeds side + the guards below.
    gate_trend_dir = (_normalize_mfr_signal(eff_trend)
                      if prange_src == "ETF Pro" else trend_dir)
    side         = _ticker_side(source_flags, rr_trend=eff_trend)
    gated_action, gated_ctx_initial = _trend_momentum_gate(
        zone, side, gate_trend_dir, momentum_dir
    )

    # 2026-06-10 — RR-trend guards (operator architectural directive):
    #   (a) INFORMATIONAL_TICKERS (VIX/UST*/USD): never emit trade verbs.
    #   (b) RR.trend NULL or neutral: force WATCH ('no Hedgeye trend —
    #       no trade'). Kills the ADD-NVDA-at-205 bug — pre-fix, an MFR
    #       bullish trend overrode an absent RR direction and the bot
    #       fired ADD on a ticker Keith had no opinion on.
    #   (c) counter-trend BUY/ADD/SHORT: downgrade to WATCH. Exits
    #       (SELL/TRIM/COVER) always allowed.
    # Guard on the effective authority (RR trend, else ETF Pro Plus bias) so an
    # ETF Pro name is NOT force-WATCHed by guard (b) for lacking an RR trend —
    # ETF Pro Plus supplies the direction. MFR-only names still have eff_trend
    # None → guard (b) forces WATCH (the NVDA-class protection is intact).
    gated_action, gated_ctx_initial = _apply_rr_trend_guards(
        ticker, zone, eff_trend, gated_action, gated_ctx_initial
    )

    # SG framing back into the prompt 2026-06-10 — DETERMINISTIC ONLY.
    # ONE pre-computed line from _spotgamma_framing_line, fed by sg_levels
    # (migration 034). NEVER raw JSON, NEVER prose. Hierarchy: SG refines
    # terrain (entry/level/structure); Hedgeye RR trend decides direction.
    # SG NEVER overrides a Keith trend.
    sg_line = ""
    try:
        import db_pg
        sg_row = db_pg.get_latest_sg_levels(ticker, max_age_hours=24)
        if sg_row:
            sg_dict = {
                "call_wall":        sg_row.get("call_wall"),
                "put_wall":         sg_row.get("put_wall"),
                "key_gamma_strike": sg_row.get("key_gamma_strike"),
                "hedge_wall":       sg_row.get("hedge_wall"),
                "gamma_flip":       sg_row.get("gamma_flip"),
            }
            line = _spotgamma_framing_line(sg_dict, price)
            if line:
                sg_line = line.splitlines()[0] + "\n"
    except Exception as e:
        log.debug("notifier: sg_levels read failed for %s (%s)", ticker, e)

    # Range line fed to Haiku. A stale RR is never presented as a live range
    # here — rr_lo/rr_hi are already None in that case; prange_src names what
    # the numbers actually are (RR / ETF Pro), and the stale RR is only echoed
    # as an explicitly-dated breadcrumb so Haiku cannot mistake it for current.
    if prange_src == "RR":
        _rng_line = f"Hedgeye Risk Range: low={prange_lo}, high={prange_hi}\n"
    elif prange_src == "ETF Pro":
        _rng_line = (
            "Hedgeye Risk Range: (stale or absent — not used)\n"
            f"ETF Pro levels: low={prange_lo}, high={prange_hi}\n")
    else:
        _rng_line = "Hedgeye Risk Range: (none — no fresh levels)\n"
    if stale_rr_note:
        _rng_line += "Note:" + stale_rr_note.lstrip(" ·") + "\n"

    user_msg = (
        f"Ticker: {ticker.upper()}\n"
        f"Price: {price}\n"
        + _rng_line
        + range_pos_line
        + f"Zone: {zone}\n"
        + f"Ticker side: {side}\n"
        + f"MFR: range={mfr_lo}-{mfr_hi}, hurst={hurst}\n"
        + f"MFR trend: {trend_dir}\n"
        + f"MFR momentum: {momentum_dir}\n"
        + wall_line
        + sg_line
        + source_flags_line
        + f"Recommended action: {gated_action} — {gated_ctx_initial}\n"
        + f"Signal origin: {signal_origin}"
        + (f"\nHedgeye-tagged conviction: {signal_conviction}"
           if signal_conviction else "")
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=NOTIFIER_MODEL,
            max_tokens=120,
            system=_NOTIFIER_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        line = "".join(getattr(b, "text", "") for b in resp.content
                       if getattr(b, "type", None) == "text").strip().splitlines()[0]
        # llm_calls ledger row (mig 033) — fire-and-forget cost stamp.
        u = getattr(resp, "usage", None)
        _log_llm_call(
            model=NOTIFIER_MODEL, caller="decide_notifier",
            ticker=ticker.upper(),
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            notes=f"signal_origin={signal_origin} zone={zone}",
        )
    except Exception as e:
        log.error("notifier API call failed for %s: %s", ticker, e)
        _log_llm_call(
            model=NOTIFIER_MODEL, caller="decide_notifier",
            ticker=ticker.upper(),
            notes=f"api-failure: {e}",
        )
        return None

    # Parse the leading verb. Accept long-side (BUY/SELL/HOLD/WATCH/AVOID)
    # AND short-side (SHORT/COVER/HOLD/WATCH/AVOID). Side-aware verbs
    # added 2026-05-28: SHORT (initiate/add short) + COVER (close short).
    # Legacy alias TRIM maps to SELL; SKIP maps to WATCH for older Haiku
    # replies.
    payload = (line or "").lstrip()
    if payload.startswith("["):
        rb = payload.find("]")
        if rb != -1:
            payload = payload[rb + 1:].lstrip()
    head = (payload.split(None, 1)[0] if payload else "").upper()
    action_map = {"BUY":   "BUY",
                  "SELL":  "SELL",
                  "TRIM":  "SELL",     # legacy alias
                  "SHORT": "SHORT",    # short-side: initiate/add
                  "COVER": "COVER",    # short-side: close
                  "HOLD":  "HOLD",
                  "WATCH": "WATCH",
                  "AVOID": "AVOID",
                  "SKIP":  "WATCH"}
    action = action_map.get(head)
    if action is None:
        log.warning("notifier returned non-conforming line for %s: %s", ticker, line)
        action = "WATCH"

    # Safety net: enforce the deterministic (zone, side, trend, momentum)
    # → action mapping in Python. The expected verb was already computed
    # upstream (gated_action) and surfaced into the user message via
    # `Recommended action:` — Haiku is supposed to copy it. If it
    # didn't (e.g. emitted a long-side verb on a short ticker), Python
    # overrides + regenerates the body deterministically. Haiku's
    # original output stays in prompt_context_full for audit.
    expected_action = gated_action
    gated_ctx = gated_ctx_initial
    if action != expected_action:
        log.info("notifier: gate(zone=%s, side=%s, trend=%s, momentum=%s) "
                 "expected action=%s but Haiku said %s for %s; forcing %s "
                 "and regenerating body",
                 zone, side, trend_dir, momentum_dir, expected_action,
                 action, ticker, expected_action)
        # Regenerate the body deterministically from Python state. Haiku's
        # original output is preserved in prompt_context_full for audit.
        range_str = " / ".join(range_pos_parts) if range_pos_parts else ""
        if divergence:
            range_str += " ⚠️ RR/MFR divergence"

        # Trend+momentum narrative tag: "bullish trend + positive momentum"
        # vs "bullish trend but bearish momentum" etc. Plain-English so the
        # alert body reads naturally.
        if trend_dir == momentum_dir:
            tm_str = f"MFR {trend_dir} trend + {momentum_dir} momentum"
        elif trend_dir == "neutral" or momentum_dir == "neutral":
            tm_str = (f"MFR {trend_dir} trend, "
                      f"{momentum_dir} momentum")
        else:
            tm_str = (f"MFR {trend_dir} trend but "
                      f"{momentum_dir} momentum")

        wall_str = wall_line.replace("MFR walls: ", "").strip()

        bits = [f"{expected_action} {ticker.upper()} — price {price}"]
        if range_str:
            bits[-1] += f" ({range_str})"
        bits.append(tm_str)
        if wall_str:
            bits.append(wall_str)
        if gated_ctx:
            bits.append(gated_ctx)
        # Join with ", " for the middle clauses and " — " before the
        # trailing action context.
        if len(bits) > 1:
            payload = ", ".join(bits[:-1]) + " — " + bits[-1]
        else:
            payload = bits[0]
        action = expected_action

    # Deterministic source label (2026-05-26 fix for the XME bug). Haiku
    # was confabulating the [SourceLabel] off the action/zone pattern
    # instead of off the actual source_flags list — XME got tagged
    # "[ETF Pro Short]" purely because the prompt's top_edge example
    # happened to use it, even though XME has zero ETF Pro data. Strip
    # whatever bracket Haiku emitted and prepend the Python-computed one.
    # 2026-06-10: pass rr_trend so [Risk Range Bullish/Bearish/Neutral]
    # surfaces Keith's direction in the label, not just the source.
    _fset = set(source_flags or [])
    _ss_member = _in_ss_roster_current(ticker) if "signal_strength" in _fset else False
    deterministic_label = _compute_source_label(
        source_flags, rr_trend=rr_trend_val, ss_member=_ss_member,
    )
    _base = f"{deterministic_label} {payload}" if deterministic_label else payload
    # Direction/side rendered as SEPARATE fields — never fused into the source
    # label. "keith's side" is a stored fact from hedgeye_keiths_signals; "bucket"
    # is from ticker_tags. Each omitted when absent.
    _fields = []
    if "signal_strength_short" in _fset:
        _fields.append("keith's side: short")
    elif "signal_strength_long" in _fset:
        _fields.append("keith's side: long")
    _bucket = _ticker_bucket(ticker)
    if _bucket:
        _fields.append(f"bucket: {_bucket}")
    final_line = _base + (("  (" + " · ".join(_fields) + ")") if _fields else "")
    if final_line != line.lstrip():
        log.info("notifier: replaced Haiku label with deterministic '%s' for "
                 "%s (flags=%s)", deterministic_label or "(none)",
                 ticker, source_flags)

    # Resolve account value for downstream sizing math.
    if account_value_usd is None:
        try:
            from portfolio import account_value, hedgeye_target_account
            acct = hedgeye_target_account("Long")
            account_value_usd = float(account_value(acct) or 0)
        except Exception:
            account_value_usd = 50_000.0

    # Sizing: BUY/SELL/SHORT/COVER all get the 50 bps notifier adder
    # (operator handles position-sizing promotion to starters via reply).
    # HOLD/WATCH/AVOID are informational only.
    bps: Optional[int] = 50 if action in ("BUY", "SELL", "SHORT", "COVER") else None
    dollars: Optional[float] = None
    if bps:
        try:
            from recommender import size_from_bps
            dollars = size_from_bps(bps, account_value_usd)
        except Exception:
            dollars = None

    # Conviction: any action at an edge or breakout zone is informational
    # enough to reach the operator's phone. mid_range / unknown were
    # already short-circuited upstream with conviction=Monitor; we never
    # reach here for those.
    #
    # Direction reflects the trade direction of the recommendation:
    #   BUY   / COVER → long  (buying)
    #   SELL  / SHORT → short (selling)
    #   HOLD          → tracks the ticker's natural side
    #   WATCH / AVOID → tracks side (long bias for undetermined)
    if action in ("BUY", "COVER"):
        _direction = "long"
    elif action in ("SELL", "SHORT"):
        _direction = "short"
    else:
        _direction = "short" if side == "short" else "long"
    decision: dict = {
        "ticker":            ticker.upper(),
        "signal_origin":     signal_origin,
        "signal_conviction": signal_conviction,
        "account_value_usd": account_value_usd,
        "conviction":        "Adding",
        "direction":         _direction,
        "side":              side,
        "action":            action,
        "bps":               bps,
        "recommended_dollars": dollars,
        "confidence":        0.5,     # not used in actionability gate; placeholder
        "reasoning":         final_line,
        "evidence":          [],
        "decided_at":        datetime.utcnow().isoformat() + "Z",
        # Polling-universe source membership (2026-05-25). Top-level mirror
        # of context_summary.source_flags / quad_aligned for downstream
        # consumers (alert formatter, persister, future approval UI).
        "source_flags":      source_flags,
        "quad_aligned":      quad_aligned,
        # Dual-range percentages + divergence flag (2026-05-26).
        "pct_rr":            pct_rr,
        "pct_mfr":           pct_mfr,
        "rr_mfr_divergence": divergence,
        "context_summary": {
            "hedgeye_quad":    None,   # quad info is now metadata, not in prompt
            "vix_bucket":      None,
            "risk_range_low":  rr_lo,
            "risk_range_high": rr_hi,
            "mfr_range_low":   mfr_lo,
            "mfr_range_high":  mfr_hi,
            "mfr_hurst":       hurst,
            "yahoo_price":     price,
            "mfr_zone":        zone,
            "range_position_pct": range_position_pct,
            # Dual-range percentages (2026-05-26). pct_rr is the % against
            # the Hedgeye Risk Range; pct_mfr against the MFR range. Either
            # can be None when the source has no published bounds.
            # `rr_mfr_divergence` is True when both exist and disagree by
            # >20pp — a useful operator-side audit signal.
            "pct_rr":          pct_rr,
            "pct_mfr":         pct_mfr,
            "rr_mfr_divergence": divergence,
            # MFR gamma walls (migration 028) — None on tickers w/o options.
            "mfr_call_wall":   call_wall,
            "mfr_put_wall":    put_wall,
            "mfr_zero_gamma":  zero_gamma,
            # SG keys preserved for downstream template compatibility
            "spotgamma_call_wall": None,
            "spotgamma_put_wall":  None,
            # Polling-universe source membership (2026-05-25 expansion).
            # Empty list means the ticker isn't currently in any tracked
            # Hedgeye-product universe (operator override or explicit CLI list).
            "source_flags":   source_flags,
            "quad_aligned":   quad_aligned,
        },
        # Prompt-cache telemetry kept at zero — notifier doesn't cache.
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens":     0,
        "input_tokens":                0,
        "output_tokens":               0,
        "prompt_context_full": {
            "model":      NOTIFIER_MODEL,
            "system":     _NOTIFIER_PROMPT,
            "user":       user_msg,
            # raw_output is Haiku's verbatim response (preserved for
            # ML training); reasoning above is the post-processed line
            # with the deterministic [SourceLabel] applied.
            "raw_output": line,
            "decided_at": datetime.utcnow().isoformat() + "Z",
        },
    }
    return decision


def decide(
    ticker: str,
    *,
    signal_origin: str = "proactive_scan",
    signal_conviction: Optional[str] = None,
    account_value_usd: Optional[float] = None,
) -> Optional[dict]:
    """Notifier-mode entry point (rollback architecture, 2026-05-24).

    Delegates to `decide_notifier`. The heavyweight legacy path
    (`_decide_legacy` below — Sonnet 4.5, 3-block cached prompt, full
    doctrine + SG + corpus context) is preserved unreachable for future
    restore."""
    return decide_notifier(
        ticker,
        signal_origin=signal_origin,
        signal_conviction=signal_conviction,
        account_value_usd=account_value_usd,
    )


def _decide_legacy(
    ticker: str,
    *,
    signal_origin: str = "proactive_scan",
    signal_conviction: Optional[str] = None,
    account_value_usd: Optional[float] = None,
) -> Optional[dict]:
    """Legacy heavyweight decision engine — Sonnet 4.5, 3-block cached prompt,
    full doctrine + SG (now stubbed) + corpus context. Preserved for restore.
    NOT called in the live path as of 2026-05-24."""
    # Diagnostic: log env state at function entry, before any other imports.
    _early_key = os.environ.get("ANTHROPIC_API_KEY", "")
    log.info(
        "decide() entry: ANTHROPIC_API_KEY present=%s length=%d",
        bool(_early_key), len(_early_key),
    )
    if account_value_usd is None:
        try:
            from portfolio import account_value, hedgeye_target_account
            acct = hedgeye_target_account("Long")
            account_value_usd = float(account_value(acct) or 0)
        except Exception as e:
            log.warning("decision_engine: could not resolve account value: %s", e)
            account_value_usd = 50_000.0  # safe fallback for sizing math

    ctx = gather_context(ticker, signal_conviction=signal_conviction)
    per_ticker_static, dynamic_block = _format_user_message(
        ctx, signal_origin=signal_origin,
        signal_conviction=signal_conviction,
        account_value=account_value_usd,
    )

    try:
        import anthropic
    except ImportError:
        log.error("anthropic package not installed; cannot run decision_engine")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set; cannot run decision_engine")
        return None

    # Prompt caching: the static framework block (canon + doctrine
    # summary) is identical on every call, so we mark it cache_control
    # ephemeral and send it as the FIRST content block. The dynamic
    # per-ticker context follows uncached. After the first call this
    # cuts input-token cost on the static prefix by ~90% (~40% total).
    static_block = _static_framework_block()
    cache_usage = {"cache_creation_input_tokens": 0,
                   "cache_read_input_tokens": 0,
                   "input_tokens": 0, "output_tokens": 0}
    try:
        client = anthropic.Anthropic(api_key=api_key)
        # 3-block content array:
        #  1) static framework  — cache_control ephemeral (5-min default TTL)
        #  2) per-ticker static  — cache_control ephemeral, 1h extended TTL
        #     (SG/MFR/RR/macro — same per ticker all day; only price +
        #     recent activity change). Needs the extended-cache-ttl beta.
        #  3) dynamic per-call   — uncached (live price, recent alerts, ts)
        content_blocks = []
        if static_block:
            # 1h TTL: framework canon + doctrine are the most static of
            # all — and a longer-TTL block must not follow a shorter-TTL
            # one (API processes blocks in order), so this leads.
            content_blocks.append({
                "type": "text",
                "text": static_block,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            })
        if per_ticker_static:
            content_blocks.append({
                "type": "text",
                "text": per_ticker_static,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            })
        content_blocks.append({"type": "text", "text": dynamic_block})
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content_blocks}],
            extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
        )
        raw_text = "".join(
            getattr(b, "text", "") for b in resp.content
            if getattr(b, "type", None) == "text"
        )
        u = getattr(resp, "usage", None)
        if u is not None:
            for k in cache_usage:
                cache_usage[k] = getattr(u, k, 0) or 0
    except Exception as e:
        log.error("decision_engine: Claude API call failed for %s: %s", ticker, e)
        _log_llm_call(
            model=CLAUDE_MODEL, caller="decide_legacy", ticker=ticker.upper(),
            notes=f"api-failure: {e}",
        )
        return None

    # llm_calls ledger row — fire-and-forget cost stamp (mig 033).
    _log_llm_call(
        model=CLAUDE_MODEL, caller="decide_legacy", ticker=ticker.upper(),
        input_tokens=cache_usage["input_tokens"],
        output_tokens=cache_usage["output_tokens"],
        cache_read_tokens=cache_usage["cache_read_input_tokens"],
        cache_creation_tokens=cache_usage["cache_creation_input_tokens"],
        notes=f"signal_origin={signal_origin}",
    )

    try:
        decision = _parse_and_validate(raw_text, ctx=ctx)
    except ValueError as e:
        log.error("decision_engine: response validation failed for %s: %s", ticker, e)
        log.error("  raw response: %s", raw_text[:500])
        return None

    # Enrich with context + metadata for caller / persistence
    decision["ticker"]            = ticker.upper()
    decision["signal_origin"]     = signal_origin
    decision["signal_conviction"] = signal_conviction
    decision["account_value_usd"] = account_value_usd
    decision["decided_at"]        = datetime.utcnow().isoformat() + "Z"
    # Prompt-cache telemetry (smoke tests + cost tracking)
    decision["cache_creation_input_tokens"] = cache_usage["cache_creation_input_tokens"]
    decision["cache_read_input_tokens"]     = cache_usage["cache_read_input_tokens"]
    decision["input_tokens"]                = cache_usage["input_tokens"]
    decision["output_tokens"]               = cache_usage["output_tokens"]
    # FULL prompt context for ML — the complete feature vector that fed
    # the LLM (not a thinned summary). Persisted to
    # alerts_fired.prompt_context_full when an alert fires (migration 012).
    decision["prompt_context_full"] = {
        "static_framework":  static_block,
        "per_ticker_static": per_ticker_static,
        "dynamic":           dynamic_block,
        "system_prompt_sha": None,
        "model":             CLAUDE_MODEL,
        "decided_at":        decision["decided_at"],
    }
    # Compute the implied dollar size from bps for caller convenience
    from recommender import size_from_bps
    if decision.get("bps") in (50, 100):
        decision["recommended_dollars"] = size_from_bps(decision["bps"], account_value_usd)
    else:
        decision["recommended_dollars"] = None

    # Trim full context out of the returned dict (it's heavy); persistence can
    # store the ctx separately on alerts_fired / trade_recommendations if wanted.
    decision["context_summary"] = {
        "hedgeye_quad":    (ctx.get("hedgeye_macro") or {}).get("current_quad"),
        "vix_bucket":      (ctx.get("hedgeye_macro") or {}).get("vix_bucket"),
        "risk_range_low":  (ctx.get("risk_range") or {}).get("buy_trade"),
        "risk_range_high": (ctx.get("risk_range") or {}).get("sell_trade"),
        "etf_pro_low":     (ctx.get("etf_pro_range") or {}).get("range_low"),
        "etf_pro_high":    (ctx.get("etf_pro_range") or {}).get("range_high"),
        "etf_pro_week":    (ctx.get("etf_pro_range") or {}).get("week_of"),
        # SG stripped from live path 2026-05-24 — keys preserved as None so
        # downstream alert templates and the alerts_fired write path don't
        # need to special-case the absence.
        "spotgamma_call_wall": None,
        "spotgamma_put_wall":  None,
        "mfr_range_low":   (ctx.get("mfr") or {}).get("range_low"),
        "mfr_range_high":  (ctx.get("mfr") or {}).get("range_high"),
        "mfr_hurst":       (ctx.get("mfr") or {}).get("hurst"),
        "yahoo_price":     (ctx.get("yahoo") or {}).get("price"),
    }
    return decision


# ─────────────────────────── CLI smoke test ───────────────────────────

def _cli() -> None:
    ap = argparse.ArgumentParser(
        description="decision_engine smoke test — gather context for a ticker and call Claude"
    )
    ap.add_argument("--check-key", action="store_true",
                    help="Verify ANTHROPIC_API_KEY is loaded in the subprocess env. "
                         "Prints presence, length, and a masked fingerprint — never the value.")
    ap.add_argument("--ticker", help="Ticker to evaluate")
    ap.add_argument("--signal-origin", default="manual",
                    choices=["rta", "risk_range", "proactive_scan", "manual"])
    ap.add_argument("--signal-conviction", default=None,
                    help="Optional: pre-tagged conviction from classifier (Best Idea / Adding / etc)")
    ap.add_argument("--account-value", type=float, default=None,
                    help="Override account value (default: pull from portfolio)")
    ap.add_argument("--context-only", action="store_true",
                    help="Just gather context and print it, no Claude call (cheap)")
    ap.add_argument("--smoke-zone", action="store_true",
                    help="Run an offline assert: build the prompt with AMLP-like "
                         "MFR inputs (price=54.04, low=52.4960, high=55.1590, "
                         "trend_signal=bullish) and confirm the deterministic "
                         "zone line says mid_range + NOT a breach. No DB, no API.")
    args = ap.parse_args()

    if args.smoke_zone:
        # Hermetic: build _format_user_message against a synthetic ctx that
        # mirrors today's AMLP alert. Skip the corpus block + canon by mocking
        # _load_framework_canon to return "".
        global _load_framework_canon
        _orig_canon = _load_framework_canon
        _load_framework_canon = lambda: ""  # type: ignore[assignment]
        try:
            ctx = {
                "ticker": "AMLP",
                "as_of": "2026-05-12T16:00:00Z",
                "hedgeye_macro": {"current_quad": "Quad 2", "vix_bucket": "Investable"},
                "risk_range": {},
                "etf_pro_range": {},
                "spotgamma": {},
                "mfr": {
                    "ticker": "AMLP",
                    "price": 54.04,
                    "range_low": 52.4960,
                    "range_high": 55.1590,
                    "trend_signal": "bullish",
                    "hurst": 0.55,
                },
                "yahoo": {"price": 54.04},
                "_corpus_block": "",
            }
            msg = _format_user_message(
                ctx, signal_origin="manual",
                signal_conviction=None, account_value=50_000.0,
            )
        finally:
            _load_framework_canon = _orig_canon  # type: ignore[assignment]

        # The two anchors the bug fix must guarantee.
        zone_line = next(
            (L for L in msg.splitlines() if L.startswith("MFR zone for price")),
            "",
        )
        trend_line = next(
            (L for L in msg.splitlines() if L.startswith("Recent trend:")),
            "",
        )
        # Edge-only retune (2026-05-25): AMLP price=54.04 in range
        # [52.50, 55.16] is ~58% through the range — interior to the
        # default 15% edges → mid_range (formerly middle_third).
        ok_zone   = "mid_range" in zone_line
        ok_breach = "NOT a breach" in zone_line
        ok_trend  = "bullish" in trend_line
        assert ok_zone,   f"smoke: expected 'mid_range' in zone line, got: {zone_line!r}"
        assert ok_breach, f"smoke: expected 'NOT a breach' in zone line, got: {zone_line!r}"
        assert ok_trend,  f"smoke: expected 'bullish' in trend line, got: {trend_line!r}"

        # Post-validator contradiction flag check.
        fake_decision = {
            "conviction": "Adding", "direction": "long", "action": "ADD",
            "bps": 50, "confidence": 0.6,
            "evidence": [
                "MFR: price $54.04 is above MFR range high $55.16 (breach high ~1.8%)",
                "Quad 2 supports energy infrastructure exposure",
            ],
            "reasoning": "Pullback in an uptrend; price above range high suggests trim window.",
        }
        _flag_contradictions(fake_decision, ctx)
        flagged_ev = fake_decision["evidence"][0]
        flagged_rs = fake_decision["reasoning"]
        ok_ev_tag  = flagged_ev.startswith(_UNVERIFIED_TAG)
        ok_rs_tag  = flagged_rs.startswith(_UNVERIFIED_TAG)
        assert ok_ev_tag, f"smoke: expected evidence flagged, got: {flagged_ev!r}"
        assert ok_rs_tag, f"smoke: expected reasoning flagged, got: {flagged_rs!r}"

        print(json.dumps({
            "ok": True,
            "zone_line": zone_line,
            "trend_line": trend_line,
            "flagged_evidence": flagged_ev,
            "flagged_reasoning": flagged_rs,
        }, indent=2))
        return

    if args.check_key:
        k = os.environ.get("ANTHROPIC_API_KEY", "")
        # Also report the bridge's parent process PID + a probe of how many
        # python/pyw processes are running (to diagnose double-spawned bridges).
        parent_pid = os.getppid()
        bridge_count = -1
        try:
            import subprocess as _sp
            cp = _sp.run(
                ["tasklist", "/FI", "IMAGENAME eq pyw.exe", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=10, shell=False,
                creationflags=0x08000000,
            )
            bridge_count = len([L for L in cp.stdout.splitlines() if L.strip()])
        except Exception:
            pass
        result = {
            "present": bool(k),
            "length": len(k),
            "starts_with": k[:7] + "..." if len(k) >= 7 else None,
            "ends_with": "..." + k[-4:] if len(k) >= 4 else None,
            "has_trailing_whitespace": bool(k) and k != k.rstrip(),
            "has_internal_whitespace": any(c.isspace() for c in k),
            "model": CLAUDE_MODEL,
            "parent_pid": parent_pid,
            "pyw_count": bridge_count,
        }
        print(json.dumps(result, indent=2))
        return

    if not args.ticker:
        print("--ticker is required (unless --check-key)")
        return

    if args.context_only:
        ctx = gather_context(args.ticker, signal_conviction=args.signal_conviction)
        # Strip the heavy corpus block for terminal readability
        ctx_print = {k: v for k, v in ctx.items() if k != "_corpus_block"}
        ctx_print["_corpus_block_len"] = len(ctx.get("_corpus_block", ""))
        print(json.dumps(ctx_print, indent=2, default=str))
        return

    decision = decide(
        args.ticker,
        signal_origin=args.signal_origin,
        signal_conviction=args.signal_conviction,
        account_value_usd=args.account_value,
    )
    if not decision:
        print(json.dumps({"error": "decision engine returned None — see logs"}, indent=2), flush=True)
        sys.stdout.flush()
        return
    print(json.dumps(decision, indent=2, default=str), flush=True)
    sys.stdout.flush()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
