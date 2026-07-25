"""_tag_proposals.py — full-universe tagging: migrations 066 + 073 +
operator-confirmable tag proposals for every untagged universe name.

Rule-based (no fetch) for futures (_F), indices (^), bare FX codes and spot
crypto. yfinance reference data — PACED, yahoo rate-limits unpaced runs —
for everything else, cached to _tag_proposals_cache.json so --commit writes
exactly what the eyeballed dry run showed (identity facts = operator gate).
cyclicality is never guessed; rate_sensitive/duration_char only from
unambiguous bond-fund keywords, else NULL for an operator pass (review=1
marks rows needing one).

Non-GICS EXPOSURE axis (073): GICS is equity-only, so ETFs/thematics/country/
commodity funds get no sector. classify_exposure() adds a grouping axis
(single-country | commodity-proxy | crypto-proxy | volatility | broad-market)
plus orthogonal inverse / leverage_factor flags — conservative, only on
unambiguous keywords. An ETF classified on the exposure axis is NO LONGER
review=1 (it's now visible to rotation + CONC).

    python _tag_proposals.py                     # migrations + DRY RUN (fetch + cache)
    python _tag_proposals.py --priority-only     # held / SS-roster names only
    python _tag_proposals.py --refresh           # ignore cache, refetch
    python _tag_proposals.py --commit            # write the cached proposals (INSERT new names)
    python _tag_proposals.py --backfill-exposure # DRY RUN: fill exposure on already-tagged rows
    python _tag_proposals.py --backfill-exposure --commit  # apply (fills NULL exposure only)

  ETF holdings-truth labeling (label by what a fund HOLDS, not its name):
    python _tag_proposals.py --holdings-label            # DRY RUN: current -> truth diff per ETF
    python _tag_proposals.py --holdings-label --commit   # write truth labels (unrouted rows untouched)
    python _tag_proposals.py --holdings-label --refresh  # ignore holdings cache, refetch
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
    # Blocked stocks — yfinance returns empty for the bare symbol (foreign /
    # ambiguous listing). Web-confirmed 2026-07-25.
    "EXPN": {"instrument": "stock", "gics_sector": "Industrials",
             "src": "operator web 2026-07-25"},   # Experian — data/business svcs
    "FI":   {"instrument": "stock", "gics_sector": "Financials",
             "src": "operator web 2026-07-25"},   # Fiserv — payments
    "FYBR": {"instrument": "stock", "gics_sector": "Communication Services",
             "src": "operator web 2026-07-25"},   # Frontier Communications — telecom
}

# Operator-confirmed ETF labels for names holdings data CAN'T resolve — currency
# funds (yfinance returns no asset_classes) and single-stock / geared wrappers
# (asset_classes is a swap artifact). Cross-checked on issuer sites 2026-07-25.
# These BEAT the classifier in holdings_label (identity fact = operator gate).
# Keys not present: gics_sector=None, exposure=None, inverse=0, leverage_factor=None.
OPERATOR_ETF_LABELS = {
    # ── currency funds (no funds_data) ──
    "FXE": {"exposure": "currency"},                 # long euro
    "FXB": {"exposure": "currency"},                 # long pound
    "FXF": {"exposure": "currency"},                 # long swiss franc
    "FXY": {"exposure": "currency"},                 # long yen
    "UUP": {"exposure": "currency"},                 # long US dollar
    "FXC": {"exposure": "currency"},                 # long Canadian dollar
    "EUO": {"exposure": "currency", "inverse": 1, "leverage_factor": 2},  # -2x euro
    "YCS": {"exposure": "currency", "inverse": 1, "leverage_factor": 2},  # -2x yen
    # ── geared / single-stock wrappers (issuer-confirmed) ──
    "SQQQ": {"exposure": "broad-market", "inverse": 1, "leverage_factor": 3},  # -3x Nasdaq-100
    "DRIP": {"gics_sector": "Energy", "inverse": 1, "leverage_factor": 2},     # -2x oil&gas E&P
    "GGLS": {"gics_sector": "Communication Services", "inverse": 1},  # short GOOGL
    "METD": {"gics_sector": "Communication Services", "inverse": 1},  # short META
    "MSFD": {"gics_sector": "Technology", "inverse": 1},              # short MSFT
    "REW": {"gics_sector": "Technology", "inverse": 1, "leverage_factor": 2},  # -2x tech
    "MSTY": {"exposure": "crypto-proxy"},            # MSTR option-income (bitcoin driver)
    "MAGS": {"exposure": "mega-cap-core"},           # equal-weight Mag-7 core long
    "BLOK": {"exposure": "btc-sensitivity"},         # blockchain equities — btc-sensitive
    "HEFT": {"exposure": "multi-asset"},             # fund-of-ETFs (no funds_data)
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
    # Position Monitor crypto pairs: BTCUSD, SOLUSD, AVAXUSD, RUNEUSD …
    # (6+ chars ending USD — no listed equity ticker fits that shape).
    if t.endswith("USD") and len(t) >= 6:
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


# ── exposure axis (non-GICS grouping) + geared flags ────────────────────────
# Conservative: a value is returned ONLY on an unambiguous name/category
# keyword; ambiguous or silent -> None/0 and the ETF still needs an operator
# pass. inverse/leverage from fund names is best-effort — the dry-run eyeball
# and OPERATOR_OVERRIDES are the real gate. Order matters: crypto/vol beat
# commodity beat single-country beat broad-market ("Bitcoin" wins over a
# country word; a "China Internet" fund is a single-country call).
_XP_CRYPTO_RE    = re.compile(r"\b(bitcoin|ethereum|ether\b|crypto|blockchain|digital asset)\b", re.I)
_XP_VOL_RE       = re.compile(r"\b(volatility|\bvix\b)\b", re.I)
_XP_COMMODITY_RE = re.compile(
    r"\b(gold|silver|platinum|palladium|copper|crude|\boil\b|brent|\bwti\b|"
    r"natural\s?gas|gasoline|heating oil|uranium|lithium|agricultur\w*|"
    r"\bwheat\b|\bcorn\b|soybean|coffee|\bsugar\b|cocoa|cotton|cattle|"
    r"\bmetals?\b|commodit\w*|carbon allowance)\b", re.I)
_XP_COUNTRY_RE   = re.compile(
    r"\b(china|japan|india|brazil|mexico|germany|france|united kingdom|"
    r"spain|italy|canada|australia|south korea|\bkorea\b|taiwan|vietnam|"
    r"indonesia|thailand|turkey|poland|saudi|israel|south africa|nigeria|"
    r"argentina|chile|switzerland|netherlands|singapore|hong kong|"
    r"philippines|malaysia|russia|greece|egypt|peru|colombia|qatar)\b", re.I)
_XP_BROAD_RE     = re.compile(
    r"\b(s&p\s?500|total (?:stock |us )?market|russell (?:1000|2000|3000)|"
    r"nasdaq[- ]?100|dow jones industrial|msci (?:eafe|world|acwi|emerging)|"
    r"ftse (?:all|global)|total world)\b", re.I)
# inverse: only on explicit inverse tokens, or "short <index/asset>" — NOT
# bare "short", which collides with short-DURATION bond funds (e.g. ICSH,
# "iShares Ultra Short-Term Bond").
_INV_RE = re.compile(
    r"\binverse\b|\bbear\b|-[123]\s?x\b|"
    r"\bshort\s+(?:vix|s&p|spy|qqq|dow|nasdaq|russell|treasur|gold|silver|"
    r"\boil\b|crude|bitcoin|ether|bond futures|the\s|\d)",
    re.I)
_LEV_RE = re.compile(r"\b([123])\s?x\b", re.I)


def _leverage_from(low: str):
    """Geared multiple from a fund name, or None. '2x'/'3x'/'ultrapro'/'ultra'
    (but not 'ultra short', a duration term)."""
    m = _LEV_RE.search(low)
    if m:
        return float(m.group(1))
    if "ultrapro" in low:
        return 3.0
    if re.search(r"\bultra\b(?!\s*short)", low):
        return 2.0
    return None


def classify_exposure(ticker: str, info: dict):
    """(exposure, inverse, leverage_factor). Pure, no fetch. Conservative —
    None/0/None when the name/category doesn't unambiguously say. inverse and
    leverage_factor are orthogonal flags (a 2x crude fund is
    exposure='commodity-proxy', inverse=0, leverage_factor=2)."""
    blob = " ".join(str(info.get(k) or "") for k in
                    ("longName", "shortName", "category", "industry"))
    low = blob.lower()
    inverse = 1 if _INV_RE.search(low) else 0
    lev = _leverage_from(low)
    if inverse and lev is None:
        lev = 1.0                      # plain inverse fund = -1x
    exposure = None
    if _XP_CRYPTO_RE.search(low):
        exposure = "crypto-proxy"
    elif _XP_VOL_RE.search(low):
        exposure = "volatility"
    elif _XP_COMMODITY_RE.search(low):
        exposure = "commodity-proxy"
    elif _XP_COUNTRY_RE.search(low):
        exposure = "single-country"
    elif _XP_BROAD_RE.search(low):
        exposure = "broad-market"
    return (exposure, inverse, lev)


# ── holdings-truth ETF classifier (yfinance funds_data) ─────────────────────
# Built against the REAL funds_data shape (probe 2026-07-25). Label an ETF by
# what it HOLDS, not what it's named — because the name lies (HEFT "Fourth
# Turning" is actually a multi-asset fund-of-ETFs). Signals, by reliability:
#   info.sector        -> ALWAYS None for ETFs. Ignored.
#   info.category      -> always present + descriptive. 'Trading--Leveraged/
#                         Inverse ...' prefixes flag geared funds; region words
#                         flag geographic funds (the ONLY country tell).
#   asset_classes      -> {stockPosition, bondPosition, otherPosition, ...},
#                         ~sums to 1. FIRST-PASS ROUTER. stock can be <0
#                         (inverse) or >1 (levered) — use sign/thresholds.
#   sector_weightings  -> {slug: wt} for equity; {} for commodity/bond/inverse.
#                         EMPTINESS IS SIGNAL. Floats are noisy — round.
#   top_holdings       -> unreliable for routing (BOIL, a levered commodity
#                         fund, "holds" a money-market fund). Not used here.
_SECTOR_DOMINANT = 0.60      # one sector this share of an equity fund => that sector
_BOND_DOMINANT   = 0.70
_STOCK_DOMINANT  = 0.70
_COMMOD_OTHER    = 0.30      # otherPosition floor when sector_weightings is empty
_REGION_RE = re.compile(
    r"region|greater china|latin america|\beurope\b|\bpacific\b|"
    r"emerging market|single[- ]country|\bjapan\b|\bchina\b|\bbrazil\b", re.I)


def _r(x, nd=3):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return 0.0


def _sector_from_slug(slug):
    """yfinance sector_weightings slug -> canonical GICS, or None."""
    s = {"realestate": "real estate"}.get(slug, str(slug).replace("_", " "))
    return map_sector(s)


def _dominant_sector(sw):
    if not sw:
        return (None, 0.0)
    slug, wt = max(sw.items(), key=lambda kv: _r(kv[1]))
    return (slug, _r(wt))


def classify_from_holdings(ticker, category, asset_classes, sector_weightings,
                           name=""):
    """Truth-based ETF label from funds_data. PURE — takes already-fetched
    dicts, no network. Routes on asset_classes first, sector_weightings only
    for the equity branch. Returns a proposal dict (same keys the write path
    uses) plus 'why' (the evidence string for the dry-run eyeball)."""
    cat = category or ""
    catl = cat.lower()
    ac = asset_classes or {}
    sw = {k: _r(v) for k, v in (sector_weightings or {}).items() if _r(v) > 0}
    stock = _r(ac.get("stockPosition"))
    bond = _r(ac.get("bondPosition"))
    other = _r(ac.get("otherPosition"))

    # geared flags — category is the reliable tell; name confirms the multiple
    blob = f"{name} {cat}".lower()
    inverse = 1 if ("inverse" in catl or stock <= -0.05
                    or _INV_RE.search(blob)) else 0
    lev = _leverage_from(blob)
    if ("leveraged" in catl or "ultra" in blob) and lev is None:
        lev = 2.0
    if inverse and lev is None:
        lev = 1.0

    def out(gics=None, exposure=None, rate=None, dur=None, review=0, why=""):
        return {"ticker": ticker, "instrument": "etf", "gics_sector": gics,
                "exposure": exposure, "inverse": inverse,
                "leverage_factor": lev, "rate_sensitive": rate,
                "duration_char": dur, "subsector": None, "name": name,
                "src": "holdings", "review": review,
                "raw_quotetype": "ETF", "raw_sector": cat, "why": why}

    # 1. BOND — bondPosition dominant (TLT, SHY)
    if bond >= _BOND_DOMINANT:
        _, dur = bond_fields(name, cat)
        return out(exposure="fixed-income", rate=1, dur=dur,
                   why=f"bond {bond:.0%}")

    # 2. CRYPTO — spot/wrapped crypto funds: ~100% 'other' with category
    #    'Digital Assets' (IBIT/ETHA/SOLZ) or an obvious crypto name. MUST beat
    #    the commodity branch (both look like empty-sector + other-heavy) so
    #    crypto stays on its OWN axis instead of being mislabeled commodity.
    #    Keeps the 'Digital Assets' pseudo-sector; inverse/leverage flags carry
    #    through (SETH -> crypto-proxy/INV, SBIT -> crypto-proxy/2x).
    #    Gate on EMPTY sector weights: true spot/wrapped crypto holds no
    #    equities (sw={}). A blockchain-EQUITY fund (BLOK: Coinbase/MSTR/miners)
    #    has sector weights, so it falls through to the equity branch.
    if not sw and ("digital asset" in catl or _XP_CRYPTO_RE.search(blob)):
        return out(gics="Digital Assets", exposure="crypto-proxy",
                   why=f"crypto: cat='{cat}', other {other:.0%}")

    # 3. INVERSE equity — net short (SH). Underlying from the name if we can;
    #    if the name doesn't resolve (single-stock option-income funds like
    #    MSTY read as net-short), route to REVIEW rather than guess broad-market.
    if stock <= -0.05:
        exp = classify_exposure(ticker, {"longName": name, "category": cat})[0]
        if exp is None:
            return out(review=1,
                       why=f"stock {stock:+.0%} inverse, underlying unresolved")
        return out(exposure=exp, why=f"stock {stock:+.0%} (inverse {exp})")

    # 4. COMMODITY — empty sector weights + other-heavy, or category says so
    #    (GLD other=100%; USO other=43%; BOIL levered commodity, sw={})
    if not sw and (other >= _COMMOD_OTHER or "commodit" in catl):
        return out(exposure="commodity-proxy",
                   why=f"other {other:.0%}, no sector weights, cat='{cat}'")

    # 5. EQUITY — stock-dominant with sector weights (XLV, SKYY, SPY, EWZ, SOXL)
    if stock >= _STOCK_DOMINANT and sw:
        # Region wins over sector concentration: a single-country fund is a
        # COUNTRY bet even when one sector dominates it (EWY = Korea, ~61% tech).
        if _REGION_RE.search(cat):                 # EWZ, FXI, EWY — geographic
            return out(exposure="single-country",
                       why=f"equity, region cat='{cat}'")
        slug, wt = _dominant_sector(sw)
        if wt >= _SECTOR_DOMINANT:
            gics = _sector_from_slug(slug)
            return out(gics=gics, review=0 if gics else 1,
                       why=f"{slug} {wt:.0%}"
                           + ("" if gics else " — sector slug UNMAPPED"))
        if _XP_BROAD_RE.search(blob):              # SPY — a real broad index
            return out(exposure="broad-market",
                       why=f"equity broad-index cat='{cat}'")
        return out(exposure="diversified-equity",  # ARKK, TAN — multi-sector
                   why=f"equity, {len(sw)} sectors, none >= "
                       f"{_SECTOR_DOMINANT:.0%}")

    # 6. MULTI-ASSET — meaningful stock AND bond, or nothing dominant (HEFT).
    #    Honest label is 'multi-asset'; the real fix is look-through (later).
    if (stock >= 0.10 and bond >= 0.10) or (0 < (stock + bond + other)
                                            and stock < _STOCK_DOMINANT
                                            and bond < _BOND_DOMINANT):
        # A GEARED fund is not a genuine allocation fund — the mixed asset_class
        # read is a swap/leverage artifact (DRIP/GGLS/METD/MSFD). Route to
        # REVIEW; only un-geared mixes (HEFT) get the honest 'multi-asset'.
        if inverse or lev:
            return out(review=1,
                       why=f"geared, mixed stock {stock:.0%}/bond {bond:.0%}/"
                           f"other {other:.0%} — underlying unresolved")
        return out(exposure="multi-asset",
                   why=f"mixed stock {stock:.0%}/bond {bond:.0%}/other "
                       f"{other:.0%} — look-through candidate")

    # 7. UNROUTED — operator pass (loud, never a silent guess)
    return out(review=1,
               why=f"unrouted: stock {stock}/bond {bond}/other {other}, "
                   f"sector_weights={bool(sw)}, cat='{cat}'")


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
    exposure, inverse, lev = classify_exposure(ticker, info)
    return {"ticker": ticker, "instrument": instrument, "gics_sector": sector,
            "subsector": subsector, "rate_sensitive": rate_sens,
            "duration_char": dur, "exposure": exposure, "inverse": inverse,
            "leverage_factor": lev, "name": name, "src": "yfinance",
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


# ── holdings-truth labeling (funds_data) — the ETF path ─────────────────────
HCACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "_holdings_cache.json")


def _load_holdings_cache():
    if os.path.exists(HCACHE):
        with open(HCACHE) as f:
            return json.load(f)
    return {}


def _save_holdings_cache(cache):
    with open(HCACHE, "w") as f:
        json.dump(cache, f, indent=1, sort_keys=True)


def fetch_funds_data(ticker):
    """(dict category/asset_classes/sector_weightings/name) from yfinance, or
    None if the ticker isn't a fund / is unreachable. Impure — network."""
    import yfinance as yf
    sym = yf_symbol(ticker)
    try:
        tk = yf.Ticker(sym)
        info = tk.info or {}
        name = info.get("longName") or info.get("shortName") or ""
        category = info.get("category") or ""
        try:
            fd = tk.funds_data
            ac = dict(getattr(fd, "asset_classes", {}) or {})
            sw = dict(getattr(fd, "sector_weightings", {}) or {})
            if not category:
                ov = getattr(fd, "fund_overview", {}) or {}
                category = (ov or {}).get("categoryName") or ""
        except Exception:
            return None                      # not a fund (stocks have no funds_data)
        if not ac and not sw:
            return None
        return {"category": category, "asset_classes": ac,
                "sector_weightings": sw, "name": name}
    except Exception as e:
        log.warning("funds_data fetch failed for %s: %s", ticker, e)
        return None


