"""tools/rp_resolve.py — THE range-position resolver. One helper, one truth.

2026-08-26 (the HYG 1.29-vs-0.89 defect): MFR publishes positionOnRange for
both tiers and the bot re-derived rp from whichever band a view coalesced —
sometimes Hedgeye's narrower trade band — with nothing to check it against.
Resolution order, never silently falling through a tier:

  1. MFR's PUBLISHED short-term position (fresh)  -> 'mfr-published'
  2. derived from a band ('derived-hdg' when the Hedgeye override supplied
     the band, else 'derived-mfr')
  3. the shadow engine's band                      -> 'shadow'
  4. the wrapper's underlying, inverted if inverse -> 'wrapper'
  5. nothing                                       -> (None, None): DARK

rp is NEVER clamped to [0,1]: above 1 / below 0 is real information — price
outside the band — and is LABELLED by the zone logic, not hidden.

2026-09-06 (rp frozen at fetch-time price): tier 1 is gated. The published
position is computed by the vendor from the price AT FETCH TIME, so on any
surface that just recomputed rp from a live quote (rows carrying
_rp_stale=False) the live derived value wins and the published one is only
a fallback — displayed with a stale marker by the callers.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger("rp_resolve")

PUBLISHED_MAX_AGE_DAYS = 5      # a stale published value is stale, not tier 1
DIVERGENCE_THRESHOLD = 0.05     # published vs derived beyond this = alarm

# Band width below 2% of price: a routine half-percent move swings rp across
# most of the scale (HYG's whole band is ~0.6% of price), so rp there is
# noise wearing a signal's clothes — such rows are excluded from trim/add/
# cover candidate lists (they still PRINT, tagged LOW-SIGNAL).
LOW_SIGNAL_BAND_PCT = 0.02

# Zone boundaries (E2, 2026-08-26). Inclusive at 0.80/1.00 and 0.00/0.20.
ZONE_BREAKOUT = "BREAKOUT"
ZONE_NEAR_TOP = "NEAR TOP"
ZONE_MID = "MID"
ZONE_NEAR_BOTTOM = "NEAR BOTTOM"
ZONE_BREAKDOWN = "BREAKDOWN"

# short display tags — every surface that prints an rp prints one of these
SRC_TAG = {"mfr-published": "mfr", "derived-mfr": "drv", "derived-hdg": "hdg",
           "shadow": "shd", "wrapper": "wrap"}


# ─────────────────────────── pure logic ───────────────────────────

def resolve_rp(published=None, derived=None, derived_src=None,
               shadow=None, wrapper=None) -> tuple:
    """Pure. (rp, rp_source) by the five-tier order. derived_src names which
    band produced `derived` ('derived-mfr' or 'derived-hdg'); it defaults to
    'derived-mfr'."""
    if published is not None:
        return float(published), "mfr-published"
    if derived is not None:
        return float(derived), (derived_src or "derived-mfr")
    if shadow is not None:
        return float(shadow), "shadow"
    if wrapper is not None:
        return float(wrapper), "wrapper"
    return None, None


def divergence(published, derived,
               threshold=DIVERGENCE_THRESHOLD):
    """Pure. |published - derived| when both exist and the gap exceeds the
    threshold; None otherwise. The check that would have caught HYG on day
    one."""
    if published is None or derived is None:
        return None
    d = abs(float(published) - float(derived))
    # 1e-9 guard: 0.55 - 0.50 is 0.050000000000000044 in floats — exactly
    # AT the threshold must not fire.
    return d if d > threshold + 1e-9 else None


def zone(rp) -> str | None:
    """Pure. The five zones at their exact boundaries. None for no rp."""
    if rp is None:
        return None
    rp = float(rp)
    if rp > 1.00:
        return ZONE_BREAKOUT
    if rp >= 0.80:
        return ZONE_NEAR_TOP
    if rp > 0.20:
        return ZONE_MID
    if rp >= 0.00:
        return ZONE_NEAR_BOTTOM
    return ZONE_BREAKDOWN


def verdict(z, side) -> str | None:
    """Pure. Candidate verdict per zone AND side (E2): for LONGS the top is
    trim and the bottom is add; for SHORTS the sense inverts — top means the
    short is being run over (ADD zone per the operator's spec) and bottom
    means it worked (COVER). Shorts previously got no verdict at all; SUJA
    sat at 1.02 short and appeared nowhere."""
    if z is None:
        return None
    if side == "long":
        if z in (ZONE_BREAKOUT, ZONE_NEAR_TOP):
            return "trim"
        if z in (ZONE_NEAR_BOTTOM, ZONE_BREAKDOWN):
            return "add"
        return None
    if side == "short":
        if z in (ZONE_BREAKOUT, ZONE_NEAR_TOP):
            return "add"
        if z in (ZONE_NEAR_BOTTOM, ZONE_BREAKDOWN):
            return "cover"
        return None
    return None


def is_low_signal(range_low, range_high, price,
                  min_pct=LOW_SIGNAL_BAND_PCT) -> bool:
    """Pure. True when the band is under min_pct of price — see the
    LOW_SIGNAL_BAND_PCT comment. Unknown inputs are NOT low-signal (they are
    dark or unmeasurable, a different statement)."""
    if range_low is None or range_high is None or not price:
        return False
    try:
        return (float(range_high) - float(range_low)) / abs(float(price)) \
            < min_pct
    except (TypeError, ValueError, ZeroDivisionError):
        return False


# ─────────────────────────── DB-backed assembly ───────────────────────────

def published_map(tickers) -> dict:
    """{ticker: (pos_short, pos_long, snapshot_date)} from each ticker's
    newest mfr_snapshots row, FRESHNESS-GATED to PUBLISHED_MAX_AGE_DAYS —
    a stale published value must not outrank a live derived one."""
    if not tickers:
        return {}
    import db_pg
    out = {}
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ON (ticker) ticker, mfr_pos_short,
                          mfr_pos_long, snapshot_date
                   FROM mfr_snapshots WHERE ticker = ANY(%s)
                   ORDER BY ticker, snapshot_date DESC""",
                (sorted({str(t).upper() for t in tickers}),))
            today = date.today()
            for t, ps, pl, sd in cur.fetchall():
                if sd is None or (today - sd).days > PUBLISHED_MAX_AGE_DAYS:
                    continue
                out[t] = (float(ps) if ps is not None else None,
                          float(pl) if pl is not None else None, sd)
    except Exception as e:
        log.warning("published_map unavailable (%s) — derived values stand", e)
    return out


