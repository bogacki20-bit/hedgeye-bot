"""Underlying-linkage for wrapper ETFs + an unmapped-wrapper DETECTOR.

Inverse/levered single-name & index ETFs (METD->META, SQQQ->QQQ) map to their tracked
underlying (wrapper_links table). The detector scans each book snapshot's Fidelity
descriptions for wrapper naming ("DAILY", "BEAR", "ULTRAPRO", "-2X", …) and flags NEW
tickers with a wrapper name but no linkage row — PROPOSING a likely mapping for operator
CONFIRM. Nothing auto-writes: a CONFIRM writes to wrapper_links; a reject writes to
wrapper_no_mapping so it stops nagging. Basket/thematic funds (no wrapper naming) never
flag — they're their own exposure. Python owns it; no LLM.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Wrapper naming — presence of any = a single-name/index/levered/inverse wrapper.
# Deliberately excludes bare "SHORT" (ambiguous with short-DURATION bond funds);
# ULTRASHORT/ULTRAPRO still catch the inverse ProShares.
_WRAP_RE = re.compile(r"\bDAILY\b|\bULTRAPRO\b|\bULTRASHORT\b|\bULTRA\b|\bLEVERAGED\b"
                      r"|\bINVERSE\b|\bBEAR\b|\bBULL\b|[+-]?\d(?:\.\d)?X\b")
_INV_RE  = re.compile(r"\bBEAR\b|\bULTRASHORT\b|\bINVERSE\b|\bSHORT\b")
_LEV_RE  = re.compile(r"[+-]?(\d(?:\.\d)?)X\b")

# Non-ticker underlying names that appear in descriptions -> canonical symbol.
_ALIAS = {"BITCO": "BTCUSD", "BITCOIN": "BTCUSD", "ETHER": "ETHUSD", "ETHEREUM": "ETHUSD",
          "SOLANA": "SOLUSD", "EURO": "FXE", "YEN": "FXY", "NASDAQ": "QQQ"}
_INDEX = {"QQQ", "SPY", "IWM", "DIA", "MDY"}
_CRYPTO = {"BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "AVAXUSD"}
_CURRENCY = {"FXE", "FXY", "FXB", "FXF", "UUP"}


def _q(sql, args=None, fetch=True):
    import db_pg
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(sql, args or ())
        rows = cur.fetchall() if fetch else None
        c.commit()
    return rows


def get_links() -> dict:
    """{wrapper: {underlying, inverse, leverage, fund_class, note}} from wrapper_links."""
    out = {}
    for w, u, inv, lev, cls, note in _q(
            "SELECT wrapper, underlying, inverse, leverage, fund_class, note FROM wrapper_links"):
        out[w] = {"underlying": u, "inverse": inv, "leverage": lev,
                  "fund_class": cls, "note": note}
    return out


def _no_mapping() -> set:
    return {r[0] for r in _q("SELECT ticker FROM wrapper_no_mapping")}


def _fund_class(underlying) -> str | None:
    if not underlying:
        return None
    if underlying in _CRYPTO:   return "crypto"
    if underlying in _CURRENCY: return "currency"
    if underlying in _INDEX:    return "index"
    return "equity"


def propose_mapping(symbol, description, known_tickers) -> dict | None:
    """From a Fidelity description, propose {wrapper, underlying, inverse, leverage,
    fund_class, note}. Returns None if the description has no wrapper naming. underlying
    is None when it can't be extracted (operator supplies it on CONFIRM)."""
    if not description:
        return None
    d = description.upper()
    if not _WRAP_RE.search(d):
        return None
    inverse = bool(_INV_RE.search(d))
    m = _LEV_RE.search(d)
    leverage = float(m.group(1)) if m else None
    underlying = None
    for tok in re.findall(r"\b[A-Z]{2,6}\b", d):
        if tok == symbol.upper():
            continue
        if tok in _ALIAS:
            underlying = _ALIAS[tok]; break
        if tok in known_tickers:
            underlying = tok; break
    return {"wrapper": symbol.upper(), "underlying": underlying, "inverse": inverse,
            "leverage": leverage, "fund_class": _fund_class(underlying),
            "note": description.strip()}