def _label_str(d) -> str:
    """Compact one-line label for the dry-run diff."""
    parts = []
    if d.get("gics_sector"):
        parts.append(d["gics_sector"])
    if d.get("exposure"):
        parts.append(d["exposure"])
    if d.get("inverse"):
        parts.append("INV")
    lf = d.get("leverage_factor")
    if lf and float(lf) != 1.0:
        parts.append(f"{float(lf):g}x")
    return "/".join(parts) or "—"


def _operator_etf_proposal(ticker):
    """Full proposal dict from an OPERATOR_ETF_LABELS entry — operator-confirmed
    identity, always written (review=0)."""
    o = OPERATOR_ETF_LABELS[ticker]
    return {"ticker": ticker, "instrument": "etf",
            "gics_sector": o.get("gics_sector"), "exposure": o.get("exposure"),
            "inverse": o.get("inverse", 0),
            "leverage_factor": o.get("leverage_factor"),
            "rate_sensitive": o.get("rate_sensitive"),
            "duration_char": o.get("duration_char"),
            "subsector": None, "name": "", "src": "operator", "review": 0,
            "raw_quotetype": "ETF", "raw_sector": "",
            "why": "operator-confirmed (web cross-check 2026-07-25)"}


def holdings_label(commit=False, refresh=False):
    """Label every enrolled ETF from what it HOLDS (funds_data), not its name.
    Dry-run prints a current -> truth DIFF so corrections (e.g. HEFT's fake
    'Fourth Turning' theme) are loud; --commit writes the truth. Rows the
    classifier can't route (review=1) are LEFT UNTOUCHED — never nulled."""
    import db_pg
    cache = _load_holdings_cache()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        _apply_migrations(cur)
        conn.commit()
        cur.execute("SELECT ticker, gics_sector, exposure, inverse, "
                    "leverage_factor FROM ticker_tags "
                    "WHERE instrument = 'etf' ORDER BY ticker")
        rows = cur.fetchall()
    print(f"holdings-label: {len(rows)} enrolled ETFs\n")
    print(f"  {'':1} {'TICKER':<8} {'CURRENT':<26} -> {'TRUTH (from holdings)':<26} WHY")

    props, changed, skipped = [], 0, 0
    for tk, g0, e0, i0, l0 in rows:
        cur0 = {"gics_sector": g0, "exposure": e0, "inverse": i0,
                "leverage_factor": l0}
        # Operator-confirmed labels win outright — currency funds and geared
        # single-stock wrappers that holdings data can't resolve. No fetch.
        if tk in OPERATOR_ETF_LABELS:
            p = _operator_etf_proposal(tk)
            is_changed = _label_str(p) != _label_str(cur0)
            if is_changed:
                changed += 1
            print(f"  {'Δ' if is_changed else ' '} {tk:<8} "
                  f"{_label_str(cur0):<26} -> {_label_str(p):<26} {p['why']}")
            props.append(p)
            continue
        fd = cache.get(tk) if not refresh else None
        if fd is None and tk not in cache:
            fd = fetch_funds_data(tk)
            time.sleep(PACE_SECONDS)
            cache[tk] = fd
            _save_holdings_cache(cache)
        elif fd is None:
            fd = cache.get(tk)
        if not fd:
            print(f"    {tk:<8} — no funds_data (not a fund / unreachable) — skipped")
            skipped += 1
            continue
        p = classify_from_holdings(tk, fd["category"], fd["asset_classes"],
                                   fd["sector_weightings"], fd["name"])
        routed = not p["review"]
        is_changed = routed and _label_str(p) != _label_str(cur0)
        if is_changed:
            changed += 1
        flag = "Δ" if is_changed else ("?" if not routed else " ")
        print(f"  {flag} {tk:<8} {_label_str(cur0):<26} -> "
              f"{(_label_str(p) if routed else 'review — leave as-is'):<26} "
              f"{p['why']}")
        if routed:
            props.append(p)

    print(f"\n{changed} would change · {len(props) - changed} unchanged · "
          f"{len(rows) - len(props) - skipped} unrouted (left as-is) · "
          f"{skipped} not-a-fund")

    if not commit:
        print("\nDry run — nothing written. If the diff reads right:")
        print("    python _tag_proposals.py --holdings-label --commit")
        return
    wrote = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for p in props:                      # routed rows only — never null a row
            cur.execute(
                """UPDATE ticker_tags SET
                     gics_sector = %s, exposure = %s, inverse = %s,
                     leverage_factor = %s, rate_sensitive = %s,
                     duration_char = %s, review = 0
                   WHERE ticker = %s""",
                (p["gics_sector"], p["exposure"], p["inverse"],
                 p["leverage_factor"], p["rate_sensitive"],
                 p["duration_char"], p["ticker"]))
            wrote += cur.rowcount
        conn.commit()
    print(f"\n✅ wrote truth labels to {wrote} ETF rows "
          f"(subsector free-text left intact; unrouted rows untouched)")


