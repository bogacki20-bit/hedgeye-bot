"""
t1a_parse.py — Tier One Alpha Market Situation Report → structured facts
(designed against the REAL OCR text of the 7/10 report, doc_uploads id 4).

Pipeline: screenshot -> vision OCR (transcription only) -> THIS module
(pure Python regex) -> t1a_daily row -> fact line in REPORT (and therefore
in DAYPACK — operator: 'this should also go in the llm dump').

OCR truths this parser respects:
  * The dashboard dials lose their SELECTION in transcription (all labels
    print) — so regimes come from the PROSE, which states them outright:
    'currently LONG GAMMA' · 'likely to increase their exposure' ·
    'Neutral Risk regimes'.
  * OCR eats decimal points on percentages ('118%' ≈ 1.18%). Ratio fields
    are stored RAW and flagged scale_suspect when implausibly large —
    never silently corrected.
  * Price levels survive cleanly (7543.84, 7455.62) — the derived
    flip-distance uses only those.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("t1a_parse")

_NUM = r"([\d][\d,]*\.?\d*)"


def _f(pat, text, flags=re.I):
    m = re.search(pat, text, flags)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_t1a(text: str) -> dict:
    """Pure extraction. Missing fields are None (loud downstream), never
    guessed. Returns {} shaped for t1a_daily."""
    t = text or ""
    out = {}

    # regimes — from prose (the dials' selection doesn't survive OCR)
    m = re.search(r"currently\s+(LONG|POSITIVE|SHORT|NEGATIVE)\s+GAMMA", t, re.I)
    out["gamma_regime"] = (None if not m else
                           ("positive" if m.group(1).upper() in ("LONG", "POSITIVE")
                            else "negative"))
    m = re.search(r"Systematic\s+Funds.{0,120}?(increase|decrease|reduce)\s+"
                  r"their\s+exposure", t, re.I | re.S)
    out["systematic_bias"] = (None if not m else
                              ("buyers" if m.group(1).lower() == "increase"
                               else "sellers"))
    m = re.search(r"(Risk[- ]?On|Risk[- ]?Off|Neutral)\s+Risk\s+regimes?", t, re.I)
    out["strategic_regime"] = (None if not m else
                               m.group(1).lower().replace("-", "_").replace(" ", "_"))

    # levels (clean through OCR)
    out["last_price"] = _f(rf"Last\s+Price:?\s*{_NUM}", t)
    out["upper_pv"] = _f(rf"Upper\s+PV\s+Band:?\s*{_NUM}", t)
    out["lower_pv"] = _f(rf"Lower\s+PV\s+Band:?\s*{_NUM}", t)
    out["gex_flip"] = _f(rf"GEX\s+Price:?\s*{_NUM}", t)
    out["gex_throttle"] = _f(rf"GEX\s+Throttle:?\s*(-?[\d.]+)", t)
    out["support_strike"] = _f(rf"Support\s+Strike:?\s*{_NUM}", t)
    out["focal_strike"] = _f(rf"Focal\s+Strike:?\s*{_NUM}", t)
    out["resistance_strike"] = _f(rf"Resistance\s+Strike:?\s*{_NUM}", t)

    # ratios (scale-suspect when OCR dropped the decimal)
    out["upside_risk"] = _f(r"Upside\s+Risk:?\s*(-?[\d.]+)\s*%", t)
    out["downside_risk"] = _f(r"Downside\s+Risk:?\s*(-?[\d.]+)\s*%", t)
    out["core_pct"] = _f(r"Core\s+Position\D{0,10}([\d.]+)\s*%", t)
    out["low_beta_pct"] = _f(r"Low\s+Beta\D{0,10}([\d.]+)\s*%", t)
    out["scale_suspect"] = any(
        v is not None and abs(v) > 25
        for v in (out["upside_risk"], out["downside_risk"]))

    # derived (Python math over clean prices only)
    lp, fl = out["last_price"], out["gex_flip"]
    out["flip_dist_pct"] = (round((lp - fl) / lp * 100.0, 2)
                            if lp and fl else None)

    # high/medium-impact econ events w/ expected move (pipe rows survive)
    events = []
    for ln in t.split("\n"):
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) >= 5 and re.match(r"20\d{2}-\d{2}-\d{2}$", parts[0]):
            impact = next((p for p in parts if p.lower() in
                           ("high", "medium", "low")), None)
            move = next((p for p in parts if p.startswith("~")), None)
            if impact in ("high", "medium"):
                events.append({"date": parts[0], "event": parts[1],
                               "impact": impact, "exp_move": move})
    out["events"] = events
    return out


def fact_line(row: dict) -> str:
    """One REPORT/DAYPACK line from a t1a_daily-shaped dict. Missing = ?"""
    g = (row.get("gamma_regime") or "?").upper()
    fd = row.get("flip_dist_pct")
    fd_s = (f"flip {row['gex_flip']:.0f} ({fd:+.1f}%)"
            if fd is not None and row.get("gex_flip") else "flip ?")
    th = row.get("gex_throttle")
    th_s = (f"throttle {th:g} ({'compression' if th > 0 else 'expansion'})"
            if th is not None else "throttle ?")
    sb = row.get("systematic_bias") or "?"
    sr = (row.get("strategic_regime") or "?").replace("_", "-")
    ev = (row.get("events") or [])
    nxt = next((e for e in ev if e["impact"] == "high"), None)
    nxt_s = (f" · next high-impact: {nxt['event']} {nxt['date']}"
             + (f" ({nxt['exp_move']})" if nxt.get("exp_move") else "")
             if nxt else "")
    warn = " ⚠ratio-fields scale-suspect (OCR)" if row.get("scale_suspect") else ""
    return (f"T1A[{row.get('report_date', '?')}]: gamma {g} · {fd_s} · {th_s} "
            f"· systematics {sb.upper()} · strategic {sr.upper()}{nxt_s}{warn}")


def store_t1a(parsed: dict, report_date, doc_id=None) -> bool:
    """Upsert one t1a_daily row. Returns True on write."""
    import db_pg
    cols = ["gamma_regime", "systematic_bias", "strategic_regime",
            "last_price", "upper_pv", "lower_pv", "gex_flip", "gex_throttle",
            "support_strike", "focal_strike", "resistance_strike",
            "upside_risk", "downside_risk", "core_pct", "low_beta_pct",
            "flip_dist_pct", "scale_suspect"]
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute(
                f"""INSERT INTO t1a_daily (report_date, {', '.join(cols)},
                                           events, doc_id)
                    VALUES (%s, {', '.join(['%s'] * len(cols))}, %s, %s)
                    ON CONFLICT (report_date) DO UPDATE SET
                    {', '.join(f'{k}=EXCLUDED.{k}' for k in cols)},
                    events=EXCLUDED.events, doc_id=EXCLUDED.doc_id,
                    parsed_at=now()""",
                [report_date] + [parsed.get(k) for k in cols]
                + [json.dumps(parsed.get("events") or []), doc_id])
            c.commit()
        return True
    except Exception as e:
        log.warning("t1a_daily store failed: %s", e)
        return False


def latest_line() -> str | None:
    """The most recent t1a_daily row rendered for REPORT/DAYPACK; None when
    the table is empty (section prints n/a upstream)."""
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT * FROM t1a_daily ORDER BY report_date DESC LIMIT 1")
        r = cur.fetchone()
        if not r:
            return None
        cols = [d[0] for d in cur.description]
        row = dict(zip(cols, r))
    for k in ("last_price", "gex_flip", "gex_throttle", "flip_dist_pct"):
        if row.get(k) is not None:
            row[k] = float(row[k])
    if isinstance(row.get("events"), str):
        row["events"] = json.loads(row["events"])
    return fact_line(row)


def ingest_hook(doc_id: int, note_date, text: str) -> str | None:
    """Called by doc_ingest after storing a tier1alpha upload: parse ->
    upsert -> return a summary line for the Telegram reply (None = parse
    produced nothing usable; loud upstream)."""
    parsed = parse_t1a(text)
    core = [k for k in ("gamma_regime", "gex_flip", "last_price")
            if parsed.get(k) is not None]
    if not core:
        return None
    if note_date is None:
        return "⚠ T1A parse skipped: report UNDATED (a fact without a date)"
    if store_t1a(parsed, note_date, doc_id):
        parsed["report_date"] = note_date
        return "parsed → t1a_daily: " + fact_line(parsed)
    return "🛑 T1A parsed but store failed (see logs)"
