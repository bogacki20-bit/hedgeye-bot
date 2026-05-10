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
from datetime import date, datetime
from typing import Optional

log = logging.getLogger(__name__)


CLAUDE_MODEL = os.environ.get("DECISION_ENGINE_MODEL", "claude-sonnet-4-5")
CORPUS_SNIPPET_LIMIT = 4   # how many corpus snippets to fold into the prompt
CORPUS_MAX_CHARS = 2400    # cap total corpus text in prompt


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
        "spotgamma":      _get_spotgamma_latest(ticker),
        "mfr":            _get_mfr_latest(ticker),
        "yahoo":          _get_yahoo_latest(ticker),
        "_corpus_block":  _get_corpus_snippets(ticker, signal_conviction=signal_conviction),
    }


# ─────────────────────────── Prompt construction ───────────────────────────

_SYSTEM_PROMPT = """You are the decision brain of a personal trading bot. Your job is to
propose a sized trade recommendation for a single ticker, given multi-source
context (Hedgeye macro regime, Hedgeye Risk Range, SpotGamma dealer
positioning, MyFractalRange fractal data, Yahoo live price, plus corpus
snippets from Hedgeye University lessons and VolSignals/Natenberg
materials).

HARD CONSTRAINTS — your output MUST respect these:

1. `bps` must be one of: 50 or 100.
   - 100 = starter position (first BUY for a ticker) OR aggressive add
   - 50  = conservative add to an existing position
2. `conviction` must be one of: "Best Idea", "Adding", "Reducing", "Remove", "Monitor".
3. `direction` must be one of: "long", "short", "close".
4. `action` must be one of: "BUY", "ADD", "TRIM", "SELL", "WATCH".
5. The bps × account_value (provided in user message) clamped at $1,000 is
   the dollar value. Your reasoning should reflect this constraint.
6. Build incrementally: a single fill should be at most ~33% of full target
   position size. If your reasoning suggests "high conviction full size",
   propose multiple legs over time, not one big fill.

DECISION GUIDANCE:

- Hedgeye is the signal source. MFR/SpotGamma/Yahoo/corpus are corroborating
  evidence. Never override a Hedgeye-aligned setup with corroborating-source
  noise; only USE the corroborating sources to add or remove conviction.
- Bottom-third of Hedgeye Risk Range + below SpotGamma Put Wall = HIGH
  conviction long entry (framework-aligned dip).
- Top-third of Risk Range + above SpotGamma Call Wall = trim or short.
- MFR Hurst > 0.5 = trending (follow); Hurst < 0.5 = mean-reverting
  (fade extremes).
- Negative SpotGamma regime (net negative gamma) = expect volatility
  acceleration; size smaller and use tighter stops mentally.
- If risk range is missing/stale or all sources disagree sharply, default to
  "Monitor" (no trade) and explain why.

OUTPUT FORMAT — return ONLY a single JSON object, no prose around it:
{
  "conviction":  "<one of the five>",
  "direction":   "<long|short|close>",
  "action":      "<BUY|ADD|TRIM|SELL|WATCH>",
  "bps":         <50 or 100, or null if action is TRIM/SELL/WATCH>,
  "reasoning":   "<one short paragraph: which sources align, what the framework says>",
  "evidence":    ["<bullet 1>", "<bullet 2>", ...],
  "confidence":  <0.0 to 1.0 — your own probability that this trade is +EV>
}
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
    sections.append("## Hedgeye Risk Range (latest signal)")
    sections.append(json.dumps(_trim(ctx.get("risk_range") or {}), indent=2, default=str))
    sections.append("")
    sections.append("## SpotGamma (latest equity hub)")
    sections.append(json.dumps(_trim(ctx.get("spotgamma") or {}), indent=2, default=str))
    sections.append("")
    sections.append("## MFR (latest fractal range)")
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
        print(json.dumps({"error": "decision engine returned None — see logs"}, indent=2))
        return
    print(json.dumps(decision, indent=2, default=str))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