def apply_rp_resolution(rows, record=True) -> list:
    """Mutate slice-shaped row dicts in place: set range_pos / rp_source /
    rp_lt via the five-tier order, and (record=True) file+squawk divergences.
    Rows keep their derived value in _rp_derived so nothing is erased.
    Returns the divergence list [(ticker, published, derived, delta)].

    LIVE gate (2026-09-06, the rp-does-not-move-with-price defect): a row
    whose range_pos was just recomputed from a LIVE quote (_rp_stale is
    False — SCREEN's _refresh_range_pos_live sets it) keeps that value; the
    feed's published positionOnRange is frozen at ITS fetch-time price and
    must never outrank a quote from seconds ago. Published stays tier 1
    only where no live quote backs the derived value (rows without the flag
    — EOD report, book alerts — behave exactly as before: the 2026-08-26
    HYG ordering was about deriving from the WRONG BAND, not about
    live-vs-published freshness, and this gate never re-derives a band)."""
    pub = published_map({r.get("ticker") for r in rows if r.get("ticker")})
    divergences = []
    for r in rows:
        t = r.get("ticker")
        ps, pl, _sd = pub.get(t, (None, None, None))
        derived = r.get("range_pos")
        derived = float(derived) if derived is not None else None
        if r.get("_rsrc") == "shd":
            shadow_rp, derived_now, derived_src = derived, None, None
        else:
            shadow_rp = None
            derived_now = derived
            derived_src = ("derived-hdg" if r.get("band_source") == "hdg"
                           else "derived-mfr")
        live_derived = (derived_now is not None
                        and r.get("_rp_stale") is False)
        rp, src = resolve_rp(published=None if live_derived else ps,
                             derived=derived_now,
                             derived_src=derived_src, shadow=shadow_rp,
                             wrapper=r.get("_wrapper_rp"))
        r["_rp_derived"] = derived
        r["range_pos"], r["rp_source"] = rp, src
        r["rp_lt"] = pl
        d = divergence(ps, derived)
        if d is not None:
            divergences.append((t, ps, derived, d))
    if record and divergences:
        record_divergences(divergences)
    return divergences


def record_divergences(divergences) -> None:
    """File each divergence (PK dedups per ticker per day) and squawk ONCE
    per ticker per day — only a fresh insert sends. Best-effort; never
    raises into a render path."""
    import db_pg
    fresh = []
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for t, pub, der, delta in divergences:
                cur.execute(
                    """INSERT INTO rp_divergence
                         (seen_on, ticker, published, derived, delta)
                       VALUES (CURRENT_DATE, %s, %s, %s, %s)
                       ON CONFLICT (seen_on, ticker) DO NOTHING""",
                    (t, pub, der, delta))
                if cur.rowcount:
                    fresh.append((t, pub, der, delta))
            conn.commit()
    except Exception as e:
        log.warning("rp divergence record failed: %s", e)
        return
    if not fresh:
        return
    try:
        from notifier import send_telegram
        lines = [f"{t}: published {p:.2f} vs derived {d:.2f} (delta {x:.2f})"
                 for t, p, d, x in sorted(fresh, key=lambda r: -r[3])[:15]]
        send_telegram("RP DIVERGENCE",
                      f"{len(fresh)} name(s) where MFR's published range "
                      f"position disagrees with the derived one by more than "
                      f"{DIVERGENCE_THRESHOLD}:\n" + "\n".join(lines)
                      + "\nThe published value is being used. The derived "
                        "band (often a narrower Hedgeye trade range) is the "
                        "one that disagrees.")
    except Exception as e:
        log.warning("rp divergence squawk failed: %s", e)


def todays_divergences() -> list:
    """[(ticker, published, derived, delta)] recorded today — the
    RP DIVERGENCE section MFR COVERAGE prints."""
    import db_pg
    try:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT ticker, published, derived, delta "
                        "FROM rp_divergence WHERE seen_on = CURRENT_DATE "
                        "ORDER BY delta DESC")
            return [(t, float(p), float(d), float(x))
                    for t, p, d, x in cur.fetchall()]
    except Exception as e:
        log.warning("rp divergence read failed: %s", e)
        return []
