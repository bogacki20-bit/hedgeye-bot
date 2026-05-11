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
          → Risk-on, growth-equity, cyclicals

  QUAD 2: ENERGY (XLE, XOP, OIH), Materials (XLB), Industrials (XLI),
          Financials (XLF), Tech (selective), Bitcoin (BTC), Small-Caps,
          Commodities broadly → Pro-cyclical + inflation hedges. Energy
          is BULLISH in Quad 2.

  QUAD 3: Energy (XLE), Gold (GLD), Gold Miners (GDX), Silver (SLV),
          Utilities (XLU), Staples (XLP), TIPS → Inflation hedges + defensives

  QUAD 4: Bonds (TLT, IEF, AGG), Utilities (XLU), Staples (XLP), Healthcare
          (XLV), Gold, US Dollar (UUP) → Defensives + duration

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
  If ALL three range sources are missing → Monitor.

STEP 3 — Does SpotGamma corroborate?
  Bottom-third + below Put Wall = strong dealer support (negative gamma below → magnet up).
  Top-third + above Call Wall = strong dealer resistance (negative gamma above → ceiling).
  Hedge Wall is far-OTM, usually not actionable.
  Negative net gamma regime = expect amplified moves, size smaller.

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
  All four agree → Best Idea, 100 bps starter or 100 bps add.
  Three of four agree → Adding, 50 bps.
  Two or fewer agree, or sharp disagreement → Monitor, no trade.

  HARD CEILING RULES (NOT OVERRIDABLE under any circumstance, no matter how
  strong the framework alignment, no matter what other layers say, no matter
  what HU doctrine you cite):

    R1. ALL Hedgeye range sources missing AND MFR also unavailable → Monitor.
        Force conviction='Monitor', action='WATCH', bps=null. NO EXCEPTIONS.

    R2. Hedgeye range sources missing but MFR present → max conviction='Adding',
        max bps=50, action='ADD' or 'BUY'. NO EXCEPTIONS. Hedgeye is the master
        signal source; without it we never go full-size. If you find yourself
        about to write 'Best Idea' or '100 bps' here, STOP and downshift to
        'Adding' / 50 bps regardless of how strong the other votes are.

    R3. Account value $0 or unknown → Monitor. Can't size without a denominator.

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


def _format_user_message(ctx: dict, *, signal_origin: str,
                         signal_conviction: Optional[str],
                         account_value: float) -> str:
    """Compose the user message: structured context + corpus snippets +
    explicit account value so Claude can size."""
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

    sections = []

    # Framework canon FIRST — this is the bot's operating rulebook (Hedgeye +
    # SpotGamma + market-maker mechanics) and must be considered before
    # interpreting any of the dynamic context blocks that follow.
    canon = _load_framework_canon()
    if canon:
        sections.append("## Framework canon (authoritative operating rules)")
        sections.append(canon)
        sections.append("")

    sections.append(f"## Ticker: {ctx['ticker']}")
    sections.append(f"As of: {ctx['as_of']}")
    sections.append(f"Account value (for bps sizing): ${account_value:,.0f}")
    sections.append(f"Signal origin: {signal_origin}")
    if signal_conviction:
        sections.append(f"Hedgeye-tagged conviction (input): {signal_conviction}")
    sections.append("")

    sections.append("## Hedgeye macro context")
    sections.append(json.dumps(_trim(ctx.get("hedgeye_macro") or {}), indent=2, default=str))
    sections.append("")
    sections.append("## Hedgeye Risk Range (latest daily signal — PRIMARY range source)")
    sections.append(json.dumps(_trim(ctx.get("risk_range") or {}), indent=2, default=str))
    sections.append("")
    sections.append("## Hedgeye ETF Pro Range (Monday weekly — SECONDARY range source for ETFs)")
    sections.append(json.dumps(_trim(ctx.get("etf_pro_range") or {}), indent=2, default=str))
    sections.append("")
    sections.append("## SpotGamma (latest equity hub)")
    sections.append(json.dumps(_trim(ctx.get("spotgamma") or {}), indent=2, default=str))
    sections.append("")
    sections.append("## MFR (latest fractal range — TERTIARY range source; secondary to Hedgeye)")
    sections.append(json.dumps(_trim(ctx.get("mfr") or {}), indent=2, default=str))
    sections.append("")
    sections.append("## Yahoo (latest snapshot)")
    sections.append(json.dumps(_trim(ctx.get("yahoo") or {}), indent=2, default=str))
    sections.append("")

    if ctx.get("_corpus_block"):
        sections.append("## Relevant corpus snippets (Hedgeye U, Macro Show, VolSignals)")
        sections.append(ctx["_corpus_block"])
        sections.append("")

    sections.append("## Task")
    sections.append("Propose the sized recommendation as JSON per the system prompt's schema.")
    return "\n".join(sections)


# ─────────────────────────── Output validation ───────────────────────────

def _parse_and_validate(text: str) -> dict:
    """Extract the JSON object from Claude's response and validate against
    the schema constraints. Raises ValueError on hard violations."""
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
    user_msg = _format_user_message(
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

    try:
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        raw_text = "".join(
            getattr(b, "text", "") for b in resp.content
            if getattr(b, "type", None) == "text"
        )
    except Exception as e:
        log.error("decision_engine: Claude API call failed for %s: %s", ticker, e)
        return None

    try:
        decision = _parse_and_validate(raw_text)
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
    args = ap.parse_args()

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
