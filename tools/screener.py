"""SCREEN v2 — natural-language screener over v_screener (ticker_tags + latest
mfr_snapshots + TREND). Python owns ALL math/filtering; no LLM.

trend_dir = Hedgeye risk-range TREND (primary), MFR trend_signal fallback.
trend_source ('hdg'/'mfr') shown per row.

Telegram: `SCREEN <sentence>` e.g. `SCREEN energy shorts top of range`

Rules:
  range_pos   = (price - range_low)/(range_high-range_low)          [view]
  momentum_ok = MFR momentum_signal is momentumBullish              [view; no history]
  divergence  = MFR trade (trend_signal) vs momentum disagree — exhaustion-fade ⚡
  hurst       = MFR Hurst (>0.5 trending, <0.5 mean-reverting)      [view]
  iv/rv/ivpd  = MFR vol fields (authoritative)                      [view]
  corrSPY/corrUUP = bot-COMPUTED Pearson on daily returns vs SPY/UUP (calc, not MFR)
  near_bottom = range_pos <= 0.20 ; near_top = range_pos >= 0.80
  TREND gate MANDATORY, direction-tied (Rule-1 — nothing returns if it fails):
    longs  -> active_long/top_idea_long/long_bench   AND trend_dir='BULLISH'
    shorts -> active_short/top_idea_short/short_bench AND trend_dir='BEARISH'
  Tier markers: ●● active · ● top-idea · · bench.  Sort range_pos ASC (longs)/DESC (shorts).
A DARK section (passed tag filters, no MFR range) is always appended.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)
SENTINEL = "SCREEN"
CORR_MIN_N = 20   # min overlapping daily returns to report a correlation

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

# v2 — all three tiers screenable (bench included); TREND gate still applies.
_DIR_BUCKETS = {
    "longs":  (["active_long", "top_idea_long", "long_bench"],   "BULLISH"),
    "shorts": (["active_short", "top_idea_short", "short_bench"], "BEARISH"),
}


def _tier(bucket) -> str:
    b = bucket or ""
    if b.startswith("active"):   return "●●"   # active
    if b.startswith("top_idea"): return "●"    # top idea
    return "·"                                 # bench


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
           "momentum_dir, divergence, hurst, iv, rv, ivpd, trend_dir, trend_source, "
           "held, has_range FROM v_screener WHERE hedgeye_bucket_0629 = ANY(%s)")
    args = [buckets]
    if sector:
        sql += " AND gics_sector = %s"
        args.append(sector)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


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


def _is_mfr_only_topidea(r) -> bool:
    return ((r.get("hedgeye_bucket_0629") or "").startswith("top_idea")
            and r.get("trend_source") == "mfr")


def _num(v, sign=False, nd=2) -> str:
    if v is None:
        return "?"
    return f"{float(v):+.{nd}f}" if sign else f"{float(v):.{nd}f}"


def _fmt_row(r, corr) -> str:
    tier = _tier(r["hedgeye_bucket_0629"])
    rp = "n/a" if r["range_pos"] is None else f"{float(r['range_pos']):.2f}"
    md = {"BULLISH": "BULL", "BEARISH": "BEAR", "NEUTRAL": "NEUT"}.get(r.get("momentum_dir"), "?")
    src = {"hedgeye": "hdg", "mfr": "mfr"}.get(r.get("trend_source"), "")
    trend = f"{r['trend_dir'] or '-'}" + (f"·{src}" if src else "")
    cs, cu = corr.get(r["ticker"], (None, None))
    div = f" ⚡DIV({r['divergence']})" if r.get("divergence") else ""
    book = " 📗own" if r["held"] else ""
    warn = " ⚠mfr-only" if _is_mfr_only_topidea(r) else ""
    return (f"  {tier:<2} {r['ticker']:<9} {(r['subsector'] or ''):<18} {trend:<11} "
            f"rp={rp:<5} mom={md:<4} h={_num(r.get('hurst')):<5} "
            f"iv={_num(r.get('iv'))} rv={_num(r.get('rv'))} ivpd={_num(r.get('ivpd'), sign=True)} "
            f"cSPY={_num(cs, sign=True)} cUUP={_num(cu, sign=True)}{div}{book}{warn}")


def run_screen(text: str) -> str:
    """Parse + execute + format. On empty, names the FIRST funnel stage that hit 0."""
    q = parse_query(text)
    if not q["direction"]:
        return ("🔎 SCREEN needs a direction — say **longs** or **shorts** "
                "(the TREND gate is tied to it). E.g. `SCREEN energy shorts top of range`.")
    buckets, req_trend = _DIR_BUCKETS[q["direction"]]

    try:
        slice_ = _fetch_tag_slice(q["sector"], buckets)
    except Exception as e:
        log.exception("screen query failed")
        return f"🛑 SCREEN error: {e}"

    dark = sorted([r for r in slice_ if not r["has_range"]], key=lambda r: r["ticker"])
    ranged = [r for r in slice_ if r["has_range"]]

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
    corr = _corr_for([r["ticker"] for r in result]) if result else {}

    filt = [q["direction"].upper()]
    if q["sector"]:   filt.append(q["sector"])
    if q["near"]:     filt.append(f"near_{q['near']}")
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
        funnel = [
            f"tag match (sector+bucket): {len(slice_)}",
            f"→ has MFR range:           {len(ranged)}",
            f"→ TREND={req_trend} (Rule-1): {len(after_trend)}",
            f"→ {near_lbl}:              {len(after_near)}",
            (f"→ momentum_ok:             {len(after_mom)}" if q["momentum"] else None),
            (f"→ in-book:                 {len(after_held)}" if q["held"] else None),
        ]
        funnel = [f for f in funnel if f]
        # FIRST funnel stage that hit 0 (tag match / has-range / trend / near / ...).
        stages = [("tag match", len(slice_)), ("has-range", len(ranged)),
                  (f"TREND={req_trend}", len(after_trend))]
        if q["near"]:     stages.append((near_lbl, len(after_near)))
        if q["momentum"]: stages.append(("momentum_ok", len(after_mom)))
        if q["held"]:     stages.append(("in-book", len(after_held)))
        culprit = next((name for name, n in stages if n == 0), "unknown")
        lines.append(f"0 matches — emptied by: **{culprit}**")
        lines.append("")
        lines += funnel

    lines.append("")
    if dark:
        lines.append(f"🌑 DARK — passed tag filters, NO MFR range ({len(dark)}):")
        lines += [f"  {_tier(r['hedgeye_bucket_0629'])} {r['ticker']:<9} "
                  f"{(r['subsector'] or '')} [{r['hedgeye_bucket_0629']}]" for r in dark]
    else:
        lines.append("🌑 DARK: none — every tag-matched name has an MFR range.")
    return "\n".join(lines)


def handle_screen_command(text):
    """Telegram listener hook. Fires only on the SCREEN sentinel; else None."""
    if not text:
        return None
    s = text.strip()
    if s.upper().startswith(SENTINEL):
        return run_screen(s[len(SENTINEL):].lstrip(": ").strip())
    return None
