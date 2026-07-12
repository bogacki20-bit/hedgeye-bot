"""Fixture tests for _tag_proposals pure logic (no DB, no network).
    python test_tag_proposals.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tag_proposals import (bond_fields, classify_rule_based, map_sector,
                            proposal_from_info)

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

print(f"\n{'🛑 ' + str(FAIL) + ' FAILURES' if FAIL else '✅ all passed'}")
sys.exit(1 if FAIL else 0)