def detect_unmapped_wrappers(positions=None) -> list:
    """Wrapper-named tickers with NO linkage row and not dismissed. Scans `positions`
    (iterable of (symbol, description) — the just-parsed export) when given, else the
    latest book snapshot in the DB. Read-only. Returns a list of proposal dicts."""
    known = {r[0].upper() for r in _q("SELECT ticker FROM ticker_tags") if r[0]}
    linked = set(get_links())
    skip = linked | _no_mapping()
    if positions is None:
        rows = _q("SELECT DISTINCT symbol, description FROM book_positions "
                  "WHERE snapshot_date=(SELECT max(snapshot_date) FROM book_positions) "
                  "AND asset_class <> 'cash'")
    else:
        rows = [(p.get("symbol") if isinstance(p, dict) else p[0],
                 p.get("description") if isinstance(p, dict) else p[1]) for p in positions]
    out = []
    for sym, desc in rows:
        if not sym or sym.upper() in skip:
            continue
        p = propose_mapping(sym, desc, known)
        if p:
            out.append(p)
    return sorted(out, key=lambda p: p["wrapper"])


def _fmt_proposal(p) -> str:
    arrow = "→" if not p["inverse"] else "→⁻"   # ⁻ marks inverse
    u = p["underlying"] or "?"
    lev = f" {p['leverage']:g}x" if p["leverage"] else ""
    inv = " inverse" if p["inverse"] else ""
    return f"{p['wrapper']} {arrow} {u}{lev}{inv} [{p['fund_class'] or '?'}]"


def detector_summary_line(positions=None) -> str | None:
    """One-line 'possible unmapped wrappers' for the ingest summary / operator checklist,
    or None when there are none. Pass the just-parsed positions during ingest."""
    props = detect_unmapped_wrappers(positions)
    if not props:
        return None
    return ("🔗 Possible unmapped wrappers (" + str(len(props)) + ") — CONFIRM to link: "
            + "; ".join(_fmt_proposal(p) for p in props)
            + "  (reply `WRAP OK <tkr>` to link / `WRAP NO <tkr>` to dismiss)")


# ─────────────────────────── writes (operator CONFIRM only) ───────────────────────────

def confirm_mapping(wrapper, underlying, inverse, leverage=None, fund_class=None,
                    note=None) -> dict:
    _q("""INSERT INTO wrapper_links (wrapper, underlying, inverse, leverage, fund_class,
             note, source_description)
          VALUES (%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (wrapper) DO UPDATE SET underlying=EXCLUDED.underlying,
             inverse=EXCLUDED.inverse, leverage=EXCLUDED.leverage,
             fund_class=EXCLUDED.fund_class, note=EXCLUDED.note, confirmed_at=now()""",
       (wrapper.upper(), underlying.upper(), inverse, leverage,
        fund_class or _fund_class(underlying.upper()), note, note), fetch=False)
    return {"wrapper": wrapper.upper(), "underlying": underlying.upper(), "inverse": inverse}


def dismiss(ticker, reason="own exposure / not a single-name wrapper") -> None:
    _q("INSERT INTO wrapper_no_mapping (ticker, reason) VALUES (%s,%s) "
       "ON CONFLICT (ticker) DO UPDATE SET reason=EXCLUDED.reason, dismissed_at=now()",
       (ticker.upper(), reason), fetch=False)


# ─────────────────────────── flip-watch (held wrappers, underlying trend) ───────────

_FLIP_LASTRUN_KEY = "wrapper_flip_lastrun"     # ET date — once/day throttle
_UTREND_KEY = "wrapper_utrend:"                # + wrapper -> last-seen underlying trend
_INV = {"BULLISH": "BEARISH", "BEARISH": "BULLISH", "NEUTRAL": "NEUTRAL"}


def _bs_get(key):
    r = _q("SELECT value FROM bot_state WHERE key=%s", (key,))
    return r[0][0] if r and r[0][0] else None


def _bs_set(key, val):
    _q("INSERT INTO bot_state (key,value,updated_at) VALUES (%s,%s,NOW()) "
       "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()",
       (key, val), fetch=False)


def _underlying_trend(u) -> str | None:
    """The underlying's trend from the full signal stack (Hedgeye RR > BTC Quant > MFR),
    via the screener helper so it matches the gate and covers names not in v_screener
    (FXE/FXY). Read-only."""
    try:
        from tools.screener import _underlying_trends
        return _underlying_trends([u]).get((u or "").upper())
    except Exception as e:
        log.warning("underlying trend lookup failed for %s: %s", u, e)
        return None


