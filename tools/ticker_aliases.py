"""Map traded instruments to the Hedgeye/MFR/SpotGamma ticker they really track.

Hedgeye's framework signals SPX, but on Fidelity we trade SPHB / SPMO etc.
Reconciliation needs to recognize those fills as actions on the SPX signal.

Add entries as the user surfaces real trades; default behavior is identity.
"""
from __future__ import annotations

ALIASES: dict[str, str] = {
    # High-beta + momentum SPX vehicles
    "SPHB": "SPX",
    "SPMO": "SPX",

    # Bitcoin trackers — Hedgeye uses 'BITCOIN' as the macro symbol
    "IBIT": "BITCOIN",   # iShares Bitcoin Trust ETF
    "GBTC": "BITCOIN",   # Grayscale Bitcoin Trust
    "BITO": "BITCOIN",   # ProShares Bitcoin Strategy ETF

    # Ethereum trackers — bot doesn't currently fire ETH alerts, but pin the
    # alias so once Hedgeye starts surfacing ETH ranges the fills reconcile.
    "ETHA": "ETH",        # iShares Ethereum Trust
    "ETHE": "ETH",        # Grayscale Ethereum Trust

    # Precious metals — Hedgeye uses 'GOLD' / 'SILVER' macro symbols
    "AAAU": "GOLD",       # Goldman Sachs Physical Gold
    "GLD":  "GOLD",
    "IAU":  "GOLD",
    "SLV":  "SILVER",

    # Oil — bot uses 'BRENT' only (no WTI/CRUDE/OIL symbols), so route both
    # the WTI tracker (USO) and the Brent tracker (BNO) to BRENT.
    "USO":  "BRENT",      # United States Oil Fund (WTI-tracking; grouped here)
    "BNO":  "BRENT",      # United States Brent Oil Fund

    # Kinesis-native symbols. Hedgeye macro uses pair names like 'EUR/USD'
    # / 'GBP/USD' — Kinesis stablecoins map to those.
    "C1EUR": "EUR/USD",   # Kinesis EUR stablecoin
    "C1GBP": "GBP/USD",   # Kinesis GBP stablecoin
    "C1CHF": "CHF/USD",   # bot has no CHF/USD alert today; pin for future
    "KAU":   "GOLD",      # Kinesis gold (1 g)
    "KAG":   "SILVER",    # Kinesis silver (10 g)
    "BTC":    "BITCOIN",
    "BTCUSD": "BITCOIN",
}


def normalize_ticker(ticker: str | None) -> str | None:
    """Return the canonical ticker (Hedgeye view). None passes through."""
    if not ticker:
        return ticker
    t = ticker.strip().upper()
    return ALIASES.get(t, t)


# ─────────────────────────── Doctrine proxy map ───────────────────────────
#
# Hedgeye's Risk Range labels are macro symbols (SPX, COMPQ, GOLD, BRENT, …),
# but config/hedgeye_doctrine.yaml's quad_universe / expected_returns are
# keyed by tradeable ETF tickers (SPY, QQQ, GLD, USO, …). Without a bridge,
# every doctrine-membership test for an RR-labeled ticker missed and
# rendered "favored neutral" — the bug operator caught 2026-06-10 where
# GOLD alerts said "Q3 favored neutral" with Q3 active and GLD in
# Q3 longs.commodities.
#
# Direction: macro/index label → doctrine ETF proxy. Apply BEFORE the
# membership test, never as a substitute for normalize_ticker() (which
# walks the other way — ETF wrapper → macro label, for trade
# reconciliation). The two maps live in the same module for visibility
# so a future contributor sees both at once.
RR_TO_DOCTRINE_PROXY: dict[str, str] = {
    "GOLD":    "GLD",
    "SILVER":  "SLV",
    "WTIC":    "USO",
    "BRENT":   "USO",
    "NATGAS":  "UNG",
    "COPPER":  "CPER",
    "COMPQ":   "QQQ",
    "SPX":     "SPY",
    "BITCOIN": "GBTC",
}


def to_doctrine_proxy(ticker: str | None) -> str | None:
    """Map an RR/macro label to its doctrine-universe ETF proxy. Falls
    through to the input (uppercased) when no proxy is known so single
    stocks and tickers already in doctrine form aren't disturbed."""
    if not ticker:
        return ticker
    t = ticker.strip().upper()
    return RR_TO_DOCTRINE_PROXY.get(t, t)
