"""Bot ticker -> TradingView symbol, for the overnight TradingView level read.

Plain US tickers pass through (TradingView resolves them). Everything else is
mapped explicitly: Hedgeye names (BRENT, SPX, UST10Y), futures (CL_F),
foreign listings (MC.PA), Yahoo-style indices (^VIX) and crypto (BTCUSD).
Oil/metals/rates use the FUTURES contract; Keith quotes futures, and the
9/26 Brent check showed CFD and futures agree within cents anyway.
"""
from __future__ import annotations

import re

SPECIAL = {
    # Hedgeye Risk Range names
    "SPX": "SP:SPX", "COMPQ": "NASDAQ:IXIC", "RUT": "TVC:RUT", "NIKK": "TVC:NI225",
    "DAX": "XETR:DAX", "SSEC": "SSE:000001", "BRENT": "ICEEUR:BRN1!",
    "WTIC": "NYMEX:CL1!", "GOLD": "COMEX:GC1!", "SILVER": "COMEX:SI1!",
    "COPPER": "COMEX:HG1!", "NATGAS": "NYMEX:NG1!", "USD": "TVC:DXY",
    "UST10Y": "TVC:US10Y", "UST2Y": "TVC:US02Y", "UST30Y": "TVC:US30Y",
    "VIX": "CBOE:VIX", "BITCOIN": "BITSTAMP:BTCUSD",
    "EUR/USD": "FX:EURUSD", "GBP/USD": "FX:GBPUSD", "CAD/USD": "FX_IDC:CADUSD",
    "USD/YEN": "FX:USDJPY", "SPCX": "AMEX:SPCX",
    # Yahoo-style indices
    "^VIX": "CBOE:VIX", "^VVIX": "CBOE:VVIX", "^VXN": "CBOE:VXN", "^RVX": "CBOE:RVX",
    "^GVZ": "CBOE:GVZ", "^OVX": "CBOE:OVX", "^MOVE": "TVC:MOVE",
    # crypto
    "BTCUSD": "BITSTAMP:BTCUSD", "ETHUSD": "BITSTAMP:ETHUSD", "XRPUSD": "BITSTAMP:XRPUSD",
    "SOLUSD": "COINBASE:SOLUSD", "AVAXUSD": "COINBASE:AVAXUSD",
    "TRXUSD": "BINANCE:TRXUSDT", "RUNEUSD": "BINANCE:RUNEUSDT",
    # share classes
    "BF-B": "NYSE:BF.B", "PBR-A": "NYSE:PBR.A",
}

FUTURES = {   # root -> exchange prefix for the continuous front month
    "ES": "CME_MINI", "NQ": "CME_MINI", "RTY": "CME_MINI", "YM": "CBOT_MINI",
    "CL": "NYMEX", "NG": "NYMEX", "HO": "NYMEX", "RB": "NYMEX", "PL": "NYMEX", "PA": "NYMEX",
    "GC": "COMEX", "SI": "COMEX", "HG": "COMEX",
    "ZB": "CBOT", "ZN": "CBOT", "ZF": "CBOT", "ZT": "CBOT", "UB": "CBOT", "ZQ": "CBOT",
    "ZC": "CBOT", "ZS": "CBOT", "ZW": "CBOT", "ZL": "CBOT", "ZM": "CBOT",
    "6E": "CME", "VX": "CBOE",
    "FDAX": "EUREX", "FESX": "EUREX", "FGBL": "EUREX", "FGBM": "EUREX", "FGBS": "EUREX",
    "FGBX": "EUREX", "FOAT": "EUREX", "FBTP": "EUREX", "FBTS": "EUREX", "FVS": "EUREX",
    "FXXP": "EUREX", "FESB": "EUREX", "FCE": "EURONEXT",
}

SUFFIX = {   # Yahoo suffix -> TradingView exchange
    ".TO": "TSX", ".L": "LSE", ".PA": "EURONEXT", ".DE": "XETR", ".SW": "SIX",
    ".HK": "HKEX", ".T": "TSE", ".KS": "KRX", ".TW": "TWSE", ".ST": "OMXSTO", ".MI": "MIL",
}


def tv_symbol(ticker: str) -> str | None:
    t = (ticker or "").strip().upper()
    if not t:
        return None
    if t in SPECIAL:
        return SPECIAL[t]
    m = re.fullmatch(r"([0-9A-Z]+)_F", t)
    if m:
        ex = FUTURES.get(m.group(1))
        return f"{ex}:{m.group(1)}1!" if ex else None
    for suf, ex in SUFFIX.items():
        if t.endswith(suf):
            base = t[: -len(suf)]
            if suf == ".HK":
                base = base.lstrip("0") or "0"
            base = base.replace("-", "_")
            return f"{ex}:{base}"
    if re.fullmatch(r"[A-Z]{1,5}", t):
        # Pin to a US venue: a bare symbol lets TradingView pick ANY listing
        # (9/26 run: CORN -> a corn CFD, DOW -> the index, CSL/BHP -> ASX,
        # ENB -> TSX, BTC -> crypto market cap). 5-letter F/Y = OTC foreign ordinary/ADR.
        if len(t) == 5 and t[-1] in "FY":
            return f"OTC:{t}"
        return f"BATS:{t}"
    return None
