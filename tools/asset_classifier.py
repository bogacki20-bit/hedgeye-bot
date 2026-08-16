"""Asset class + PM sector resolution for any ticker. FAILS CLOSED.

WHY (2026-08-16). The sector cap's C1 gate failed because nothing in the repo
could reliably say what a held name IS:
  * doctrine.asset_class_for is a static 59-entry map -> resolved 7 of 58 held.
  * book_positions.asset_class is 100% populated and says "equity" for EVERY
    row, including BUXX (Ultrashort Bond), CLOX (Securitized Bond) and VTIP
    (Short-Term Inflation-Protected) -- $16K of fixed income labelled equity
    because they are exchange-traded funds.
  * hedgeye_group covers ~50% and contains ZERO ETFs (the Position Monitor is
    a single-name list).
A default-to-equity assumption is what hid that $16K, so UNKNOWN is a real
answer here and is NEVER silently promoted to equity.

RESOLUTION ORDER (first hit wins):
  1. OPERATOR_OVERRIDES        - hand-edited, beats everything
  2. doctrine ticker_to_asset_class - the existing curated map
  3. ticker_tags.exposure      - explicit instrument-shape tag
  4. ticker_tags.subsector     - EXACT-MATCH tables, never substring (see below)
  5. instrument == 'stock'     - a single company share is an equity
  6. cash_equivalent == 1      - money-market / sweep
  7. UNKNOWN                   - fail closed

WHY EXACT-MATCH, NOT KEYWORDS. Substring matching on the provider vocabulary
is actively wrong, and the evidence is in the data:
  * "Com-muni-cations" contains "muni".
  * "Emerging-Markets Local-Currency Bond" contains "Currency" but is a BOND.
  * "Equity Precious Metals" and "Natural Resources" are equity MINERS, not
    commodities.
  * "Energy Limited Partnership" is an equity MLP, not energy the commodity.
Every string below was observed in ticker_tags and classified deliberately.

WHY NOT commodity_linked. That flag is set on CVX, COP, BP, DVN, FANG --
integrated and E&P oil COMPANIES. It means "commodity-linked economics", not
"is a commodity instrument". Using it for asset class would file Chevron as a
commodity. It is deliberately unused here.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

EQUITY, FIXED_INCOME, CURRENCY, COMMODITY, CASH = (
    "equity", "fixed_income", "currency", "commodity", "cash")
CRYPTO = "crypto"          # only ever from doctrine's own map
UNKNOWN = "unknown"

# ── 1. OPERATOR OVERRIDES — edit this table; it beats every derived rule ────
# ticker -> {"asset_class": ..., "sector": ...}. Either key may be omitted.
# Seeded with names the derived rules get wrong or cannot see.
OPERATOR_OVERRIDES: dict = {
    # No ticker_tags row at all, so rules 3-6 are blind. Classified by hand.
    "VCSH": {"asset_class": FIXED_INCOME},   # Vanguard Short-Term Corporate Bond
    "UDN":  {"asset_class": CURRENCY},       # Invesco DB US Dollar Bearish
    "IGV":  {"asset_class": EQUITY, "sector": "SOFTWARE"},   # iShares Software
    "QTUM": {"asset_class": EQUITY},         # Defiance Quantum — thematic equity
    "WOOD": {"asset_class": EQUITY},         # iShares Timber & Forestry
    "TSLQ": {"asset_class": EQUITY},         # inverse single-stock (TSLA) equity
    "CRAK": {"asset_class": EQUITY, "sector": "ENERGY"},     # oil REFINERS ETF
    "HDV":  {"asset_class": EQUITY},         # iShares Core High Dividend
    "GSL":  {"asset_class": EQUITY},         # single name, already SMALL CAPS
    # Oil-services single names. Verified 2026-08-16: HAL, SLB and BKR have NO
    # ticker_tags row at all and are absent from v_screener, so every derived
    # rule is blind to them and they resolved "unknown". They are plainly
    # energy equities. This is OPERATOR KNOWLEDGE, not derivation — without it
    # an energy-concentrated book would be refused as "unclassified" rather
    # than caught by the ENERGY sector cap, which is a false-confidence pass.
    "HAL": {"asset_class": EQUITY, "sector": "ENERGY"},   # Halliburton
    "SLB": {"asset_class": EQUITY, "sector": "ENERGY"},   # SLB (Schlumberger)
    "BKR": {"asset_class": EQUITY, "sector": "ENERGY"},   # Baker Hughes
    # F1e — ordinary single stocks that simply are not on the Position Monitor.
    # Same status as HAL/SLB/BKR: OPERATOR KNOWLEDGE, not derived. Both DO have
    # a ticker_tags row and a truthful subsector, but no hedgeye_group, because
    # the PM never listed them.
    "LMT":  {"asset_class": EQUITY, "sector": "INDUSTRIALS"},  # aerospace/defence
    "CBRL": {"asset_class": EQUITY, "sector": "RESTAURANTS"},  # Cracker Barrel
}

# ── 3. exposure -> asset class ─────────────────────────────────────────────
EXPOSURE_CLASS = {
    "fixed-income":       FIXED_INCOME,
    "currency":           CURRENCY,
    "commodity-proxy":    COMMODITY,
    "crypto-proxy":       CRYPTO,
    "btc-sensitivity":    CRYPTO,
    "single-country":     EQUITY,
    "diversified-equity": EQUITY,
    "broad-market":       EQUITY,
    "mega-cap-core":      EQUITY,
    # "multi-asset" is deliberately ABSENT: a genuinely mixed fund has no single
    # asset class, so it must fall through to UNKNOWN rather than be guessed.
}

# ── 4. subsector -> asset class, EXACT match, curated from observed values ──
SUBSECTOR_FIXED_INCOME = {
    "Bank Loan", "Convertibles", "Corporate Bond", "Emerging Markets Bond",
    "Emerging-Markets Local-Currency Bond", "Global Bond",
    "Global Bond-USD Hedged", "Government Mortgage-Backed Bond",
    "High Yield Bond", "Inflation-Protected Bond", "Intermediate Core Bond",
    "Long Government", "Long-Term Bond", "Preferred Stock",
    "Securitized Bond - Focused", "Short Government",
    "Short-Term Inflation-Protected Bond", "Ultrashort Bond",
}
SUBSECTOR_CURRENCY = {"Single Currency"}
SUBSECTOR_COMMODITY = {"Commodities Broad Basket", "Commodities Focused"}
# Equity funds. Included so a broad/regional/sector EQUITY ETF resolves its
# CLASS (it still gets no PM sector unless SECTOR_ETF_PM maps it).
SUBSECTOR_EQUITY = {
    "Large Blend", "Large Growth", "Large Value", "Mid-Cap Blend",
    "Mid-Cap Value", "Small Blend", "Small Growth", "Focused Region",
    "Foreign Large Blend", "Foreign Large Value", "Europe Stock",
    "Japan Stock", "India Equity", "Greater China Region",
    "Diversified Emerging Mkts", "Global Large-Stock Blend",
    "Equity Energy", "Equity Precious Metals", "Equity Digital Assets",
    "Equity Market Neutral", "Natural Resources",
    "Energy Limited Partnership", "Derivative Income",
    "Trading--Inverse Equity", "Miscellaneous Sector", "Real Estate",
    "Health", "Utilities", "Technology", "Industrials", "Financial",
    "Communications", "Consumer Defensive", "Consumer Cyclical",
    "Infrastructure", "Building Products & Equipment",
}

# ── B3. Sector/thematic ETFs that genuinely correspond to a PM sector ──────
# ONLY unambiguous correspondences. A fund whose PM sector does not exist
# (XLU -> Utilities) or that is not a sector bet at all (country, currency,
# commodity, broad market) is deliberately ABSENT and gets no sector.
SECTOR_ETF_PM = {
    "XLE": "ENERGY", "XOP": "ENERGY", "OIH": "ENERGY", "VDE": "ENERGY",
    "IEO": "ENERGY", "FCG": "ENERGY", "CRAK": "ENERGY",
    "XLV": "HEALTHCARE", "FXH": "HEALTHCARE", "IHI": "HEALTHCARE",
    "IHF": "HEALTHCARE", "XHE": "HEALTHCARE",
    "IGV": "SOFTWARE", "WCLD": "SOFTWARE",
    "XLF": "FINANCIALS", "KBE": "FINANCIALS", "KRE": "FINANCIALS",
    "IAK": "FINANCIALS", "IAI": "FINANCIALS", "IPAY": "FINANCIALS",
    "XRT": "RETAIL", "RTH": "RETAIL",
    "XLP": "CONSUMER STAPLES", "KXI": "CONSUMER STAPLES",
    "XLB": "MATERIALS", "XME": "MATERIALS", "GDX": "MATERIALS",
    "SIL": "MATERIALS",
    "XLC": "COMMUNICATIONS", "XTL": "COMMUNICATIONS",
    # F1b 2026-08-16 — sector-bet ETFs found by scanning the universe for
    # single-sector subsectors with no hedgeye_group.
    "AMLP": "ENERGY", "MLPX": "ENERGY", "TPYP": "ENERGY", "COAL": "ENERGY",
    "SMH": "GLOBAL TECH", "SOXX": "GLOBAL TECH", "MAGS": "GLOBAL TECH",
    "BUG": "GLOBAL TECH", "DRAM": "GLOBAL TECH", "TINY": "GLOBAL TECH",
    "IVES": "GLOBAL TECH",
    "ARKG": "HEALTHCARE",
    "ITA": "INDUSTRIALS", "XAR": "INDUSTRIALS", "PAVE": "INDUSTRIALS",
    "IFRA": "INDUSTRIALS", "GII": "INDUSTRIALS", "NFRA": "INDUSTRIALS",
    # New CAP-ONLY sectors: real single-sector concentrations the PM's 15
    # cannot express. These are FULL sector-cap participants, not exemptions --
    # XLU is a pure utilities bet and is exactly what this cap exists to catch.
    "XLU": "UTILITIES", "FUTY": "UTILITIES",
    "XLRE": "REAL ESTATE", "DESK": "REAL ESTATE",
    # F1d THEMATIC — concentrated bets, NOT broad, so each gets a sector.
    "QTUM": "GLOBAL TECH",   # quantum computing = semis/hardware
    "WOOD": "MATERIALS",     # timber & forestry
    "TSLQ": "INDUSTRIALS",   # -2x TSLA; TSLA is hedgeye_group=INDUSTRIALS
                             # (verified in ticker_tags, not assumed)
    # Operator ruling 2026-08-16: mapped despite the imperfect fit I flagged.
    # XLI is a broad industrial ETF vs PM INDUSTRIALS being a single-name list;
    # XLK is US tech vs PM GLOBAL TECH being global semis/hardware; JETS is
    # airlines, which sits under GLL (gaming/lodging/leisure) as travel.
    "XLI": "INDUSTRIALS", "XLK": "GLOBAL TECH", "JETS": "GLL",
}
# The PM's 15 sectors are a SINGLE-NAME EQUITY list and are not a complete
# partition of what can be held. The cap needs one, so three explicit,
# hand-curated categories sit alongside the sector map. Every one of them is
# MEMBERSHIP-BASED. None is ever inferred from "this name has no sector" --
# that inference is precisely how an exemption becomes a fail-open hatch.

# BROAD_MARKET — multi-sector BY CONSTRUCTION, so it cannot be a sector
# concentration and is EXEMPT from the sector cap. Still fully subject to the
# per-position and asset-class caps.
# EXEMPTION REQUIRES EXPLICIT MEMBERSHIP HERE. A name that merely lacks a
# sector must never land in this set; it is REFUSED instead. test_sector_cap
# asserts exactly that.
BROAD_MARKET = {
    "NOBL",   # dividend aristocrats, all sectors
    "VYM",    # high dividend yield, all sectors
    "RSP",    # equal-weight S&P 500
    "SPLV",   # low-volatility S&P 500
    "HDV",    # core high dividend
    "FAB",    # first trust multi-cap value
}

# COUNTRY — geographic concentration, NOT sector concentration. A country fund
# is diversified ACROSS sectors within one geography, so forcing it into a
# sector bucket would be a lie (which sector is EPHE?). It is still a genuine
# concentration axis, so it gets its OWN rule rather than an exemption:
# grouped by country, SAME 8%/12% thresholds as the sector cap. Same numbers
# because nothing in the operator's stated risk appetite distinguishes "one
# sector" from "one country" -- both are one bet.
COUNTRY_FUND = {
    "EPHE": "PHILIPPINES",
    "COLO": "COLOMBIA",
    "ENZL": "NEW ZEALAND",
    "EWZ":  "BRAZIL",
}

# Deliberately NOT mapped to a PM sector, with the reason. Reported, never forced.
NO_PM_EQUIVALENT = {
    "EPHE": "country fund (Philippines)", "EWZ": "country fund (Brazil)",
    "USO": "commodity (crude)", "UGA": "commodity (gasoline)",
    "FXE": "currency", "FXY": "currency", "UUP": "currency", "UDN": "currency",
}


def _tags(ticker: str) -> tuple:
    """(instrument, subsector, hedgeye_group, exposure, cash_equivalent)."""
    import db_pg
    try:
        with db_pg.get_conn() as c, c.cursor() as cur:
            cur.execute("SELECT instrument, subsector, hedgeye_group, exposure, "
                        "cash_equivalent FROM ticker_tags WHERE upper(ticker)=%s",
                        (ticker,))
            r = cur.fetchone()
            return tuple(r) if r else (None, None, None, None, None)
    except Exception as e:
        log.warning("asset_classifier: ticker_tags unreadable for %s (%s)",
                    ticker, e)
        return (None, None, None, None, None)


def classify_from(ticker, instrument=None, subsector=None, hedgeye_group=None,
                  exposure=None, cash_equivalent=None,
                  doctrine_class=None) -> dict:
    """PURE. Resolve {asset_class, sector, basis} from already-fetched signals.

    `basis` names the rule that fired, so a surprising answer is traceable to
    the line that produced it rather than being a black box.
    """
    t = (ticker or "").strip().upper()
    ov = OPERATOR_OVERRIDES.get(t) or {}

    # sector: override > hedgeye_group > sector-ETF map
    sector = ov.get("sector")
    sector_basis = "operator override" if sector else None
    if not sector and hedgeye_group:
        sector, sector_basis = str(hedgeye_group).strip().upper(), "hedgeye_group"
    if not sector and t in SECTOR_ETF_PM:
        sector, sector_basis = SECTOR_ETF_PM[t], "sector-ETF map"

    # What does the CAP group this name by? Membership-based, never inferred.
    #   ("sector", NAME)  -> the sector cap
    #   ("country", NAME) -> the country concentration rule, same thresholds
    #   ("broad", None)   -> EXEMPT from concentration (multi-sector by
    #                        construction); per-position/asset-class still apply
    #   (None, None)      -> ungroupable -> the cap REFUSES
    if t in BROAD_MARKET:
        bucket_kind, bucket = "broad", None
    elif t in COUNTRY_FUND:
        bucket_kind, bucket = "country", COUNTRY_FUND[t]
    elif sector:
        bucket_kind, bucket = "sector", sector
    else:
        bucket_kind, bucket = None, None

    if "asset_class" in ov:
        return {"asset_class": ov["asset_class"], "sector": sector,
                "basis": "operator override", "sector_basis": sector_basis,
                "bucket_kind": bucket_kind, "bucket": bucket}
    if doctrine_class:
        m = {"equities": EQUITY, "fixed_income": FIXED_INCOME,
             "foreign_currency": CURRENCY, "commodities": COMMODITY,
             "crypto": CRYPTO, "options": UNKNOWN}
        cls = m.get(doctrine_class, UNKNOWN)
        if cls != UNKNOWN:
            return {"asset_class": cls, "sector": sector,
                    "basis": "doctrine ticker_to_asset_class",
                    "sector_basis": sector_basis,
                "bucket_kind": bucket_kind, "bucket": bucket}
    exp = (exposure or "").strip().lower()
    if exp in EXPOSURE_CLASS:
        return {"asset_class": EXPOSURE_CLASS[exp], "sector": sector,
                "basis": "exposure=%s" % exp, "sector_basis": sector_basis,
                "bucket_kind": bucket_kind, "bucket": bucket}
    ss = (subsector or "").strip()
    for table, cls in ((SUBSECTOR_FIXED_INCOME, FIXED_INCOME),
                       (SUBSECTOR_CURRENCY, CURRENCY),
                       (SUBSECTOR_COMMODITY, COMMODITY),
                       (SUBSECTOR_EQUITY, EQUITY)):
        if ss in table:
            return {"asset_class": cls, "sector": sector,
                    "basis": "subsector=%r" % ss, "sector_basis": sector_basis,
                "bucket_kind": bucket_kind, "bucket": bucket}
    if (instrument or "").strip().lower() == "stock":
        return {"asset_class": EQUITY, "sector": sector,
                "basis": "instrument=stock", "sector_basis": sector_basis,
                "bucket_kind": bucket_kind, "bucket": bucket}
    if cash_equivalent == 1:
        return {"asset_class": CASH, "sector": None, "basis": "cash_equivalent",
                "sector_basis": None}
    # FAIL CLOSED. An ETF with an unrecognised subsector is NOT an equity.
    return {"asset_class": UNKNOWN, "sector": sector,
            "basis": "no rule matched (fail closed)",
            "sector_basis": sector_basis,
                "bucket_kind": bucket_kind, "bucket": bucket}


def classify(ticker: str) -> dict:
    """DB-backed. {asset_class, sector, basis, sector_basis}."""
    t = (ticker or "").strip().upper()
    from tools.doctrine import asset_class_for
    ins, ss, hg, exp, ce = _tags(t)
    return classify_from(t, instrument=ins, subsector=ss, hedgeye_group=hg,
                         exposure=exp, cash_equivalent=ce,
                         doctrine_class=asset_class_for(t))
