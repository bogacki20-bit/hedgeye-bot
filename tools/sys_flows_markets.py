"""tools/sys_flows_markets.py - the one market list every part of the flow tracker shares.

Each market carries what each data source calls it, so the trend model, the fund holdings,
the CFTC report and the returns fit can be lined up in one row (Kris 2026-11-26: all assets).

  key    our short name                       cls    equity | bond | fx | commodity | crypto
  yahoo  symbol for the trend model (None = no trend row)
  cot    (dataset, CFTC contract code): 'tff' = financial futures, leveraged funds;
         'dis' = commodities, managed money. None = not in the CFTC report.
  etf    the ETF used as this market's factor in the returns fit (None = not a factor)
  rx     regex that matches the futures line in a fund's holdings file
  roll   True = Yahoo front-month future, levels jump at contract rolls
Currencies are quoted per US dollar pair as the futures trade: + = long that currency vs USD.
"""
from __future__ import annotations

import re

M = dict
MARKETS = [
    # equities
    M(key="spx", label="S&P 500", cls="equity", yahoo="^GSPC", cot=("tff", "13874A"), etf="SPY", rx=r"S\+?&?P\s?500|EMINI S"),
    M(key="ndx", label="Nasdaq 100", cls="equity", yahoo="^NDX", cot=("tff", "209742"), etf=None, rx=r"NASDAQ"),
    M(key="rut", label="Russell 2000", cls="equity", yahoo="^RUT", cot=("tff", "239742"), etf=None, rx=r"RUSSELL"),
    M(key="eafe", label="MSCI EAFE", cls="equity", yahoo="EFA", cot=("tff", "244041"), etf="EFA", rx=r"EAFE"),
    M(key="em", label="MSCI Emerging", cls="equity", yahoo="EEM", cot=("tff", "244042"), etf="EEM", rx=r"EMGMKT|MSCI EM"),
    M(key="stoxx", label="Euro Stoxx 50", cls="equity", yahoo="^STOXX50E", cot=None, etf=None, rx=r"EURO STOXX"),
    M(key="nikkei", label="Japan Nikkei", cls="equity", yahoo="^N225", cot=None, etf=None, rx=r"NIKKEI"),
    # bonds
    M(key="us2", label="UST 2y", cls="bond", yahoo="ZT=F", cot=("tff", "042601"), etf="SHY", rx=r"US 2YR", roll=True),
    M(key="us5", label="UST 5y", cls="bond", yahoo="ZF=F", cot=("tff", "044601"), etf=None, rx=r"US 5YR", roll=True),
    M(key="us10", label="UST 10y", cls="bond", yahoo="ZN=F", cot=("tff", "043602"), etf="IEF", rx=r"US 10YR", roll=True),
    M(key="us30", label="UST long bond", cls="bond", yahoo="ZB=F", cot=("tff", "020601"), etf="TLT", rx=r"US LONG BOND|US ULTRA BOND", roll=True),
    M(key="bund", label="German Bund", cls="bond", yahoo=None, cot=None, etf=None, rx=r"EURO-BUND"),
    M(key="gilt", label="UK Gilt", cls="bond", yahoo=None, cot=None, etf=None, rx=r"LONG GILT"),
    M(key="jgb", label="Japan 10y", cls="bond", yahoo=None, cot=None, etf=None, rx=r"JPN 10Y"),
    M(key="cgb", label="Canada 10y", cls="bond", yahoo=None, cot=None, etf=None, rx=r"CAN 10YR"),
    # currencies (vs the dollar)
    M(key="dxy", label="Dollar DXY", cls="fx", yahoo="DX-Y.NYB", cot=("tff", "098662"), etf=None, rx=r"DOLLAR INDEX"),
    M(key="eur", label="Euro", cls="fx", yahoo="6E=F", cot=("tff", "099741"), etf="FXE", rx=r"EURO FX"),
    M(key="jpy", label="Japanese yen", cls="fx", yahoo="6J=F", cot=("tff", "097741"), etf="FXY", rx=r"JPN YEN"),
    M(key="gbp", label="British pound", cls="fx", yahoo="6B=F", cot=("tff", "096742"), etf=None, rx=r"BP CURRENCY"),
    M(key="aud", label="Australian dollar", cls="fx", yahoo="6A=F", cot=("tff", "232741"), etf=None, rx=r"AUDUSD"),
    M(key="cad", label="Canadian dollar", cls="fx", yahoo="6C=F", cot=("tff", "090741"), etf=None, rx=r"C\$ CURRENCY"),
    M(key="chf", label="Swiss franc", cls="fx", yahoo="6S=F", cot=("tff", "092741"), etf=None, rx=r"CHF CURRENCY"),
    # commodities
    M(key="wti", label="WTI crude", cls="commodity", yahoo="CL=F", cot=("dis", "067651"), etf="USO", rx=r"WTI CRUDE", roll=True),
    M(key="brent", label="Brent crude", cls="commodity", yahoo="BZ=F", cot=None, etf=None, rx=r"BRENT", roll=True),
    M(key="gasoline", label="Gasoline", cls="commodity", yahoo="RB=F", cot=("dis", "111659"), etf=None, rx=r"GASOLINE", roll=True),
    M(key="diesel", label="Heating oil", cls="commodity", yahoo="HO=F", cot=("dis", "022651"), etf=None, rx=r"ULSD", roll=True),
    M(key="natgas", label="Natural gas", cls="commodity", yahoo="NG=F", cot=("dis", "023651"), etf=None, rx=r"NATURAL GAS", roll=True),
    M(key="gold", label="Gold", cls="commodity", yahoo="GC=F", cot=("dis", "088691"), etf="GLD", rx=r"GOLD", roll=True),
    M(key="silver", label="Silver", cls="commodity", yahoo="SI=F", cot=("dis", "084691"), etf=None, rx=r"SILVER", roll=True),
    M(key="copper", label="Copper", cls="commodity", yahoo="HG=F", cot=("dis", "085692"), etf="CPER", rx=r"COPPER", roll=True),
    M(key="corn", label="Corn", cls="commodity", yahoo="ZC=F", cot=("dis", "002602"), etf=None, rx=r"CORN", roll=True),
    M(key="soy", label="Soybeans", cls="commodity", yahoo="ZS=F", cot=("dis", "005602"), etf=None, rx=r"SOYBEAN FUT", roll=True),
    M(key="soyoil", label="Soybean oil", cls="commodity", yahoo="ZL=F", cot=None, etf=None, rx=r"SOYBEAN OIL", roll=True),
    M(key="wheat", label="Wheat", cls="commodity", yahoo="ZW=F", cot=("dis", "001602"), etf=None, rx=r"WHEAT", roll=True),
    M(key="sugar", label="Sugar", cls="commodity", yahoo="SB=F", cot=("dis", "080732"), etf=None, rx=r"SUGAR", roll=True),
    M(key="coffee", label="Coffee", cls="commodity", yahoo="KC=F", cot=("dis", "083731"), etf=None, rx=r"COFFEE", roll=True),
    M(key="cocoa", label="Cocoa", cls="commodity", yahoo="CC=F", cot=("dis", "073732"), etf=None, rx=r"COCOA", roll=True),
    M(key="cotton", label="Cotton", cls="commodity", yahoo="CT=F", cot=("dis", "033661"), etf=None, rx=r"COTTON", roll=True),
    M(key="cattle", label="Live cattle", cls="commodity", yahoo="LE=F", cot=("dis", "057642"), etf=None, rx=r"LIVE CATTLE", roll=True),
    # crypto
    M(key="btc", label="Bitcoin", cls="crypto", yahoo="BTC-USD", cot=("tff", "133741"), etf=None, rx=r"BITCOIN|\bBTC\b"),
]
BY_KEY = {m["key"]: m for m in MARKETS}
CLASSES = ["equity", "bond", "fx", "commodity", "crypto"]


def market_of(security):
    """Fund holdings line -> market key, or None (bills, cash, anything unmapped)."""
    s = (security or "").upper()
    for m in MARKETS:
        if re.search(m["rx"], s):
            return m["key"]
    return None
