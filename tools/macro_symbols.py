"""macro_symbols.py — TradingView symbols for every country's index, 10y yield
and currency, so the nightly TradingView run gives the Morning Brief real
TRADE / TREND levels and ranges for all 47 GIP countries.

Keys are the bot tickers stored in tv_levels. Symbols that TradingView can't
resolve just come back as misses (logged) — the brief shows "—" for those.
"""
from __future__ import annotations

# country -> (index key, TV symbol)
INDEX_TV = {
    "US": ("SPX", "SP:SPX"), "Canada": ("TSX", "TSX:TSX"), "UK": ("UKX", "TVC:UKX"),
    "Germany": ("DAX", "XETR:DAX"), "France": ("CAC40", "EURONEXT:PX1"), "Italy": ("FTSEMIB", "INDEX:FTSEMIB"),
    "Switzerland": ("SMI", "SIX:SMI"), "Japan": ("NIKK", "TVC:NI225"), "China": ("SSEC", "SSE:000001"),
    "Hong Kong": ("HSI", "TVC:HSI"), "South Korea": ("KOSPI", "KRX:KOSPI"), "Taiwan": ("TAIEX", "TWSE:TAIEX"),
    "India": ("NIFTY", "NSE:NIFTY"), "Australia": ("ASX200", "ASX:XJO"), "Brazil": ("IBOV", "BMFBOVESPA:IBOV"),
    "Mexico": ("IPC", "BMV:ME"), "Eurozone": ("SX5E", "TVC:SX5E"), "Spain": ("IBEX", "BME:IBC"),
    "Netherlands": ("AEX", "EURONEXT:AEX"), "Belgium": ("BEL20", "EURONEXT:BEL20"), "Austria": ("ATX", "VIE:ATX"),
    "Ireland": ("ISEQ", "EURONEXT:ISEQ"), "Finland": ("OMXH25", "OMXHEX:OMXH25"), "Portugal": ("PSI20", "EURONEXT:PSI20"),
    "Greece": ("ATHEX", "ATHEX:GD"), "Sweden": ("OMXS30", "OMXSTO:OMXS30"), "Norway": ("OBX", "OSL:OBX"),
    "Denmark": ("OMXC25", "OMXCOP:OMXC25"), "Poland": ("WIG20", "GPW:WIG20"), "Czech Rep.": ("PX", "PSECZ:PX"),
    "Russia": ("IMOEX", "MOEX:IMOEX"), "Turkey": ("XU100", "BIST:XU100"), "Israel": ("TA125", "TASE:TA125"),
    "Saudi Arabia": ("TASI", "TADAWUL:TASI"), "South Africa": ("J200", "JSE:J200"), "Singapore": ("STI", "TVC:STI"),
    "Malaysia": ("KLCI", "MYX:FBMKLCI"), "Indonesia": ("JKSE", "IDX:COMPOSITE"), "Thailand": ("SETI", "SET:SET"),
    "Philippines": ("PSEI", "PSE:PSEI"), "New Zealand": ("NZ50", "NZX:NZ50G"), "Chile": ("IPSA", "BCS:SP_IPSA"),
    "Colombia": ("COLCAP", "BVC:ICOLCAP"), "Peru": ("SPBLPGPT", "BVL:SPBLPGPT"), "Argentina": ("MERV", "BCBA:IMV"),
}
# country -> (10y yield key, TV symbol)
YIELD_TV = {
    "US": ("UST10Y", "TVC:US10Y"), "Canada": ("CA10Y", "TVC:CA10Y"), "UK": ("GB10Y", "TVC:GB10Y"),
    "Germany": ("DE10Y", "TVC:DE10Y"), "France": ("FR10Y", "TVC:FR10Y"), "Italy": ("IT10Y", "TVC:IT10Y"),
    "Spain": ("ES10Y", "TVC:ES10Y"), "Switzerland": ("CH10Y", "TVC:CH10Y"), "Japan": ("JP10Y", "TVC:JP10Y"),
    "China": ("CN10Y", "TVC:CN10Y"), "South Korea": ("KR10Y", "TVC:KR10Y"), "India": ("IN10Y", "TVC:IN10Y"),
    "Australia": ("AU10Y", "TVC:AU10Y"), "Brazil": ("BR10Y", "TVC:BR10Y"), "Mexico": ("MX10Y", "TVC:MX10Y"),
    "Netherlands": ("NL10Y", "TVC:NL10Y"), "Belgium": ("BE10Y", "TVC:BE10Y"), "Austria": ("AT10Y", "TVC:AT10Y"),
    "Ireland": ("IE10Y", "TVC:IE10Y"), "Portugal": ("PT10Y", "TVC:PT10Y"), "Greece": ("GR10Y", "TVC:GR10Y"),
    "Sweden": ("SE10Y", "TVC:SE10Y"), "Norway": ("NO10Y", "TVC:NO10Y"), "Denmark": ("DK10Y", "TVC:DK10Y"),
    "Poland": ("PL10Y", "TVC:PL10Y"), "Czech Rep.": ("CZ10Y", "TVC:CZ10Y"), "Turkey": ("TR10Y", "TVC:TR10Y"),
    "Israel": ("IL10Y", "TVC:IL10Y"), "South Africa": ("ZA10Y", "TVC:ZA10Y"), "Singapore": ("SG10Y", "TVC:SG10Y"),
    "Malaysia": ("MY10Y", "TVC:MY10Y"), "Indonesia": ("ID10Y", "TVC:ID10Y"), "Thailand": ("TH10Y", "TVC:TH10Y"),
    "Philippines": ("PH10Y", "TVC:PH10Y"), "New Zealand": ("NZ10Y", "TVC:NZ10Y"), "Chile": ("CL10Y", "TVC:CL10Y"),
    "Colombia": ("CO10Y", "TVC:CO10Y"), "Peru": ("PE10Y", "TVC:PE10Y"), "Hong Kong": ("HK10Y", "TVC:HK10Y"),
    "Taiwan": ("TW10Y", "TVC:TW10Y"), "Finland": ("FI10Y", "TVC:FI10Y"), "Eurozone": ("DE10Y", "TVC:DE10Y"),
}
# country -> (fx key, TV symbol, inverted?)  inverted = quoted USD/XXX
FX_TV = {
    "US": ("USD", "TVC:DXY", False), "Canada": ("CAD/USD", "FX_IDC:CADUSD", False),
    "UK": ("GBP/USD", "FX:GBPUSD", False), "Eurozone": ("EUR/USD", "FX:EURUSD", False),
    "Germany": ("EUR/USD", "FX:EURUSD", False), "Switzerland": ("USD/CHF", "FX:USDCHF", True),
    "Japan": ("USD/YEN", "FX:USDJPY", True), "China": ("USD/CNY", "FX_IDC:USDCNY", True),
    "South Korea": ("USD/KRW", "FX_IDC:USDKRW", True), "Taiwan": ("USD/TWD", "FX_IDC:USDTWD", True),
    "India": ("USD/INR", "FX_IDC:USDINR", True), "Australia": ("AUD/USD", "FX:AUDUSD", False),
    "Brazil": ("USD/BRL", "FX_IDC:USDBRL", True), "Mexico": ("USD/MXN", "FX:USDMXN", True),
    "Sweden": ("USD/SEK", "FX:USDSEK", True), "Norway": ("USD/NOK", "FX:USDNOK", True),
    "Denmark": ("USD/DKK", "FX:USDDKK", True), "Poland": ("USD/PLN", "FX:USDPLN", True),
    "Czech Rep.": ("USD/CZK", "FX_IDC:USDCZK", True), "Turkey": ("USD/TRY", "FX:USDTRY", True),
    "Israel": ("USD/ILS", "FX_IDC:USDILS", True), "South Africa": ("USD/ZAR", "FX:USDZAR", True),
    "Singapore": ("USD/SGD", "FX_IDC:USDSGD", True), "Malaysia": ("USD/MYR", "FX_IDC:USDMYR", True),
    "Indonesia": ("USD/IDR", "FX_IDC:USDIDR", True), "Thailand": ("USD/THB", "FX_IDC:USDTHB", True),
    "Philippines": ("USD/PHP", "FX_IDC:USDPHP", True), "New Zealand": ("NZD/USD", "FX:NZDUSD", False),
    "Chile": ("USD/CLP", "FX_IDC:USDCLP", True), "Colombia": ("USD/COP", "FX_IDC:USDCOP", True),
    "Peru": ("USD/PEN", "FX_IDC:USDPEN", True), "Argentina": ("USD/ARS", "FX_IDC:USDARS", True),
    "Russia": ("USD/RUB", "FX_IDC:USDRUB", True), "Saudi Arabia": ("USD/SAR", "FX_IDC:USDSAR", True),
}
for _c in ("France", "Italy", "Spain", "Netherlands", "Belgium", "Austria", "Ireland", "Finland",
           "Portugal", "Greece"):
    FX_TV[_c] = ("EUR/USD", "FX:EURUSD", False)


def tv_pairs():
    """[[key, tvsym], ...] for the TradingView run (deduped)."""
    seen, out = set(), []
    for k, s in list(INDEX_TV.values()) + list(YIELD_TV.values()) + [(k, s) for k, s, _ in FX_TV.values()]:
        if k not in seen:
            seen.add(k)
            out.append([k, s])
    return out
