"""_tag_proposals.py — full-universe tagging step 2: migration 066 +
operator-confirmable tag proposals for every untagged universe name.

Rule-based (no fetch) for futures (_F), indices (^), bare FX codes and spot
crypto. yfinance reference data — PACED, yahoo rate-limits unpaced runs —
for everything else, cached to _tag_proposals_cache.json so --commit writes
exactly what the eyeballed dry run showed (identity facts = operator gate).
cyclicality is never guessed; rate_sensitive/duration_char only from
unambiguous bond-fund keywords, else NULL for an operator pass (review=1
marks rows needing one).

    python _tag_proposals.py                 # migration 066 + DRY RUN (fetch + cache)
    python _tag_proposals.py --priority-only # held / SS-roster names only
    python _tag_proposals.py --refresh       # ignore cache, refetch
    python _tag_proposals.py --commit        # write the cached proposals
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "_tag_proposals_cache.json")
PACE_SECONDS = 1.2          # yahoo rate-limits ~300 unpaced lookups

# ══════════════════════ pure logic (fixture-tested) ═════════════════════════

# Bare FX codes that appear in the universe as their own tickers.
FX_CODES = {"EUR", "GBP", "USD", "JPY", "CHF", "CAD", "AUD"}
# Spot crypto symbols (ETF wrappers like IBIT/ETHA/SOLZ go through yfinance).
CRYPTO_SPOT = {"BTC", "ETH", "BITCOIN", "AVAX", "SOL", "ADA", "XRP", "DOGE"}

# yfinance sector names that the screener's canonical regexes don't catch.
YF_SECTOR_PREMAP = {
    "consumer cyclical":  "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "health":             "Health Care",   # yahoo ETF category vocabulary
}

# Operator-confirmed identity facts that beat reference data (yfinance had
# these wrong or empty). Merged over the fetched proposal — loud in dry run.
OPERATOR_OVERRIDES = {
    "HEFT": {"instrument": "etf",
             "subsector": "Thematic — Fourth Turning",
             "src": "operator 2026-07-12"},
}

QUOTETYPE_TO_INSTRUMENT = {
    "ETF": "etf", "EQUITY": "stock", "MUTUALFUND": "fund",
    "MONEYMARKET": "fund", "INDEX": "index", "FUTURE": "future",
    "CURRENCY": "currency", "CRYPTOCURRENCY": "crypto",
}

_BOND_RE = re.compile(
    r"\bbonds?\b|\btreasur(?:y|ies)\b|fixed[- ]income|\bt[- ]bills?\b"
    r"|\bmunicipal\b|corporate debt|\bnotes?\b.*\b(?:2|5|10|20|30)[- ]?y",
    re.I)
_DUR_LONG_RE  = re.compile(r"\b20\+|\b25\+|long[- ]term|extended duration", re.I)
_DUR_SHORT_RE = re.compile(r"\b0-3\b|\b1-3\b|short[- ]term|ultra[- ]?short"
                           r"|floating rate", re.I)
_DUR_INT_RE   = re.compile(r"\b3-7\b|\b7-10\b|intermediate", re.I)


def classify_rule_based(ticker: str):
    """(instrument, gics_sector) for names classifiable without a fetch,
    else None. Futures/indices/FX/spot-crypto never resolve via yfinance
    info, so they must not hit the fetch path."""
    t = (ticker or "").strip()
    if t.endswith("_F"):
        return ("future", None)
    if t.startswith("^"):
        return ("index", None)
    if t in FX_CODES:
        return ("currency", None)
    if t in CRYPTO_SPOT:
        return ("crypto", "Digital Assets")
    return None


def map_sector(text: str | None):
    """yfinance sector/category text -> canonical gics_sector (screener
    vocab) or None. Pre-maps the two yfinance names ('Consumer Cyclical' /
    'Consumer Defensive') the canonical regexes don't match."""
    if not text:
        return None
    pre = YF_SECTOR_PREMAP.get(text.strip().lower())
    if pre:
        return pre
    from tools.screener import _SECTORS
    low = text.lower()
    for pat, canon in _SECTORS:
        if re.search(pat, low):
            return canon
    return None


