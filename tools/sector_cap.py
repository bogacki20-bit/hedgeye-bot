"""Sector concentration cap — 8% warn / 12% hard reject, EQUITY only.

WHY IT EXISTS. A book can pass every per-position check and still be one bet:
USO, UGA, XOP, OIH and HAL are five names, each inside a 6% position cap, and
collectively a single levered wager on crude. That cost the operator money and
nothing in the codebase saw it, because the only cap here is per-position and
keyed on asset class.

WHAT IT DOES
  * EQUITY positions are grouped by PM sector (ticker_tags.hedgeye_group, via
    tools.asset_classifier) and capped at 8% warn / 12% hard reject of ACCOUNT
    value.
  * NON-EQUITY routes to the doctrine asset-class ceiling (6% equity / 12%
    currency / 10% fixed income / 4% commodity), applied on the SAME
    denominator so two caps can never disagree about the size of the account.
  * Both ceilings are evaluated on every equity trade and the TIGHTER one binds;
    the verdict names which, so "why was I stopped" is never a guess.

FAIL CLOSED — the whole point.
  * asset_class == unknown            -> REFUSE
  * equity with no PM sector          -> REFUSE
  * non-equity with no doctrine ceiling -> REFUSE
  Nothing falls through both rules. This deliberately does NOT copy
  recommender.py's "Fails OPEN" pattern (:94), which skipped capping whenever
  the class was unknown and is how 88% of the book ended up governed by
  nothing. An unknown name is a refusal, not a free pass.

DENOMINATOR. portfolio.account_value() — book_positions, INCLUDING cash, the
same number the per-position cap divides by since 2026-08-16. It RAISES rather
than returning 0.0 when unresolvable, and that refusal propagates here.

ENFORCEMENT IS PER-ACCOUNT. Book-wide exposure for the same sector is computed
and displayed alongside but does NOT gate. To switch to book-level enforcement,
set ENFORCE_SCOPE = "book" below — one line.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

WARN_PCT = 8.0
REJECT_PCT = 12.0

# "account" (default) or "book". One line to flip enforcement scope; the other
# scope is still computed and shown either way.
ENFORCE_SCOPE = "account"

ALLOW, WARN, REJECT, REFUSE = "allow", "warn", "reject", "refuse"

# our asset_class -> doctrine position_sizing_caps key
_DOCTRINE_KEY = {
    "equity": "equities", "fixed_income": "fixed_income",
    "currency": "foreign_currency", "commodity": "commodities",
    "crypto": "crypto",
}


def doctrine_pct(asset_class: str, side: str = "long"):
    """Max % of account for one position of this class, or None if doctrine has
    no ceiling for it. Uses doctrine's OWN numbers, keyed by the class our
    classifier resolved — doctrine's ticker map covers only 59 tickers, so
    keying by ticker alone would leave BUXX uncapped."""
    from tools.doctrine import load_doctrine
    key = _DOCTRINE_KEY.get(asset_class)
    if not key:
        return None
    spec = (load_doctrine().get("position_sizing_caps", {}) or {}).get(key, {})
    if key == "equities":
        pct = spec.get("max_short_pct" if str(side).lower().startswith("short")
                       else "max_long_pct")
    else:
        pct = spec.get("max_pct")
    return float(pct) if pct is not None else None


def evaluate(*, ticker, side, asset_class, sector, add_dollars,
             current_position_value, current_sector_value, account_value,
             book_sector_value=None, book_total=None,
             bucket_kind="sector", bucket=None) -> dict:
    """PURE. No DB. Returns the full verdict.

    All values are DOLLARS and compared on ABSOLUTE exposure, so a short
    concentrates a sector exactly as a long does.

    bucket_kind / bucket come from the classifier and decide WHICH
    concentration rule applies:
      "sector"  -> the 8/12 sector cap, grouped by PM (or cap-only) sector
      "country" -> the same 8/12 thresholds, grouped by COUNTRY. A country fund
                   is diversified across sectors, so calling it a sector
                   concentration would be false -- but it is still one bet.
      "broad"   -> EXEMPT from concentration (multi-sector by construction).
                   Per-position and asset-class caps still apply.
      None      -> ungroupable -> REFUSE.
    Defaults keep the pre-F1 call signature working: bucket_kind="sector" with
    bucket falling back to `sector`.
    """
    if bucket_kind == "sector" and bucket is None:
        bucket = sector
    out = {"ticker": (ticker or "").upper(), "side": side,
           "asset_class": asset_class, "sector": sector,
           "bucket_kind": bucket_kind, "bucket": bucket,
           "decision": None, "binding": None, "reason": None,
           "sector_pct": None, "position_pct": None,
           "book_sector_pct": None, "scope": ENFORCE_SCOPE,
           "warn_pct": WARN_PCT, "reject_pct": REJECT_PCT}

    if not account_value or account_value <= 0:
        out.update(decision=REFUSE, binding="no_denominator",
                   reason="account value unresolvable — a percentage cap "
                          "without a denominator is not a cap")
        return out

    # ── fail closed on classification ──
    if not asset_class or asset_class == "unknown":
        out.update(decision=REFUSE, binding="unclassified_asset",
                   reason="asset class unknown for %s — REFUSED rather than "
                          "defaulted to equity (a default-to-equity assumption "
                          "is what hid $16K of fixed income)" % out["ticker"])
        return out

    add = abs(float(add_dollars or 0.0))
    pos_after = abs(float(current_position_value or 0.0)) + add
    out["position_pct"] = 100.0 * pos_after / account_value

    if book_sector_value is not None and book_total:
        out["book_sector_pct"] = 100.0 * (abs(book_sector_value) + add) / book_total

    # ── NON-EQUITY: doctrine asset-class ceiling ──
    if asset_class != "equity":
        pct = doctrine_pct(asset_class, side)
        if pct is None:
            out.update(decision=REFUSE, binding="no_asset_class_cap",
                       reason="%s resolves to asset class %r but doctrine has "
                              "no ceiling for it — REFUSED rather than allowed "
                              "to fall through both rules"
                              % (out["ticker"], asset_class))
            return out
        ceiling = account_value * pct / 100.0
        if pos_after > ceiling:
            out.update(decision=REJECT, binding="asset_class_cap",
                       reason="%s is %s: %.1f%% of account after this trade, "
                              "over the %.0f%% %s ceiling ($%.2f)"
                              % (out["ticker"], asset_class,
                                 out["position_pct"], pct, asset_class, ceiling))
        else:
            out.update(decision=ALLOW, binding="asset_class_cap",
                       reason="%s is %s: %.1f%% of account, within the %.0f%% "
                              "ceiling ($%.2f)"
                              % (out["ticker"], asset_class,
                                 out["position_pct"], pct, ceiling))
        return out

    # ── EQUITY: must be groupable ──
    # BROAD_MARKET is the ONLY exemption, and it requires explicit membership
    # in the classifier's curated set. A name that merely lacks a sector falls
    # to the REFUSE branch below, never here — otherwise the exemption becomes
    # the fail-open hatch this cap exists to remove.
    pos_pct_cap0 = doctrine_pct("equity", side)
    if bucket_kind == "broad":
        ceiling = account_value * pos_pct_cap0 / 100.0 if pos_pct_cap0 else None
        if ceiling is not None and pos_after > ceiling:
            out.update(decision=REJECT, binding="position_size",
                       reason="%s is broad-market (exempt from the "
                              "concentration cap by explicit membership) but "
                              "would be %.1f%% of account, over the %.0f%% "
                              "per-position ceiling ($%.2f)"
                              % (out["ticker"], out["position_pct"],
                                 pos_pct_cap0, ceiling))
        else:
            out.update(decision=ALLOW, binding=None,
                       reason="%s is broad-market by construction — multi-"
                              "sector, so it cannot be a concentration. Exempt "
                              "from the sector cap by EXPLICIT membership; "
                              "position size %.1f%% is within the %.0f%% "
                              "ceiling." % (out["ticker"], out["position_pct"],
                                            pos_pct_cap0 or 0))
        return out

    if not bucket:
        out.update(decision=REFUSE, binding="unclassified_sector",
                   reason="%s is an equity the cap cannot place — no sector, "
                          "not a listed broad-market fund, not a listed country "
                          "fund. REFUSED. (Exemption requires explicit "
                          "membership; lacking a sector is not enough.)"
                          % out["ticker"])
        return out

    sec_after = abs(float(current_sector_value or 0.0)) + add
    out["sector_pct"] = 100.0 * sec_after / account_value
    grouping = "country" if bucket_kind == "country" else "sector"

    # Per-position ceiling is evaluated too: at 6% long it is TIGHTER than the
    # 12% sector reject, so it often binds first. Naming the binding rule is the
    # difference between "you are too big in this name" and "you are too
    # concentrated in this sector" — different fixes.
    pos_pct_cap = doctrine_pct("equity", side)
    pos_ceiling = account_value * pos_pct_cap / 100.0 if pos_pct_cap else None
    pos_breach = pos_ceiling is not None and pos_after > pos_ceiling

    conc_binding = ("country_concentration" if bucket_kind == "country"
                    else "sector_concentration")
    if out["sector_pct"] > REJECT_PCT:
        sector_dec, sector_binding = REJECT, conc_binding
    elif out["sector_pct"] > WARN_PCT:
        sector_dec, sector_binding = WARN, conc_binding
    else:
        sector_dec, sector_binding = ALLOW, None

    # tighter of the two binds; a hard reject beats a warn
    if pos_breach and sector_dec != REJECT:
        out.update(decision=REJECT, binding="position_size",
                   reason="%s would be %.1f%% of account, over the %.0f%% "
                          "per-position ceiling ($%.2f). Sector %s sits at "
                          "%.1f%% (under the %.0f%% reject) — POSITION SIZE is "
                          "what stopped this, not sector concentration."
                          % (out["ticker"], out["position_pct"], pos_pct_cap,
                             pos_ceiling, sector, out["sector_pct"], REJECT_PCT))
    elif sector_dec == REJECT:
        out.update(decision=REJECT, binding=sector_binding,
                   reason="%s %s would be %.1f%% of account, over the "
                          "%.0f%% hard limit%s"
                          % (grouping, bucket, out["sector_pct"], REJECT_PCT,
                             " (per-position ceiling also breached)"
                             if pos_breach else ""))
    elif sector_dec == WARN:
        out.update(decision=WARN, binding=sector_binding,
                   reason="%s %s would be %.1f%% of account, over the "
                          "%.0f%% warn level (hard limit %.0f%%)"
                          % (grouping, bucket, out["sector_pct"], WARN_PCT,
                             REJECT_PCT))
    else:
        out.update(decision=ALLOW, binding=None,
                   reason="%s %s %.1f%% and position %.1f%% both within "
                          "limits" % (grouping, bucket, out["sector_pct"],
                                      out["position_pct"]))
    return out


# ─────────────────────────── DB-backed entry point ───────────────────────────

def _exposures(account_number, bucket_kind, bucket):
    """(bucket_value_in_account, book_bucket_value, book_total) — absolute
    equity exposure to the SAME concentration bucket, from the current book.

    Grouping is by (bucket_kind, bucket), so ENERGY and PHILIPPINES are
    separate pools and a country fund never inflates a sector."""
    import db_pg
    from tools.asset_classifier import classify
    acct_v = book_v = book_total = 0.0
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT max(snapshot_date) FROM book_positions")
        snap = cur.fetchone()[0]
        cur.execute("SELECT upper(underlying), account_number, "
                    "sum(market_value) FROM book_positions "
                    "WHERE snapshot_date=%s AND asset_class<>'cash' "
                    "AND COALESCE(quantity,0)<>0 GROUP BY 1,2", (snap,))
        rows = cur.fetchall()
        cur.execute("SELECT COALESCE(sum(market_value),0) FROM book_positions "
                    "WHERE snapshot_date=%s", (snap,))
        book_total = float(cur.fetchone()[0] or 0.0)
    for tk, acct, mv in rows:
        r = classify(tk)
        if (r["asset_class"] != "equity" or r.get("bucket_kind") != bucket_kind
                or r.get("bucket") != bucket):
            continue
        book_v += abs(float(mv or 0))
        if acct == account_number:
            acct_v += abs(float(mv or 0))
    return acct_v, book_v, book_total


def check_trade(ticker, side="long", add_dollars=0.0, account=None) -> dict:
    """DB-backed verdict for adding `add_dollars` of `ticker` in `account`."""
    from portfolio import account_value, UnresolvedAccountValue, \
        _name_to_account_number
    from tools.asset_classifier import classify
    import db_pg

    t = (ticker or "").strip().upper()
    r = classify(t)
    acct_no = _name_to_account_number(account) if account else None
    try:
        av = account_value(account=acct_no, ticker=None if acct_no else t)
    except UnresolvedAccountValue as e:
        return {"ticker": t, "decision": REFUSE, "binding": "no_denominator",
                "reason": str(e), "asset_class": r["asset_class"],
                "sector": r["sector"], "scope": ENFORCE_SCOPE}
    if not acct_no:
        from portfolio import _account_that_held, INDIVIDUAL_ACCOUNT
        acct_no = _account_that_held(t) or INDIVIDUAL_ACCOUNT

    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute("SELECT COALESCE(sum(market_value),0) FROM book_positions "
                    "WHERE snapshot_date=(SELECT max(snapshot_date) "
                    "FROM book_positions) AND upper(underlying)=%s "
                    "AND account_number=%s", (t, acct_no))
        cur_pos = float(cur.fetchone()[0] or 0.0)

    sec_acct, sec_book, book_total = (0.0, 0.0, 0.0)
    if r.get("bucket"):
        sec_acct, sec_book, book_total = _exposures(
            acct_no, r.get("bucket_kind"), r.get("bucket"))

    v = evaluate(ticker=t, side=side, asset_class=r["asset_class"],
                 sector=r["sector"], add_dollars=add_dollars,
                 current_position_value=cur_pos, current_sector_value=sec_acct,
                 account_value=av, book_sector_value=sec_book,
                 book_total=book_total,
                 bucket_kind=r.get("bucket_kind"), bucket=r.get("bucket"))
    v["account"] = acct_no
    v["account_value"] = av
    v["classification_basis"] = r["basis"]
    return v


def format_verdict(v: dict) -> str:
    """One ASCII line for an operator surface, including the book-wide figure."""
    tag = {ALLOW: "OK", WARN: "WARN", REJECT: "REJECT",
           REFUSE: "REFUSED"}.get(v.get("decision"), "?")
    bits = ["[%s] %s" % (tag, v.get("reason") or "")]
    if v.get("book_sector_pct") is not None and v.get("sector"):
        bits.append("book-wide %s exposure %.1f%% (enforcement is per-ACCOUNT; "
                    "set sector_cap.ENFORCE_SCOPE='book' to gate on this)"
                    % (v["sector"], v["book_sector_pct"]))
    return "  ".join(bits)
