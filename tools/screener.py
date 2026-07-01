"""SCREEN — natural-language screener over v_screener (ticker_tags + latest
mfr_snapshots + TREND). Python owns ALL math/filtering; no LLM.

trend_dir = Hedgeye risk-range TREND (primary), falling back to MFR trend_signal
(trendBullish/Bearish/Neutral -> BULLISH/BEARISH/NEUTRAL) when Hedgeye has none.
trend_source ('hdg'/'mfr') is shown per row so it's clear which fired.

Telegram: `SCREEN <sentence>` e.g.
  SCREEN bring up all healthcare longs close to the bottom of the range with bullish momentum

Rules:
  range_pos   = (price - range_low)/(range_high-range_low)   [from the view]
  momentum_ok = price_today > price_20d_ago                  [from the view]
  near_bottom = range_pos <= 0.20 ; near_top = range_pos >= 0.80
  TREND gate is MANDATORY and tied to direction (Rule-1 — nothing returns if it fails):
    longs  -> bucket IN (active_long, top_idea_long)  AND trend_dir='BULLISH'
    shorts -> bucket IN (active_short, top_idea_short) AND trend_dir='BEARISH'
  Sort range_pos ASC (longs) / DESC (shorts).
A DARK section (passed the tag filters but has no MFR range) is always appended.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)
SENTINEL = "SCREEN"

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

_DIR_BUCKETS = {
    "longs":  (["active_long", "top_idea_long"],   "BULLISH"),
    "shorts": (["active_short", "top_idea_short"], "BEARISH"),
}


def parse_query(text: str) -> dict:
    """Parse a natural-language screen into a filter dict. Pure regex, no LLM."""
    s = " " + (text or "").lower() + " "
    q: dict = {"sector": None, "direction": None, "near": None,
               "momentum": False, "held": False, "raw": (text or "").strip()}
    for pat, canon in _SECTORS:
        if re.search(pat, s):
            q["sector"] = canon
            break
    if re.search(r"\bshorts?\b", s):
        q["direction"] = "shorts"
    elif re.search(r"\blongs?\b", s):
        q["direction"] = "longs"
    if re.search(r"bottom of (?:the )?range|near (?:the )?(?:low|bottom)|close to (?:the )?(?:low|bottom)", s):
        q["near"] = "bottom"
    elif re.search(r"top of (?:the )?range|near (?:the )?(?:high|top)|close to (?:the )?(?:high|top)", s):
        q["near"] = "top"
    if re.search(r"momentum", s):
        q["momentum"] = True
    if re.search(r"in my book|that i own|\bi own\b|\bheld\b|that i hold|that i'?m holding", s):
        q["held"] = True
    return q


def _fetch_tag_slice(sector, buckets):
    """All v_screener rows in the requested sector + direction buckets (tag filters
    only — no trend/range/momentum gates yet)."""
    import db_pg
    sql = ("SELECT ticker, subsector, hedgeye_bucket_0629, range_pos, momentum_ok, "
           "trend_dir, trend_source, held, has_range FROM v_screener "
           "WHERE hedgeye_bucket_0629 = ANY(%s)")
    args = [buckets]
    if sector:
        sql += " AND gics_sector = %s"
        args.append(sector)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _is_mfr_only_topidea(r) -> bool:
    """A top-idea name passing the gate on MFR trend only (no Hedgeye TREND) —
    kept in results, but direction is lower-confidence and gets a ⚠ marker."""
    return ((r.get("hedgeye_bucket_0629") or "").startswith("top_idea")
            and r.get("trend_source") == "mfr")


def _fmt_row(r) -> str:
    rp = "  n/a" if r["range_pos"] is None else f"{float(r['range_pos']):.2f}"
    mom = {True: "yes", False: "no", None: "?"}[r["momentum_ok"]]
    book = "📗own" if r["held"] else "-"
    src = {"hedgeye": "hdg", "mfr": "mfr"}.get(r.get("trend_source"), "")
    trend = f"{r['trend_dir'] or '-'}" + (f"·{src}" if src else "")
    warn = "  ⚠mfr-only" if _is_mfr_only_topidea(r) else ""
    return (f"  {r['ticker']:<9} {(r['subsector'] or ''):<20} {trend:<12} "
            f"rp={rp:<5} mom={mom:<3} {book}{warn}")


def run_screen(text: str) -> str:
    """Parse + execute + format. Returns the Telegram message. On empty, reports
    WHICH gate emptied it (never a silent nothing)."""
    q = parse_query(text)
    if not q["direction"]:
        return ("🔎 SCREEN needs a direction — say **longs** or **shorts** "
                "(the TREND gate is tied to it). E.g. `SCREEN healthcare longs near the low`.")
    buckets, req_trend = _DIR_BUCKETS[q["direction"]]

    try:
        slice_ = _fetch_tag_slice(q["sector"], buckets)
    except Exception as e:
        log.exception("screen query failed")
        return f"🛑 SCREEN error: {e}"

    # DARK = passed tag filters but no MFR range (screener is blind on these).
    dark = sorted([r for r in slice_ if not r["has_range"]], key=lambda r: r["ticker"])
    ranged = [r for r in slice_ if r["has_range"]]

    # Gate funnel (Python owns the math). TREND gate is Rule-1/mandatory.
    after_trend = [r for r in ranged if (r["trend_dir"] or "") == req_trend]
    if q["near"] == "bottom":
        after_near = [r for r in after_trend if r["range_pos"] is not None and float(r["range_pos"]) <= 0.20]
    elif q["near"] == "top":
        after_near = [r for r in after_trend if r["range_pos"] is not None and float(r["range_pos"]) >= 0.80]
    else:
        after_near = after_trend
    after_mom = [r for r in after_near if r["momentum_ok"] is True] if q["momentum"] else after_near
    after_held = [r for r in after_mom if r["held"]] if q["held"] else after_mom

    result = sorted(
        after_held,
        key=lambda r: (r["range_pos"] is None, float(r["range_pos"]) if r["range_pos"] is not None else 0),
        reverse=(q["direction"] == "shorts"),
    )

    # ---- format ----
    filt = [q["direction"].upper()]
    if q["sector"]:   filt.append(q["sector"])
    if q["near"]:     filt.append(f"near_{q['near']}")
    if q["momentum"]: filt.append("momentum_ok")
    if q["held"]:     filt.append("in-book")
    head = f"🔎 SCREEN — {' · '.join(filt)}  (TREND gate: {req_trend}, mandatory)"

    lines = [head, ""]
    if result:
        lines.append(f"{len(result)} match(es)  [ticker · subsector · trend · range_pos · momentum · book]")
        lines += [_fmt_row(r) for r in result]
        if any(_is_mfr_only_topidea(r) for r in result):
            lines.append("⚠ = top idea gated on MFR trend, no Hedgeye TREND published "
                         "— treat direction as lower-confidence.")
    else:
        # Funnel — show which gate emptied it (never silent).
        near_lbl = f"near_{q['near']}" if q["near"] else "range gate (none)"
        funnel = [
            f"tag match (sector+bucket): {len(slice_)}",
            f"→ has MFR range:           {len(ranged)}",
            f"→ TREND={req_trend} (Rule-1): {len(after_trend)}",
            f"→ {near_lbl}:              {len(after_near)}",
            f"→ momentum_ok:             {len(after_mom)}" if q["momentum"] else None,
            f"→ in-book:                 {len(after_held)}" if q["held"] else None,
        ]
        funnel = [f for f in funnel if f]
        # Identify the first gate that hit zero.
        stages = [("has-range", len(ranged)), (f"TREND={req_trend}", len(after_trend))]
        if q["near"]:     stages.append((near_lbl, len(after_near)))
        if q["momentum"]: stages.append(("momentum_ok", len(after_mom)))
        if q["held"]:     stages.append(("in-book", len(after_held)))
        culprit = next((name for name, n in stages if n == 0), "no rows in tag slice")
        lines.append(f"0 matches — emptied by: **{culprit}**")
        lines.append("")
        lines += funnel
    # DARK section — always appended.
    lines.append("")
    if dark:
        lines.append(f"🌑 DARK — passed tag filters, NO MFR range ({len(dark)}):")
        lines += [f"  {r['ticker']:<9} {(r['subsector'] or '')} [{r['hedgeye_bucket_0629']}]" for r in dark]
    else:
        lines.append("🌑 DARK: none — every tag-matched name has an MFR range.")
    return "\n".join(lines)


def handle_screen_command(text):
    """Telegram listener hook. Fires only on the SCREEN sentinel; returns None
    otherwise so the listener falls through."""
    if not text:
        return None
    s = text.strip()
    if s.upper().startswith(SENTINEL):
        return run_screen(s[len(SENTINEL):].lstrip(": ").strip())
    return None