def bond_fields(*texts):
    """(rate_sensitive, duration_char) from unambiguous bond-fund keywords
    in name/category text; (None, None) otherwise — never guessed."""
    blob = " ".join(t for t in texts if t)
    if not blob or not _BOND_RE.search(blob):
        return (None, None)
    if _DUR_LONG_RE.search(blob):
        return (1, "long")
    if _DUR_SHORT_RE.search(blob):
        return (1, "short")
    if _DUR_INT_RE.search(blob):
        return (1, "intermediate")
    return (1, None)


def proposal_from_info(ticker: str, info: dict):
    """yfinance info subset -> proposal row dict (pure)."""
    qt = (info.get("quoteType") or "").upper()
    instrument = QUOTETYPE_TO_INSTRUMENT.get(qt)
    name = info.get("longName") or info.get("shortName") or ""
    sector_src = info.get("sector") or info.get("category")
    sector = map_sector(sector_src)
    if sector is None and instrument == "crypto":
        sector = "Digital Assets"
    subsector = info.get("industry") or info.get("category")
    rate_sens, dur = bond_fields(name, info.get("category"), subsector)
    return {"ticker": ticker, "instrument": instrument, "gics_sector": sector,
            "subsector": subsector, "rate_sensitive": rate_sens,
            "duration_char": dur, "name": name, "src": "yfinance",
            "raw_quotetype": qt or None, "raw_sector": sector_src}


# ═══════════════════════════════ I/O ════════════════════════════════════════

