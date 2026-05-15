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
}


def normalize_ticker(ticker: str | None) -> str | None:
    """Return the canonical ticker (Hedgeye view). None passes through."""
    if not ticker:
        return ticker
    t = ticker.strip().upper()
    return ALIASES.get(t, t)
