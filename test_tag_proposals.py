"""Fixture tests for _tag_proposals pure logic (no DB, no network).
    python test_tag_proposals.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tag_proposals import (bond_fields, classify_rule_based, map_sector,
                            proposal_from_info, classify_exposure,
                            classify_from_holdings)

FAIL = 0


def check(label, got, want):
    global FAIL
    ok = got == want
    print(f"  {'✅' if ok else '🛑'} {label}: got {got!r}"
          + ("" if ok else f" — want {want!r}"))
    if not ok:
        FAIL += 1


print("classify_rule_based:")
check("ES_F is future", classify_rule_based("ES_F"), ("future", None))
check("^VIX is index", classify_rule_based("^VIX"), ("index", None))
check("EUR is currency", classify_rule_based("EUR"), ("currency", None))
check("BITCOIN is crypto", classify_rule_based("BITCOIN"),
      ("crypto", "Digital Assets"))
check("BTC is crypto", classify_rule_based("BTC"),
      ("crypto", "Digital Assets"))
check("SPY needs fetch", classify_rule_based("SPY"), None)
check("BTCUSD is crypto (PM pair form)", classify_rule_based("BTCUSD"),
      ("crypto", "Digital Assets"))
check("RUNEUSD is crypto (PM pair form)", classify_rule_based("RUNEUSD"),
      ("crypto", "Digital Assets"))
check("IBIT needs fetch (ETF wrapper, not spot)",
      classify_rule_based("IBIT"), None)

print("map_sector (yfinance vocab -> screener canon):")
check("Consumer Cyclical premap", map_sector("Consumer Cyclical"),
      "Consumer Discretionary")
check("Consumer Defensive premap", map_sector("Consumer Defensive"),
      "Consumer Staples")
check("Healthcare", map_sector("Healthcare"), "Health Care")
check("Financial Services", map_sector("Financial Services"), "Financials")
check("Basic Materials", map_sector("Basic Materials"), "Materials")
check("Technology", map_sector("Technology"), "Technology")
check("Real Estate", map_sector("Real Estate"), "Real Estate")
check("nonsense unmapped", map_sector("Trading--Leveraged Equity"), None)
check("None safe", map_sector(None), None)

print("bond_fields (unambiguous keywords only):")
check("TLT-style long", bond_fields("iShares 20+ Year Treasury Bond ETF"),
      (1, "long"))
check("SHY-style short", bond_fields("iShares 1-3 Year Treasury Bond ETF"),
      (1, "short"))
check("IEF-style intermediate",
      bond_fields("iShares 7-10 Year Treasury Bond ETF"),
      (1, "intermediate"))
check("bond, duration unclear", bond_fields("Total Bond Market ETF"),
      (1, None))
check("equity untouched", bond_fields("Home Construction ETF"),
      (None, None))
check("empty safe", bond_fields(None, ""), (None, None))

print("proposal_from_info:")
p = proposal_from_info("QQQ", {"quoteType": "ETF",
                               "category": "Large Growth",
                               "longName": "Invesco QQQ Trust"})
check("QQQ instrument", p["instrument"], "etf")
check("QQQ sector unmapped -> None (review pass)", p["gics_sector"], None)
p = proposal_from_info("CAT", {"quoteType": "EQUITY",
                               "sector": "Industrials",
                               "industry": "Farm & Heavy Machinery",
                               "longName": "Caterpillar Inc."})
check("CAT instrument", p["instrument"], "stock")
check("CAT sector", p["gics_sector"], "Industrials")
check("CAT subsector", p["subsector"], "Farm & Heavy Machinery")
p = proposal_from_info("XLV", {"quoteType": "ETF", "category": "Health",
                               "longName": "Health Care Select Sector SPDR"})
check("XLV sector via category", p["gics_sector"], "Health Care")
p = proposal_from_info("FDRXX", {"quoteType": "MONEYMARKET",
                                 "longName": "Fidelity Government Cash "
                                             "Reserves"})
check("FDRXX instrument", p["instrument"], "fund")
p = proposal_from_info("ZZZ", {"quoteType": "WEIRD", "longName": "?"})
check("unknown quoteType -> None (blocked, loud)", p["instrument"], None)


def _xp(longname, **extra):
    return classify_exposure("X", {"longName": longname, **extra})


print("classify_exposure (non-GICS axis + geared flags):")
# underlying kind
check("USO -> commodity-proxy", _xp("United States Oil Fund LP"),
      ("commodity-proxy", 0, None))
check("GLD -> commodity-proxy", _xp("SPDR Gold Shares"),
      ("commodity-proxy", 0, None))
check("EWZ -> single-country", _xp("iShares MSCI Brazil ETF"),
      ("single-country", 0, None))
check("FXI -> single-country", _xp("iShares China Large-Cap ETF"),
      ("single-country", 0, None))
check("IBIT -> crypto-proxy", _xp("iShares Bitcoin Trust ETF"),
      ("crypto-proxy", 0, None))
check("SPY -> broad-market", _xp("SPDR S&P 500 ETF Trust"),
      ("broad-market", 0, None))
check("VTI -> broad-market", _xp("Vanguard Total Stock Market ETF"),
      ("broad-market", 0, None))
check("plain equity ETF -> None (operator pass)",
      _xp("First Trust Cloud Computing ETF"), (None, 0, None))
# geared flags — orthogonal to exposure
check("SOXL -> 3x, no inverse, exposure None",
      _xp("Direxion Daily Semiconductor Bull 3X Shares"), (None, 0, 3.0))
check("BOIL -> 2x commodity",
      _xp("ProShares Ultra Bloomberg Natural Gas"),
      ("commodity-proxy", 0, 2.0))
check("SH -> inverse S&P (-1x)",
      _xp("ProShares Short S&P500"), ("broad-market", 1, 1.0))
check("SQQQ -> inverse 3x (ultrapro short qqq)",
      _xp("ProShares UltraPro Short QQQ"), (None, 1, 3.0))
check("SVXY-ish inverse vol", _xp("ProShares Short VIX Short-Term Futures"),
      ("volatility", 1, 1.0))
# FALSE-POSITIVE guard: short-DURATION bond funds are NOT inverse/geared
check("ICSH ultra-short bond is NOT inverse/levered",
      _xp("iShares Ultra Short-Term Bond ETF"), (None, 0, None))
check("SHY 1-3yr treasury is NOT inverse",
      _xp("iShares 1-3 Year Treasury Bond ETF"), (None, 0, None))
check("empty safe", _xp(""), (None, 0, None))

def _h(ticker, cat, ac, sw, name=""):
    r = classify_from_holdings(ticker, cat, ac, sw, name)
    return (r["gics_sector"], r["exposure"], r["inverse"], r["leverage_factor"])


print("classify_from_holdings (truth from funds_data — probe 2026-07-25):")
# EQUITY, single-sector -> real GICS sector (no more review=1)
check("XLV -> Health Care (healthcare 100%)",
      _h("XLV", "Health", {"stockPosition": 1.0}, {"healthcare": 1.0}),
      ("Health Care", None, 0, None))
check("SKYY -> Technology (tech 87.7%)",
      _h("SKYY", "Technology", {"stockPosition": 0.9975},
         {"technology": 0.877, "communication_services": 0.10}),
      ("Technology", None, 0, None))
# EQUITY, diversified broad index
check("SPY -> broad-market (no dominant sector, S&P cat)",
      _h("SPY", "Large Blend", {"stockPosition": 0.9959},
         {"technology": 0.30, "financial_services": 0.13, "healthcare": 0.11},
         "SPDR S&P 500 ETF Trust"),
      (None, "broad-market", 0, None))
# EQUITY, geographic -> single-country (only category flags this)
check("EWZ -> single-country (region cat)",
      _h("EWZ", "Latin America Equity", {"stockPosition": 0.99},
         {"financial_services": 0.25, "energy": 0.20, "materials": 0.15},
         "iShares MSCI Brazil ETF"),
      (None, "single-country", 0, None))
check("FXI -> single-country (Greater China Region)",
      _h("FXI", "China Region", {"stockPosition": 0.99},
         {"consumer_cyclical": 0.30, "communication_services": 0.20,
          "financial_services": 0.18}),
      (None, "single-country", 0, None))
# COMMODITY -> empty sector weights + other-heavy
check("USO -> commodity-proxy (other 43%, sw empty)",
      _h("USO", "Commodities Focused",
         {"otherPosition": 0.385, "cashPosition": 0.615}, {}),
      (None, "commodity-proxy", 0, None))
check("GLD -> commodity-proxy (other 100%)",
      _h("GLD", "Commodities Focused", {"otherPosition": 1.0}, {}),
      (None, "commodity-proxy", 0, None))
# LEVERED / INVERSE — geared flags off category, underlying still labeled
check("BOIL -> commodity-proxy + 2x (Trading--Leveraged Commodities)",
      _h("BOIL", "Trading--Leveraged Commodities",
         {"otherPosition": 0.2, "cashPosition": 0.8}, {},
         "ProShares Ultra Bloomberg Natural Gas"),
      (None, "commodity-proxy", 0, 2.0))
check("SOXL -> Technology + 3x (Trading--Leveraged Equity)",
      _h("SOXL", "Trading--Leveraged Equity", {"stockPosition": 1.0},
         {"technology": 1.0}, "Direxion Daily Semiconductor Bull 3X Shares"),
      ("Technology", None, 0, 3.0))
check("SH -> inverse broad-market -1x (stock -100%)",
      _h("SH", "Trading--Inverse Equity",
         {"stockPosition": -1.0001, "cashPosition": 1.82}, {},
         "ProShares Short S&P500"),
      (None, "broad-market", 1, 1.0))
# BONDS -> fixed-income + duration
check("TLT -> fixed-income long (bond 99%)",
      _h("TLT", "Long-Term Bond", {"bondPosition": 0.9928}, {},
         "iShares 20+ Year Treasury Bond ETF"),
      (None, "fixed-income", 0, None))
check("SHY -> fixed-income short",
      _h("SHY", "Short-Term Bond", {"bondPosition": 0.995}, {},
         "iShares 1-3 Year Treasury Bond ETF"),
      (None, "fixed-income", 0, None))
# MULTI-ASSET fund-of-funds -> honest 'multi-asset' (the HEFT lesson)
check("HEFT -> multi-asset (stock+bond mix, holds ETFs)",
      _h("HEFT", "Tactical Allocation",
         {"stockPosition": 0.55, "bondPosition": 0.26, "otherPosition": 0.11},
         {}, "Hedgeye Fourth Turning ETF"),
      (None, "multi-asset", 0, None))
# ARKK -> diversified-equity (multi-sector, not a broad index, not a region)
check("ARKK -> diversified-equity (no dominant sector)",
      _h("ARKK", "Mid-Cap Growth", {"stockPosition": 1.0},
         {"technology": 0.35, "healthcare": 0.30, "consumer_cyclical": 0.20}),
      (None, "diversified-equity", 0, None))

# CRYPTO — must BEAT the commodity branch (both are empty-sector + other-heavy).
# Spot/wrapped crypto is 'Digital Assets' category + ~100% other; keeps its own
# axis (crypto-proxy), inverse/leverage flags carry through.
check("IBIT -> crypto-proxy (not commodity)",
      _h("IBIT", "Digital Assets", {"otherPosition": 1.0}, {},
         "iShares Bitcoin Trust"),
      ("Digital Assets", "crypto-proxy", 0, None))
check("ETHA -> crypto-proxy",
      _h("ETHA", "Digital Assets", {"otherPosition": 1.0}, {},
         "iShares Ethereum Trust"),
      ("Digital Assets", "crypto-proxy", 0, None))
check("SOLZ -> crypto-proxy (other 64%, Digital Assets cat)",
      _h("SOLZ", "Digital Assets", {"otherPosition": 0.64, "cashPosition": 0.36},
         {}, "Grayscale Solana Trust"),
      ("Digital Assets", "crypto-proxy", 0, None))
check("SBIT -> crypto-proxy + 2x (leveraged bitcoin)",
      _h("SBIT", "Digital Assets", {"otherPosition": 0.31, "cashPosition": 0.69},
         {}, "ProShares Ultra Bitcoin ETF"),
      ("Digital Assets", "crypto-proxy", 0, 2.0))
check("SETH -> crypto-proxy + inverse (short ether)",
      _h("SETH", "Digital Assets", {"otherPosition": 1.0}, {},
         "ProShares Short Ether ETF"),
      ("Digital Assets", "crypto-proxy", 1, 1.0))

# COUNTRY beats sector concentration: EWY is Korea (~61% tech) — stays country.
check("EWY -> single-country despite tech 61% (region wins)",
      _h("EWY", "Focused Region", {"stockPosition": 0.99},
         {"technology": 0.61, "financial_services": 0.15, "industrials": 0.10},
         "iShares MSCI South Korea ETF"),
      (None, "single-country", 0, None))

# GEARED / single-stock funds whose underlying can't be resolved -> REVIEW,
# never a wrong broad-market/multi-asset label (the MSTY/DRIP class).
_msty = classify_from_holdings(
    "MSTY", "Derivative Income", {"stockPosition": -0.66, "cashPosition": 1.66},
    {}, "YieldMax MSTR Option Income Strategy ETF")
check("MSTY inverse+unresolved -> review, no exposure written",
      (_msty["exposure"], _msty["gics_sector"], _msty["review"]),
      (None, None, 1))
_drip = classify_from_holdings(
    "DRIP", "Trading--Leveraged Equity",
    {"stockPosition": 0.18, "cashPosition": 0.82}, {},
    "Direxion Daily S&P Oil & Gas Exp Bear 2X Shares")
check("DRIP geared-mixed -> review, no multi-asset label",
      (_drip["exposure"], _drip["review"]), (None, 1))
# SH stays broad-market (its name resolves) — the resolved-underlying case
check("SH still resolves to broad-market inverse",
      _h("SH", "Trading--Inverse Equity",
         {"stockPosition": -1.0001, "cashPosition": 1.82}, {},
         "ProShares Short S&P500"),
      (None, "broad-market", 1, 1.0))
# HEFT stays multi-asset (no leverage/inverse — a genuine allocation fund)
check("HEFT still multi-asset (un-geared mix)",
      _h("HEFT", "Tactical Allocation",
         {"stockPosition": 0.55, "bondPosition": 0.26, "otherPosition": 0.11},
         {}, "Hedgeye Fourth Turning ETF"),
      (None, "multi-asset", 0, None))

# BLOK holds blockchain EQUITIES (has sector weights) — the empty-sw gate keeps
# it OUT of the crypto branch. (In holdings_label the operator override then
# sets 'btc-sensitivity'; here we check the classifier no longer says crypto.)
check("BLOK (equity, has sectors) -> not crypto-proxy",
      _h("BLOK", "Equity Digital Assets",
         {"stockPosition": 0.90, "otherPosition": 0.10},
         {"financial_services": 0.35, "technology": 0.30,
          "communication_services": 0.20, "consumer_cyclical": 0.15},
         "Amplify Transformational Data Sharing ETF"),
      (None, "diversified-equity", 0, None))

# operator-confirmed ETF labels (currency + geared/single-stock wrappers that
# holdings data can't resolve) — web cross-checked, beat the classifier.
from _tag_proposals import _operator_etf_proposal


def _op(t):
    p = _operator_etf_proposal(t)
    return (p["gics_sector"], p["exposure"], p["inverse"], p["leverage_factor"])


print("operator ETF labels (web cross-check):")
check("EUO -> currency inverse 2x", _op("EUO"), (None, "currency", 1, 2))
check("UUP -> currency long", _op("UUP"), (None, "currency", 0, None))
check("SQQQ -> broad-market inverse 3x", _op("SQQQ"),
      (None, "broad-market", 1, 3))
check("DRIP -> Energy inverse 2x", _op("DRIP"), ("Energy", None, 1, 2))
check("MSFD -> Technology inverse (short MSFT)", _op("MSFD"),
      ("Technology", None, 1, None))
check("METD -> Comm Services inverse (short META)", _op("METD"),
      ("Communication Services", None, 1, None))
check("MSTY -> crypto-proxy", _op("MSTY"), (None, "crypto-proxy", 0, None))
check("MAGS -> mega-cap-core", _op("MAGS"), (None, "mega-cap-core", 0, None))
check("BLOK -> btc-sensitivity", _op("BLOK"),
      (None, "btc-sensitivity", 0, None))
check("HEFT -> multi-asset (operator; no funds_data)", _op("HEFT"),
      (None, "multi-asset", 0, None))
# commit-5 stragglers
check("FXC -> currency (Canadian dollar)", _op("FXC"),
      (None, "currency", 0, None))
check("REW -> Technology inverse 2x (-2x tech)", _op("REW"),
      ("Technology", None, 1, 2))

# blocked stocks resolved via OPERATOR_OVERRIDES (yfinance empty for the symbol)
from _tag_proposals import OPERATOR_OVERRIDES
check("EXPN -> stock/Industrials override",
      (OPERATOR_OVERRIDES["EXPN"]["instrument"],
       OPERATOR_OVERRIDES["EXPN"]["gics_sector"]), ("stock", "Industrials"))
check("FI -> stock/Financials override",
      (OPERATOR_OVERRIDES["FI"]["instrument"],
       OPERATOR_OVERRIDES["FI"]["gics_sector"]), ("stock", "Financials"))
check("FYBR -> stock/Communication Services override",
      (OPERATOR_OVERRIDES["FYBR"]["instrument"],
       OPERATOR_OVERRIDES["FYBR"]["gics_sector"]),
      ("stock", "Communication Services"))

# duration is carried on the bond rows (checked separately from the 4-tuple)
_tlt = classify_from_holdings("TLT", "Long-Term Bond", {"bondPosition": 0.99},
                              {}, "iShares 20+ Year Treasury Bond ETF")
check("TLT duration=long + rate_sensitive", (_tlt["duration_char"],
      _tlt["rate_sensitive"], _tlt["review"]), ("long", 1, 0))

print(f"\n{'🛑 ' + str(FAIL) + ' FAILURES' if FAIL else '✅ all passed'}")
sys.exit(1 if FAIL else 0)
