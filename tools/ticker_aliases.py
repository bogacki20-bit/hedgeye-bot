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
}


def normalize_ticker(ticker: str | None) -> str | None:
    """Return the canonical ticker (Hedgeye view). None passes through."""
    if not ticker:
        return ticker
    t = ticker.strip().upper()
    return ALIASES.get(t, t)
