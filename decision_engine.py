"""Bot's decision brain — Claude API call over the multi-source corpus.

The trade decision is NOT a Python rule lookup. It's a Claude API call that
synthesizes:
  - Hedgeye Quad + VIX bucket            (monitor_context.get_hedgeye_ctx)
  - Hedgeye Risk Range zone              (latest hedgeye_risk_ranges row)
  - SpotGamma walls + IV/skew + regime   (spotgamma_client.latest)
  - MFR range + Hurst + trend            (mfr_snapshots latest)
  - Yahoo live price                     (yfinance_client.latest)
  - Recent corpus snippets (FTS)         (HU transcripts, Macro Show, VolSignals)

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
from datetime import date, datetime
from typing import Optional

log = logging.getLogger(__name__)


CLAUDE_MODEL = os.environ.get("DECISION_ENGINE_MODEL", "claude-sonnet-4-5")
CORPUS_SNIPPET_LIMIT = 4   # how many corpus snippets to fold into the prompt
CORPUS_MAX_CHARS = 2400    # cap total corpus text in prompt

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
    Includes buy_trade, sell_trade, trend, prev_close, signal_date."""
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
                return dict(row) if row else None
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
                    SELECT ticker, week_of, range_low, range_high, description
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
    """Most recent mfr_snapshots row for the ticker, typed columns only."""
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
    """Most recent spotgamma_snapshots row via spotgamma_client.latest."""
    try:
        import spotgamma_client
        return spotgamma_client.latest(ticker)
    except Exception as e:
        log.warning("decision_engine: spotgamma fetch failed for %s: %s", ticker, e)
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


def _zone_summary_line(label: str, price, low, high) -> str:
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
    return (f"{label} zone for price {p} in range [{lo}, {hi}]: "
            f"{zone} ({pct:.0f}% through range; {verdict})")


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
    mfr_price = mfr_block.get("price") or yahoo_block.get("price")

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
    sections.append(_zone_summary_line(
        "Hedgeye Risk Range", mfr_price,
        rr_block.get("buy_trade"), rr_block.get("sell_trade"),
    ))
    sections.append(json.dumps(_trim(rr_block), indent=2, default=str))
    sections.append("")
    sections.append("## Hedgeye ETF Pro Range (Monday weekly — SECONDARY range source for ETFs)")
    sections.append(_zone_summary_line(
        "ETF Pro Range", mfr_price,
        etf_block.get("range_low"), etf_block.get("range_high"),
    ))
    sections.append(json.dumps(_trim(etf_block), indent=2, default=str))
    sections.append("")
    sections.append("## SpotGamma (latest equity hub)")
    sections.append(_spotgamma_framing_line(ctx.get("spotgamma") or {}, mfr_price))
    sections.append(json.dumps(_trim(ctx.get("spotgamma") or {}), indent=2, default=str))
    sections.append("")
    sections.append("## MFR (latest fractal range — TERTIARY range source; secondary to Hedgeye)")
    # Deterministic zone + trend so the LLM can't hallucinate "above range high"
    # or "pullback" out of the raw JSON. Anchor wording must match price_monitor.
    sections.append(_zone_summary_line(
        "MFR", mfr_block.get("price"),
        mfr_block.get("range_low"), mfr_block.get("range_high"),
    ))
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

def decide(
    ticker: str,
    *,
    signal_origin: str = "proactive_scan",
    signal_conviction: Optional[str] = None,
    account_value_usd: Optional[float] = None,
) -> Optional[dict]:
    """Call the decision engine for `ticker`. Returns the validated decision
    dict, or None on fatal error (e.g. missing Anthropic key).

    Args:
        ticker: stock/ETF symbol.
        signal_origin: 'rta' / 'risk_range' / 'proactive_scan' / 'manual' —
            stamped on the resulting decision for audit.
        signal_conviction: if the classifier already tagged a conviction tier
            (from a Hedgeye email), pass it; the prompt presents it as a
            user-provided input that Claude can corroborate or override.
        account_value_usd: target account's total value, for bps sizing math.
            If None, defaults to the Individual account via portfolio.account_value.
    """
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
        return None

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
        "spotgamma_call_wall": ((ctx.get("spotgamma") or {}).get("call_wall")),
        "spotgamma_put_wall":  ((ctx.get("spotgamma") or {}).get("put_wall")),
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
                         "zone line says middle_third + NOT a breach. No DB, no API.")
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
        ok_zone   = "middle_third" in zone_line
        ok_breach = "NOT a breach" in zone_line
        ok_trend  = "bullish" in trend_line
        assert ok_zone,   f"smoke: expected 'middle_third' in zone line, got: {zone_line!r}"
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
