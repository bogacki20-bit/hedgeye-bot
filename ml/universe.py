"""ml/universe.py — one place for the ML universe and asset classes.

Phase A: SPY, UUP, USO, AAAU, TLT. Phase B(a) adds the 16 new tickers
(operator brief 2026-09-07). ASSET_CLASS feeds the walk-forward's second
categorical.
"""

PHASE_A = ["SPY", "UUP", "USO", "AAAU", "TLT"]
PHASE_B = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLY", "XLP", "XLU", "XLB",
           "XLRE", "XLC", "QQQ", "IWM", "GLD", "HYG", "EEM"]
TICKERS = PHASE_A + PHASE_B

ASSET_CLASS = {
    **{t: "sector" for t in ("XLK", "XLF", "XLV", "XLE", "XLI", "XLY",
                             "XLP", "XLU", "XLB", "XLRE", "XLC")},
    "SPY": "equity_broad", "QQQ": "equity_broad", "IWM": "equity_broad",
    "EEM": "equity_broad",
    "TLT": "bond", "HYG": "bond",
    "USO": "commodity", "GLD": "commodity", "AAAU": "commodity",
    "UUP": "fx",
}
