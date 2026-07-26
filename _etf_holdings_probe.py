"""_etf_holdings_probe.py — DUMP the real yfinance funds_data shape so the
holdings-based ETF classifier is built against actual output, not memory.

Yahoo is unreachable from the cloud sandbox, so run this on a box that CAN
reach yfinance (your machine / Railway) and paste the output back.

    python _etf_holdings_probe.py

Reads nothing, writes nothing, hits no DB. Paced 1.2s to respect rate limits.
Prints, per ETF: quoteType, .info sector/category, and the three funds_data
structures (asset_classes, sector_weightings, top_holdings) with their exact
keys/types — that's what the parser needs to see.
"""
import time
import yfinance as yf

# A spread across every case the classifier must handle:
PROBE = [
    "XLV",   # equity sector ETF (Health Care) — .info sector often blank
    "SKYY",  # thematic (cloud) — should be Technology-dominant in weightings
    "SPY",   # broad market
    "EWZ",   # single country (Brazil)
    "FXI",   # single country (China)
    "USO",   # commodity (oil futures) — likely empty sector_weightings
    "GLD",   # commodity (gold)
    "BOIL",  # 2x commodity (nat gas)
    "TLT",   # long-duration treasuries (bonds)
    "SHY",   # short-duration treasuries
    "ARKK",  # active thematic (innovation)
    "TAN",   # thematic (solar) — cross-sector
    "SOXL",  # 3x leveraged semis
    "SH",    # inverse S&P
]


def dump(t):
    print(f"\n{'='*70}\n{t}")
    try:
        tk = yf.Ticker(t)
        info = tk.info or {}
        print(f"  quoteType={info.get('quoteType')!r}  "
              f"info.sector={info.get('sector')!r}  "
              f"info.category={info.get('category')!r}  "
              f"longName={info.get('longName')!r}")
    except Exception as e:
        print(f"  .info ERR {type(e).__name__}: {e}")
        return
    try:
        fd = tk.funds_data
    except Exception as e:
        print(f"  funds_data ERR {type(e).__name__}: {e} "
              f"(likely not a fund — that's fine)")
        return
    for attr in ("asset_classes", "sector_weightings", "fund_overview"):
        try:
            val = getattr(fd, attr, None)
            print(f"  {attr} = {val!r}")
        except Exception as e:
            print(f"  {attr} ERR {type(e).__name__}: {e}")
    try:
        th = fd.top_holdings
        if th is not None and len(th):
            print(f"  top_holdings columns={list(th.columns)}")
            print(f"  top_holdings.head:\n{th.head(4).to_string()}")
        else:
            print("  top_holdings = <empty>")
    except Exception as e:
        print(f"  top_holdings ERR {type(e).__name__}: {e}")


if __name__ == "__main__":
    print(f"yfinance {yf.__version__}")
    for t in PROBE:
        dump(t)
        time.sleep(1.2)
    print(f"\n{'='*70}\nDONE — paste everything above back.")