def check_wrapper_flips(persist=True) -> list:
    """For each HELD wrapper, compare its underlying's trend now vs last-seen; return a
    flip event when it changed. Inverted-adjusted for the wrapper. Persists the new
    trend (so a flip fires once) when persist=True."""
    links = get_links()
    if not links:
        return []
    held = {r[0].upper() for r in _q(
        "SELECT DISTINCT symbol FROM book_positions "
        "WHERE snapshot_date=(SELECT max(snapshot_date) FROM book_positions) "
        "AND asset_class <> 'cash'")}
    events = []
    for w, lk in links.items():
        if w not in held:
            continue
        u, inv = lk["underlying"], lk["inverse"]
        now = _underlying_trend(u)
        if not now:
            continue
        last = _bs_get(_UTREND_KEY + w)
        if last and last != now:
            eff = _INV[now] if inv else now
            events.append({"wrapper": w, "underlying": u, "inverse": inv,
                           "from": last, "to": now, "wrapper_trend": eff,
                           "msg": (f"🔄 {u} trend {last}→{now} → {w}"
                                   f"{' (inverse)' if inv else ''} now {eff}"
                                   + (f" — short-{u} thesis {'confirmed' if eff=='BULLISH' else 'weakening'}"
                                      if inv else ""))})
        if persist and now != last:
            _bs_set(_UTREND_KEY + w, now)
    return events


def run_flip_watch(force=False) -> str:
    """Once/day (ET) — squawk any held-wrapper underlying-trend flips. No writes but the
    throttle + the per-wrapper last-trend. Caller (main.py daemon) gates on the hour."""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        today = datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        today = datetime.datetime.utcnow().date().isoformat()
    if not force and _bs_get(_FLIP_LASTRUN_KEY) == today:
        return "skip:ran-today"
    _bs_set(_FLIP_LASTRUN_KEY, today)
    events = check_wrapper_flips(persist=True)
    if not events:
        return "skip:no-flips"
    try:
        from notifier import send_telegram
        send_telegram("Wrapper flip", "\n".join(e["msg"] for e in events), priority=2)
    except Exception as e:
        log.warning("wrapper flip squawk failed: %s", e)
        return f"error:{e}"
    return f"sent:{len(events)}"


def handle_wrapper_command(text):
    """Telegram: WRAP [LIST] / WRAP OK <tkr> [underlying] [inverse|long] / WRAP NO <tkr>.
    Confirms or dismisses a detector proposal. Returns None if not a WRAP command."""
    if not text:
        return None
    parts = text.strip().split()
    if not parts or parts[0].upper() != "WRAP":
        return None
    props = {p["wrapper"]: p for p in detect_unmapped_wrappers()}

    if len(parts) == 1 or parts[1].upper() == "LIST":
        if not props:
            return "🔗 No unmapped wrappers — every book wrapper is linked or dismissed."
        return ("🔗 Unmapped wrappers:\n" + "\n".join("  " + _fmt_proposal(p) for p in props.values())
                + "\n`WRAP OK <tkr>` to link (uses the proposal; add `<underlying> inverse|long` to override), "
                  "`WRAP NO <tkr>` to dismiss.")

    action = parts[1].upper()
    if len(parts) < 3:
        return "🔗 Usage: `WRAP OK <ticker> [underlying] [inverse|long]` or `WRAP NO <ticker>`."
    tkr = parts[2].upper()

    if action == "NO":
        dismiss(tkr, " ".join(parts[3:]) or "operator-dismissed")
        return f"🔗 Dismissed {tkr} — won't flag again."
    if action == "OK":
        p = props.get(tkr)
        underlying = parts[3].upper() if len(parts) > 3 and parts[3].upper() not in ("INVERSE", "LONG") \
            else (p["underlying"] if p else None)
        if not underlying:
            return f"🔗 {tkr}: no underlying proposed — supply one: `WRAP OK {tkr} <underlying> inverse|long`."
        if "INVERSE" in [x.upper() for x in parts[3:]]:
            inverse = True
        elif "LONG" in [x.upper() for x in parts[3:]]:
            inverse = False
        else:
            inverse = p["inverse"] if p else False
        r = confirm_mapping(tkr, underlying, inverse,
                            leverage=(p or {}).get("leverage"),
                            note=(p or {}).get("note"))
        return f"✅ Linked {r['wrapper']} → {r['underlying']}{' (inverse)' if r['inverse'] else ''}."
    return "🔗 Usage: `WRAP OK <ticker> …` or `WRAP NO <ticker>`."