def untagged_universe(cur):
    """(priority_sorted, tail_sorted) — same universe as _tag_universe_audit."""
    cur.execute("SELECT ticker FROM ticker_tags")
    tagged = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT ticker FROM mfr_snapshots")
    universe = {r[0] for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT underlying FROM book_positions
                   WHERE snapshot_date = (SELECT max(snapshot_date)
                                          FROM book_positions)
                     AND asset_class <> 'cash'""")
    book = {r[0] for r in cur.fetchall()}
    universe |= book
    try:
        cur.execute("SELECT ticker FROM ss_roster_history "
                    "WHERE removed_on IS NULL")
        ss = {r[0] for r in cur.fetchall()}
    except Exception:
        ss = set()
    universe |= ss
    from tools.source_registry import REGISTRY
    for s in REGISTRY:
        try:
            universe |= set(s.members())
        except Exception as e:
            print(f"  (source {s.tag} unreadable: {e})")
    untagged = universe - tagged
    priority = sorted((book | ss) & untagged)
    tail = sorted(untagged - set(priority))
    return priority, tail


def yf_symbol(ticker: str) -> str:
    try:
        from price_monitor import HEDGEYE_TO_YFINANCE
        return HEDGEYE_TO_YFINANCE.get(ticker, ticker)
    except Exception:
        return ticker


def fetch_info(ticker: str):
    """Paced yfinance info fetch; returns (info_subset, err)."""
    import yfinance as yf
    sym = yf_symbol(ticker)
    for attempt in (1, 2):
        try:
            info = yf.Ticker(sym).info or {}
            keep = {k: info.get(k) for k in
                    ("quoteType", "sector", "industry", "category",
                     "longName", "shortName")}
            if not any(keep.values()):
                return None, "empty yfinance info"
            keep["_symbol_used"] = sym
            return keep, None
        except Exception as e:
            if attempt == 2:
                return None, f"{type(e).__name__}: {e}"
            time.sleep(5)
    return None, "unreachable"


def build_proposals(names, cache, refresh=False):
    rows, fetched = [], 0
    for t in names:
        rb = classify_rule_based(t)
        if rb:
            instrument, sector = rb
            rows.append({"ticker": t, "instrument": instrument,
                         "gics_sector": sector, "subsector": None,
                         "rate_sensitive": None, "duration_char": None,
                         "name": "", "src": "rule", "raw_quotetype": None,
                         "raw_sector": None})
            continue
        if t in cache and not refresh:
            entry = cache[t]
        else:
            info, err = fetch_info(t)
            fetched += 1
            time.sleep(PACE_SECONDS)
            entry = {"info": info, "err": err}
            cache[t] = entry
            if fetched % 25 == 0:
                _save_cache(cache)
                print(f"  … {fetched} fetched", flush=True)
        if entry.get("info"):
            row = proposal_from_info(t, entry["info"])
            if t in OPERATOR_OVERRIDES:
                row.update(OPERATOR_OVERRIDES[t])
            rows.append(row)
        elif t in OPERATOR_OVERRIDES:
            row = {"ticker": t, "instrument": None, "gics_sector": None,
                   "subsector": None, "rate_sensitive": None,
                   "duration_char": None, "name": "", "src": "operator",
                   "raw_quotetype": None, "raw_sector": None}
            row.update(OPERATOR_OVERRIDES[t])
            rows.append(row)
        else:
            rows.append({"ticker": t, "instrument": None, "gics_sector": None,
                         "subsector": None, "rate_sensitive": None,
                         "duration_char": None, "name": "",
                         "src": f"n/a — {entry.get('err') or 'no data'}",
                         "raw_quotetype": None, "raw_sector": None})
    _save_cache(cache)
    return rows


def _load_cache():
    if os.path.exists(CACHE):
        with open(CACHE) as f:
            return json.load(f)
    return {}


def _save_cache(cache):
    with open(CACHE, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)


def print_table(label, rows):
    print(f"\n{label} ({len(rows)}):")
    print(f"  {'TICKER':<8} {'INSTR':<9} {'GICS SECTOR':<24} "
          f"{'SUBSECTOR/CATEGORY':<34} NOTE")
    for r in rows:
        note = ""
        if r["src"].startswith("n/a"):
            note = f"🛑 {r['src']} — not written"
        elif r["instrument"] is None:
            note = (f"🛑 unmapped quoteType "
                    f"{r['raw_quotetype'] or '?'} — not written")
        elif r["instrument"] in ("etf", "stock", "fund") \
                and r["gics_sector"] is None:
            note = (f"⚠ sector unmapped (yf said: "
                    f"{r['raw_sector'] or 'nothing'}) — review=1")
        if r["duration_char"]:
            note = (note + " · " if note else "") + f"dur={r['duration_char']}"
        print(f"  {r['ticker']:<8} {r['instrument'] or '—':<9} "
              f"{(r['gics_sector'] or '—'):<24} "
              f"{(r['subsector'] or '—')[:33]:<34} {note}")


def main():
    commit = "--commit" in sys.argv
    refresh = "--refresh" in sys.argv
    priority_only = "--priority-only" in sys.argv

    import db_pg
    mig = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "migrations",
                            "066_ticker_tags_instrument.sql")).read()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(mig)
        conn.commit()
        print("migration 066 applied (ticker_tags.instrument)")
        priority, tail = untagged_universe(cur)

    names = priority if priority_only else priority + tail
    print(f"untagged: {len(priority)} priority + {len(tail)} tail — "
          f"processing {len(names)}"
          + (" (priority only)" if priority_only else ""))

    cache = _load_cache()
    rows = build_proposals(names, cache, refresh=refresh)
    pri = [r for r in rows if r["ticker"] in set(priority)]
    rest = [r for r in rows if r["ticker"] not in set(priority)]
    if pri:
        print_table("PRIORITY (held / SS roster)", pri)
    if rest:
        print_table("TAIL", rest)

    writable = [r for r in rows if r["instrument"]]
    blocked = [r for r in rows if not r["instrument"]]
    print(f"\nwritable: {len(writable)} · blocked (no instrument): "
          f"{len(blocked)}")

    if not commit:
        print("\nDry run — nothing written. If the table reads right:")
        print("    python _tag_proposals.py --commit"
              + (" --priority-only" if priority_only else ""))
        return

    wrote = skipped = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for r in writable:
            cur.execute("SELECT 1 FROM ticker_tags WHERE ticker = %s",
                        (r["ticker"],))
            if cur.fetchone():
                print(f"  {r['ticker']}: already tagged — SKIPPED "
                      f"(never overwrite operator rows)")
                skipped += 1
                continue
            review = 1 if (r["instrument"] in ("etf", "stock", "fund")
                           and r["gics_sector"] is None) else 0
            cur.execute(
                """INSERT INTO ticker_tags (ticker, gics_sector, subsector,
                       instrument, rate_sensitive, duration_char, review)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (r["ticker"], r["gics_sector"], r["subsector"],
                 r["instrument"], r["rate_sensitive"], r["duration_char"],
                 review))
            wrote += 1
        conn.commit()
    print(f"\n✅ wrote {wrote} rows · skipped {skipped} · "
          f"blocked {len(blocked)} (listed above with reasons)")
    print("review=1 rows need an operator sector pass: "
          + (" ".join(r["ticker"] for r in writable
                      if r["instrument"] in ("etf", "stock", "fund")
                      and r["gics_sector"] is None) or "none"))


if __name__ == "__main__":
    main()
# eof