def build_proposals(names, cache, refresh=False):
    rows, fetched = [], 0
    for t in names:
        rb = classify_rule_based(t)
        if rb:
            instrument, sector = rb
            rows.append({"ticker": t, "instrument": instrument,
                         "gics_sector": sector, "subsector": None,
                         "rate_sensitive": None, "duration_char": None,
                         "exposure": None, "inverse": 0,
                         "leverage_factor": None,
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
                   "duration_char": None, "exposure": None, "inverse": 0,
                   "leverage_factor": None, "name": "", "src": "operator",
                   "raw_quotetype": None, "raw_sector": None}
            row.update(OPERATOR_OVERRIDES[t])
            rows.append(row)
        else:
            rows.append({"ticker": t, "instrument": None, "gics_sector": None,
                         "subsector": None, "rate_sensitive": None,
                         "duration_char": None, "exposure": None, "inverse": 0,
                         "leverage_factor": None, "name": "",
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


def _needs_review(r) -> bool:
    """An ETF/stock/fund with NO grouping axis at all — neither GICS sector
    nor a non-GICS exposure — still needs an operator pass. A name classified
    on the exposure axis (commodity-proxy, single-country, …) is NOT review."""
    return (r["instrument"] in ("etf", "stock", "fund")
            and not r.get("gics_sector") and not r.get("exposure"))


def print_table(label, rows):
    print(f"\n{label} ({len(rows)}):")
    print(f"  {'TICKER':<8} {'INSTR':<9} {'GICS SECTOR':<20} "
          f"{'EXPOSURE':<15} {'SUBSECTOR/CATEGORY':<28} NOTE")
    for r in rows:
        note = ""
        if r["src"].startswith("n/a"):
            note = f"🛑 {r['src']} — not written"
        elif r["instrument"] is None:
            note = (f"🛑 unmapped quoteType "
                    f"{r['raw_quotetype'] or '?'} — not written")
        elif _needs_review(r):
            note = (f"⚠ no sector & no exposure (yf said: "
                    f"{r['raw_sector'] or 'nothing'}) — review=1")
        geared = []
        if r.get("inverse"):
            geared.append("INV")
        if r.get("leverage_factor") and float(r["leverage_factor"]) != 1.0:
            geared.append(f"{r['leverage_factor']:g}x")
        if geared:
            note = (note + " · " if note else "") + "/".join(geared)
        if r["duration_char"]:
            note = (note + " · " if note else "") + f"dur={r['duration_char']}"
        print(f"  {r['ticker']:<8} {r['instrument'] or '—':<9} "
              f"{(r['gics_sector'] or '—'):<20} {(r.get('exposure') or '—'):<15} "
              f"{(r['subsector'] or '—')[:27]:<28} {note}")


_MIGRATIONS = ("066_ticker_tags_instrument.sql",
               "073_ticker_tags_exposure.sql")


def _apply_migrations(cur):
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
    for fn in _MIGRATIONS:
        cur.execute(open(os.path.join(base, fn)).read())
    print(f"migrations applied: {', '.join(_MIGRATIONS)}")


def backfill_exposure(commit=False, refresh=False):
    """Fill the non-GICS exposure axis (+ inverse/leverage flags) on rows that
    already exist in ticker_tags but have exposure IS NULL. NEVER overwrites an
    operator-set value: the write is COALESCE-guarded and only touches rows
    whose exposure is still NULL. This is the path the 066 insert-only flow
    can't take — it skips already-present rows to protect operator edits."""
    import db_pg
    cache = _load_cache()
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        _apply_migrations(cur)
        conn.commit()
        cur.execute("SELECT ticker, instrument FROM ticker_tags "
                    "WHERE exposure IS NULL ORDER BY ticker")
        targets = cur.fetchall()
    print(f"backfill-exposure: {len(targets)} rows with exposure IS NULL")

    props = []
    for tk, instrument in targets:
        info = (cache.get(tk) or {}).get("info")
        if info is None and refresh:
            info, err = fetch_info(tk)
            time.sleep(PACE_SECONDS)
            cache[tk] = {"info": info, "err": err}
        if not info:
            continue                       # no reference data — operator pass
        exp, inv, lev = classify_exposure(tk, info)
        if exp is None and not inv and lev is None:
            continue                       # nothing unambiguous — operator pass
        props.append({"ticker": tk, "instrument": instrument, "exposure": exp,
                      "inverse": inv, "leverage_factor": lev,
                      "gics_sector": None, "subsector": None,
                      "rate_sensitive": None, "duration_char": None,
                      "src": "backfill", "raw_quotetype": None,
                      "raw_sector": None})
    _save_cache(cache)
    print_table("EXPOSURE BACKFILL (fills NULL exposure only)", props)

    if not commit:
        print("\nDry run — nothing written. If the axis reads right:")
        print("    python _tag_proposals.py --backfill-exposure --commit")
        return
    wrote = 0
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for r in props:
            cur.execute(
                """UPDATE ticker_tags
                      SET exposure = COALESCE(exposure, %s),
                          inverse = CASE WHEN COALESCE(inverse, 0) = 0
                                         THEN %s ELSE inverse END,
                          leverage_factor = COALESCE(leverage_factor, %s),
                          review = CASE WHEN %s IS NOT NULL
                                             AND gics_sector IS NULL
                                        THEN 0 ELSE review END
                    WHERE ticker = %s AND exposure IS NULL""",
                (r["exposure"], r["inverse"], r["leverage_factor"],
                 r["exposure"], r["ticker"]))
            wrote += cur.rowcount
        conn.commit()
    print(f"\n✅ backfilled {wrote} rows (exposure was NULL; "
          f"gics_sector/subsector/operator columns untouched)")


def main():
    commit = "--commit" in sys.argv
    refresh = "--refresh" in sys.argv
    priority_only = "--priority-only" in sys.argv

    if "--holdings-label" in sys.argv:
        holdings_label(commit=commit, refresh=refresh)
        return

    if "--backfill-exposure" in sys.argv:
        backfill_exposure(commit=commit, refresh=refresh)
        return

    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        _apply_migrations(cur)
        conn.commit()
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
            review = 1 if _needs_review(r) else 0
            cur.execute(
                """INSERT INTO ticker_tags (ticker, gics_sector, subsector,
                       instrument, rate_sensitive, duration_char,
                       exposure, inverse, leverage_factor, review)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (r["ticker"], r["gics_sector"], r["subsector"],
                 r["instrument"], r["rate_sensitive"], r["duration_char"],
                 r["exposure"], r["inverse"], r["leverage_factor"], review))
            wrote += 1
        conn.commit()
    print(f"\n✅ wrote {wrote} rows · skipped {skipped} · "
          f"blocked {len(blocked)} (listed above with reasons)")
    print("review=1 rows need an operator pass (no sector & no exposure): "
          + (" ".join(r["ticker"] for r in writable if _needs_review(r))
             or "none"))


if __name__ == "__main__":
    main()
# eof
