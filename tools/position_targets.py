"""
position_targets.py — TRANCHE v2 + v4.1 calibration (2026-07-11).

fill% = current % of account / target % of account. State-free: needs only
the latest book snapshot + a target — no activity history required.

DEFAULT TIERS (v4.1 FIX 1 — recalibrated: the 6%-for-everything slide
maxima summed to ~378% of book across 63 names, drowning the buckets):
  dflt-fi   10.0%  fixed income / treasury-duration funds
  dflt-core  4.0%  broad/theme ETFs (sector SPDRs, FXH, PAVE, VYM, …)
  dflt-eq    2.0%  single-name equities (and anything unroutable)
  dflt-sat   1.0%  satellites: inverse/levered, commodity, single-country
                   (MSCI x), crypto wrappers, and ALL short exposure
FLAGGED CHOICE (printed by the apply dry run, operator can override per
name): treasury-DURATION ETFs (TLT/ZROZ/TUA/…) route dflt-fi 10%, NOT
core 4% — the Hedgeye FI max. Override: TARGET <tkr> <pct> [acct].

Routing needs FUND NAMING in the Fidelity description before any fund tier
applies — a plain stock stays dflt-eq no matter what words its name has
(BARRICK GOLD CORP is never a commodity fund: the GOLD-ticker lesson).
Fidelity abbreviates (TREAS/TRS/BD/FD…) — regexes carry those forms.
Names absent from ticker_tags are additionally marked GUESSED in the dry
run (FIX 5): tier came from description alone.

CASH EQUIVALENTS (FIX 2): ticker_tags.cash_equivalent=1 (operator-set:
TARGET CASHEQ <tkr> -> CONFIRM TARGET; TARGET NOCASHEQ to unset). Excluded
from fills/exposure/CONC; their value prints on the CASH line as parked.

MULTI-ACCOUNT (FIX 3): fills are computed per (ticker, account); split
names also get an aggregate: agg fill = Σ|mv| / Σ(target_pct_a ·
account_total_a) — true exposure, never one account's slice.

Targets are IDENTITY FACTS: set via Telegram, gated on the literal
`CONFIRM TARGET` (deliberately NOT bare CONFIRM — SS/QUAD cross-clear
doesn't know this module). Never inferred, never LLM-written.

Telegram:
  TARGET LIST                      — explicit rows + cash-equivs + doctrine
  TARGET <TKR> <pct> [IND|RIRA|ROTH] [note] — stage (default acct IND)
  TARGET DEL <TKR> [acct]          — stage removal
  TARGET CASHEQ <TKR> / NOCASHEQ <TKR> — stage cash-equivalent (un)flag
  CONFIRM TARGET / CANCEL TARGET   — commit / discard staged (15-min TTL)
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger("position_targets")

SENTINEL = "TARGET"
PENDING_KEY = "target_pending"
TTL_MIN = 15

# Fidelity account numbers -> operator codes (CLAUDE.md account rules).
ACCOUNT_CODES = {"X96383748": "IND", "244859926": "RIRA", "245734604": "ROTH"}
VALID_ACCOUNTS = ("IND", "RIRA", "ROTH")

TIERS = {"fi": 10.0, "core": 4.0, "eq": 2.0, "sat": 1.0}

# Fund naming (incl. Fidelity abbreviations + issuer names) — REQUIRED
# before any fund tier applies.
_FUND_RE = re.compile(
    r"\bETF\b|\bETN\b|\bETP\b|\bFUND\b|\bFD\b|\bTRUST\b|\bTR\b|\bSHARES\b"
    r"|\bSHS\b|\bINDEX\b|\bISHARES\b|\bSPDR\b|\bVANGUARD\b|\bPROSHARES\b"
    r"|\bDIREXION\b|\bSIMPLIFY\b|\bINVESCO\b|\bPIMCO\b|\bWISDOMTREE\b"
    r"|\bABRDN\b|\bQUADRATIC\b|\bGLOBAL X\b|\bFIRST TRUST\b|\bSELECT SECTOR\b",
    re.I)
_FI_RE = re.compile(
    r"\bTREASURY\b|\bTREAS\b|\bTRS\b|\bBOND\b|\bBD\b|\bBND\b|\bBILL\b"
    r"|\bDURATION\b|\bTIPS\b|\bMUNI\b|\bAGGREGATE\b|\bAGG\b|\bZERO CPN\b"
    r"|\bFIXED INCOME\b|\bYIELD CURVE\b|\bDEFLATION\b|\bRATE\b", re.I)
_SAT_RE = re.compile(
    r"\bDAILY\b|\bBEAR\b|\bBULL\b|\bULTRASHORT\b|\bULTRA\b|\bINVERSE\b"
    r"|\bLEVERAGED\b|[+-]?\d(?:\.\d)?X\b"                      # inverse/levered
    r"|\bGOLD\b|\bSILVER\b|\bPLATINUM\b|\bPALLADIUM\b|\bOIL\b|\bGAS\b"
    r"|\bCOMMODITY\b|\bCOPPER\b|\bURANIUM\b"                   # commodity
    r"|\bMSCI\b"                                               # single-country
    r"|\bBITCOIN\b|\bBITCO\b|\bETHER\b|\bETHEREUM\b|\bSOLANA\b|\bCRYPTO\b",
    re.I)


# ═══════════════════════ pure logic (fixture-tested) ════════════════════════

def account_code(account_number: str) -> str:
    """Operator code for a Fidelity account number; unknown numbers pass
    through visibly (never silently bucketed)."""
    return ACCOUNT_CODES.get((account_number or "").strip(),
                             (account_number or "?").strip() or "?")


def default_target(description: str | None, side: str | None = None) -> tuple:
    """(target_pct, src_label) DEFAULT tier when no explicit row exists.
    Order: short exposure -> sat · fund+FI naming -> fi · fund+sat naming
    -> sat · fund naming -> core · else single-name eq. The label is always
    printed — a default is never dressed as a fact."""
    if side == "short":
        return TIERS["sat"], "dflt-sat"
    d = description or ""
    if _FUND_RE.search(d):
        if _FI_RE.search(d):
            return TIERS["fi"], "dflt-fi"
        if _SAT_RE.search(d):
            return TIERS["sat"], "dflt-sat"
        return TIERS["core"], "dflt-core"
    return TIERS["eq"], "dflt-eq"


def fill_bucket(fill_pct) -> str:
    """<40 STARTER · 40-80 BUILDING · 80-110 FULL · >110 OVER."""
    if fill_pct is None:
        return "?"
    if fill_pct < 40:
        return "STARTER"
    if fill_pct < 80:
        return "BUILDING"
    if fill_pct <= 110:
        return "FULL"
    return "OVER"


def parse_target_command(text: str):
    """Parse a TARGET message -> {op: list|del|set|casheq|nocasheq, ...},
    {'error': msg}, or None when not a TARGET command."""
    if not text:
        return None
    parts = text.strip().split()
    if not parts or parts[0].upper() != SENTINEL:
        return None
    if len(parts) == 1 or parts[1].upper() == "LIST":
        return {"op": "list"}
    op = parts[1].upper()
    if op in ("CASHEQ", "NOCASHEQ"):
        if len(parts) < 3:
            return {"error": f"usage: TARGET {op} <ticker>"}
        return {"op": op.lower(), "ticker": parts[2].upper()}
    if op == "DEL":
        if len(parts) < 3:
            return {"error": "usage: TARGET DEL <ticker> [IND|RIRA|ROTH]"}
        acct = parts[3].upper() if len(parts) > 3 else "IND"
        if acct not in VALID_ACCOUNTS:
            return {"error": f"unknown account {acct!r} — use IND/RIRA/ROTH"}
        return {"op": "del", "ticker": parts[2].upper(), "account": acct}
    if len(parts) < 3:
        return {"error": "usage: TARGET <ticker> <pct> [IND|RIRA|ROTH] [note]"}
    ticker = parts[1].upper()
    try:
        pct = float(parts[2].rstrip("%"))
    except ValueError:
        return {"error": f"bad pct {parts[2]!r} — a number like 2.5"}
    if not (0 < pct <= 25):
        return {"error": f"pct {pct:g} out of bounds (0 < pct <= 25)"}
    acct, note_from = "IND", 3
    if len(parts) > 3 and parts[3].upper() in VALID_ACCOUNTS:
        acct, note_from = parts[3].upper(), 4
    return {"op": "set", "ticker": ticker, "pct": pct, "account": acct,
            "note": " ".join(parts[note_from:]) or None}


def fmt_fill_ctx(acct_pct, fill, tgt, tgt_src, acct, pl,
                 verbose: bool = False) -> str:
    """Flag-line context (FIX 4 — one computation, two renders).
    compact:  ',3.1%acct,52%,IND,+6.0%pl' — tgt shown ONLY when the target
              is explicit (v4.1 FIX 3: OVER names no longer print it either;
              fill% alone suffices, full detail lives in upload mode).
    verbose:  ',3.1%acct,52%fill→4.0%tgt·dflt-core,IND,+6.0%pl' always.
    Missing prints ?, never disappears. acct may be 'RIRA+IND' (agg)."""
    a = f"{acct_pct:.1f}%acct" if acct_pct is not None else "?%acct"
    p = f"{pl:+.1f}%pl" if pl is not None else "?%pl"
    tgt_s = (f"→{tgt:.1f}%tgt" + (f"·{tgt_src}" if tgt_src else "")
             if tgt is not None else "→?tgt")
    if verbose:
        f_ = (f"{fill:.0f}%fill" if fill is not None else "?%fill") + tgt_s
    else:
        f_ = f"{fill:.0f}%" if fill is not None else "?%"
        if tgt_src is None:
            f_ += tgt_s
    return f",{a},{f_},{acct or '?'},{p}"


def aggregate_split(legs: list) -> tuple:
    """FIX 3 pure math. legs: [(gmv, acct_total, tgt_pct)] per account.
    Returns (agg_acct_pct, agg_fill): exposure and fill vs the SUM of
    per-account target dollars. None where undefined."""
    tot_mv = sum(g for g, _, _ in legs)
    tot_base = sum(a for _, a, _ in legs if a)
    tgt_dollars = sum(a * t / 100.0 for _, a, t in legs if a and t)
    pct = (tot_mv / tot_base * 100.0) if tot_base > 0 else None
    fill = (tot_mv / tgt_dollars * 100.0) if tgt_dollars > 0 else None
    return pct, fill


# ═══════════════════════ DB reads ═══════════════════════════════════════════

def get_targets() -> dict:
    """{(ticker, account): {'pct': float, 'set_date': date, 'note': str}}."""
    import db_pg
    out = {}
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT ticker, account, target_pct, set_date, note "
                    "FROM position_targets")
        for t, a, p, d, n in cur.fetchall():
            out[(t, a)] = {"pct": float(p), "set_date": d, "note": n}
    return out


def get_cash_equivalents(cur=None) -> set:
    """Tickers flagged cash_equivalent=1 (operator identity facts)."""
    def _q(cur):
        cur.execute("SELECT ticker FROM ticker_tags WHERE cash_equivalent = 1")
        return {r[0] for r in cur.fetchall()}
    if cur is not None:
        return _q(cur)
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur2:
        return _q(cur2)


def compute_fills(cur, sides: dict | None = None,
                  sim_targets: dict | None = None,
                  sim_casheq: set | None = None) -> dict:
    """One compute pass (FIX 4) -> dict:
      agg:        {underlying: {acct, acct_pct, fill, bucket, tgt, tgt_src,
                   pl, multi, weight}}   (split names: aggregated, acct =
                   'RIRA+IND', tgt/src = None marker 'agg' when mixed)
      per_acct:   [(t, acct, acct_pct, tgt, src, fill, bucket, pl, gmv)]
                  one row per (ticker, account) for the full table
      cash_equiv: {ticker: mv} parked names (excluded from all the above)
      gross:      Σ|mv| non-cash, non-cash-equiv legs (weight base)
      target_sum_pct: Σ target dollars / AUM-ish base ·100 (FIX 1 sanity)
      guessed:    sorted tickers whose tier came from description alone
                  (not in ticker_tags — verify before trusting fill%)
      fi_routed:  sorted tickers routed dflt-fi (the flagged tier choice)
    SIMULATION (v4.1 FIX 4 — dry runs must show the post-seed state without
    writing): sim_targets {ticker: pct} act as pending explicit rows (any
    account); sim_casheq is a set of pending cash-equivalent flags. Purely
    in-memory — the DB is never touched by simulation.
    """
    cur.execute("""
        SELECT underlying, account_number,
               sum(abs(market_value))  AS gmv,
               sum(total_gl_dollar)    AS gl,
               sum(cost_basis)         AS cost,
               max(description)        AS descr
        FROM book_positions
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
          AND asset_class <> 'cash' AND COALESCE(quantity, 0) <> 0
        GROUP BY underlying, account_number""")
    raw = cur.fetchall()

    cur.execute("""
        SELECT account_number,
               sum(CASE WHEN asset_class = 'cash' THEN COALESCE(market_value, 0)
                        ELSE abs(COALESCE(market_value, 0)) END)
        FROM book_positions
        WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)
        GROUP BY account_number""")
    acct_total = {an: float(v or 0) for an, v in cur.fetchall()}

    ce = set()
    try:
        ce = get_cash_equivalents(cur)
    except Exception as e:
        log.warning("cash_equivalent column unreadable (%s) — none excluded", e)
    if sim_casheq:
        ce |= {t.upper() for t in sim_casheq}
    tagged = set()
    try:
        cur.execute("SELECT ticker FROM ticker_tags")
        tagged = {r[0] for r in cur.fetchall()}
    except Exception as e:
        log.warning("ticker_tags unreadable (%s)", e)
    targets = {}
    try:
        targets = get_targets()
    except Exception as e:
        log.warning("position_targets unreadable (%s) — defaults only", e)

    per: dict = {}
    cash_equiv: dict = {}
    for t, an, g, gl, cost, descr in raw:
        if t in ce:
            cash_equiv[t] = cash_equiv.get(t, 0.0) + float(g or 0)
            continue
        per.setdefault(t, []).append(
            {"acct_no": an, "gmv": float(g or 0),
             "gl": (float(gl) if gl is not None else None),
             "cost": (float(cost) if cost is not None else None),
             "descr": descr})

    gross = sum(x["gmv"] for legs in per.values() for x in legs)
    agg, per_rows, guessed, fi_routed = {}, [], set(), set()
    tgt_dollars_sum = 0.0
    for t, legs in sorted(per.items()):
        side = ((sides or {}).get(t) or {}).get("side")
        leg_calc = []
        for x in sorted(legs, key=lambda v: -v["gmv"]):
            acct = account_code(x["acct_no"])
            total = acct_total.get(x["acct_no"]) or 0
            exp = targets.get((t, acct))
            if sim_targets and t in sim_targets:
                tgt, src = float(sim_targets[t]), None   # pending seed
            elif exp:
                tgt, src = exp["pct"], None
            else:
                tgt, src = default_target(x["descr"], side)
                if t not in tagged:
                    guessed.add(t)
                if src == "dflt-fi":
                    fi_routed.add(t)
            pct = (x["gmv"] / total * 100.0) if total > 0 else None
            fill = (pct / tgt * 100.0) if (pct is not None and tgt) else None
            pl_leg = (x["gl"] / abs(x["cost"]) * 100.0
                      if x["gl"] is not None and x["cost"] else None)
            per_rows.append((t, acct, pct, tgt, src, fill,
                             fill_bucket(fill), pl_leg, x["gmv"]))
            leg_calc.append((x["gmv"], total, tgt, acct, src))
            tgt_dollars_sum += (total * tgt / 100.0) if (total and tgt) else 0

        gl = sum(x["gl"] for x in legs if x["gl"] is not None)
        cost = sum(x["cost"] for x in legs if x["cost"] is not None)
        pl = (gl / abs(cost) * 100.0
              if any(x["gl"] is not None for x in legs) and cost else None)
        if len(legs) == 1:
            row = per_rows[-1]
            agg[t] = {"acct": row[1], "acct_pct": row[2], "fill": row[5],
                      "bucket": row[6], "tgt": row[3], "tgt_src": row[4],
                      "pl": pl, "multi": False,
                      "weight": (legs[0]["gmv"] / gross * 100.0) if gross else None}
        else:
            pct, fill = aggregate_split([(g, a, tg) for g, a, tg, _, _ in leg_calc])
            accts = "+".join(dict.fromkeys(a for _, _, _, a, _ in leg_calc))
            srcs = {s for _, _, _, _, s in leg_calc}
            agg[t] = {"acct": accts, "acct_pct": pct, "fill": fill,
                      "bucket": fill_bucket(fill), "tgt": None,
                      "tgt_src": ("agg" if len(srcs) > 1 or None in srcs
                                  else srcs.copy().pop()),
                      "pl": pl, "multi": True,
                      "weight": (sum(x["gmv"] for x in legs) / gross * 100.0)
                                if gross else None}

    # target-sum sanity (FIX 1): Σ target dollars vs Σ account totals
    total_base = sum(acct_total.values())
    target_sum_pct = (tgt_dollars_sum / total_base * 100.0) if total_base else None

    descr_map = {t: max((x["descr"] or "" for x in legs), key=len)
                 for t, legs in per.items()}
    return {"agg": agg, "per_acct": per_rows, "cash_equiv": cash_equiv,
            "gross": gross, "target_sum_pct": target_sum_pct,
            "guessed": sorted(guessed), "fi_routed": sorted(fi_routed),
            "descr": descr_map}


# ═══════════════════════ Telegram (stage -> CONFIRM TARGET) ═════════════════

def _bs_get(key):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT value FROM bot_state WHERE key=%s", (key,))
        r = cur.fetchone()
        return r[0] if r and r[0] else None


def _bs_set(key, val):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO bot_state (key,value,updated_at) "
                    "VALUES (%s,%s,NOW()) ON CONFLICT (key) DO UPDATE "
                    "SET value=EXCLUDED.value, updated_at=NOW()", (key, val))
        c.commit()


def _age_min(p) -> float:
    try:
        staged = datetime.fromisoformat(p["staged_at"])
        return (datetime.now(timezone.utc) - staged).total_seconds() / 60.0
    except Exception:
        return TTL_MIN + 1


def set_cash_equivalent(ticker: str, flag: bool) -> str:
    """Gated write path (also used by the apply script's --commit seed)."""
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("""INSERT INTO ticker_tags (ticker, cash_equivalent)
                       VALUES (%s, %s)
                       ON CONFLICT (ticker) DO UPDATE
                         SET cash_equivalent = EXCLUDED.cash_equivalent""",
                    (ticker.upper(), 1 if flag else 0))
        c.commit()
    # cash_equivalent is a classifier input (asset_class 'cash') and the cap
    # memoises tags per process. This command is reachable from the long-running
    # Telegram bot, so the memo must be dropped here too.
    try:
        from tools.asset_classifier import clear_cache
        clear_cache()
    except Exception:
        pass
    return (f"✅ {ticker.upper()} flagged cash-equivalent — excluded from "
            f"fills/exposure, counts as parked cash." if flag else
            f"✅ {ticker.upper()} un-flagged — back to being a position.")


def _commit(p) -> str:
    import db_pg
    if p["op"] in ("casheq", "nocasheq"):
        return set_cash_equivalent(p["ticker"], p["op"] == "casheq")
    with db_pg.get_conn() as c, c.cursor() as cur:
        if p["op"] == "del":
            cur.execute("DELETE FROM position_targets WHERE ticker=%s "
                        "AND account=%s", (p["ticker"], p["account"]))
            n = cur.rowcount
            c.commit()
            return (f"✅ TARGET removed: {p['ticker']}/{p['account']}"
                    if n else f"🛑 no explicit target for "
                              f"{p['ticker']}/{p['account']} — nothing removed")
        cur.execute("""INSERT INTO position_targets
                         (ticker, account, target_pct, set_date, note)
                       VALUES (%s,%s,%s,CURRENT_DATE,%s)
                       ON CONFLICT (ticker, account) DO UPDATE SET
                         target_pct=EXCLUDED.target_pct,
                         set_date=EXCLUDED.set_date, note=EXCLUDED.note""",
                    (p["ticker"], p["account"], p["pct"], p.get("note")))
        c.commit()
    return (f"✅ TARGET set: {p['ticker']} = {p['pct']:g}% of {p['account']}"
            + (f" ({p['note']})" if p.get("note") else ""))


def _list_reply() -> str:
    import db_pg
    rows = sorted(get_targets().items())
    with db_pg.get_conn() as c, c.cursor() as cur:
        ce = sorted(get_cash_equivalents(cur))
    lines = ["🎯 Explicit position targets (identity facts, operator-set):"]
    if not rows:
        lines.append("  none — every name uses tier defaults")
    for (t, a), v in rows:
        lines.append(f"  {t:<8} {a:<5} {v['pct']:g}%  since {v['set_date']}"
                     + (f"  {v['note']}" if v["note"] else ""))
    lines.append("💵 cash-equivalents (parked, excluded from fills): "
                 + (" ".join(ce) or "none"))
    lines.append("defaults: fi 10 · core 4 · eq 2 · sat 1 (shorts→sat) — "
                 "printed ·dflt-*; explicit rows override")
    lines.append("TARGET <tkr> <pct> [IND|RIRA|ROTH] · TARGET CASHEQ <tkr> "
                 "· then CONFIRM TARGET")
    return "\n".join(lines)


def handle_target_command(text):
    """Telegram TARGET branch. Stages writes; commits ONLY on the literal
    `CONFIRM TARGET` (15-min TTL). Returns None if not a TARGET message."""
    if not text:
        return None
    up = text.strip().upper()

    if up == "CONFIRM TARGET":
        raw = _bs_get(PENDING_KEY)
        p = json.loads(raw) if raw else None
        if not p:
            return "🛑 no TARGET staged — nothing to confirm."
        if _age_min(p) > TTL_MIN:
            _bs_set(PENDING_KEY, "")
            return "⏱️ TARGET staging expired (>15 min) — re-send it."
        _bs_set(PENDING_KEY, "")
        return _commit(p)

    if up == "CANCEL TARGET":
        raw = _bs_get(PENDING_KEY)
        _bs_set(PENDING_KEY, "")
        return "TARGET staging cancelled." if raw else None

    q = parse_target_command(text)
    if q is None:
        return None
    if "error" in q:
        return f"🛑 TARGET: {q['error']}"
    if q["op"] == "list":
        return _list_reply()
    q["staged_at"] = datetime.now(timezone.utc).isoformat()
    _bs_set(PENDING_KEY, json.dumps(q))
    if q["op"] == "del":
        return (f"🎯 Remove explicit target {q['ticker']}/{q['account']}? "
                f"Reply CONFIRM TARGET ({TTL_MIN} min).")
    if q["op"] == "casheq":
        return (f"💵 Flag {q['ticker']} as CASH-EQUIVALENT (parked, excluded "
                f"from fills/exposure)? Reply CONFIRM TARGET ({TTL_MIN} min).")
    if q["op"] == "nocasheq":
        return (f"💵 Un-flag {q['ticker']} (back to a position)? "
                f"Reply CONFIRM TARGET ({TTL_MIN} min).")
    return (f"🎯 Set {q['ticker']} target = {q['pct']:g}% of {q['account']}"
            + (f" ({q['note']})" if q.get("note") else "")
            + f"? Reply CONFIRM TARGET ({TTL_MIN} min).")
