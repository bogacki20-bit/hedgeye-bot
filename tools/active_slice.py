"""Active universe resolver — intersection of monthly and quarterly Quad.

The bot polls a slice of `config/mfr_quad_map.yaml` per cycle. The slice is
the set of tickers tagged the same side ('long' or 'short') in BOTH the
active monthly Quad AND the active quarterly Quad. Strategic AND tactical
must agree — that's the only universe the notifier looks at.

Active Quads come from `tools.doctrine.current_quarterly_quad()` /
`current_monthly_quad()`, which respect the env vars
CURRENT_QUARTERLY_QUAD_OVERRIDE / CURRENT_MONTHLY_QUAD_OVERRIDE.

CLI:
    python -m tools.active_slice long
    python -m tools.active_slice short
    python -m tools.active_slice both       # show both sides + counts
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Literal

import yaml

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent
_MAP_PATH = _REPO / "config" / "mfr_quad_map.yaml"

Side = Literal["long", "short"]


def _load_map() -> dict:
    if not _MAP_PATH.exists():
        log.warning("mfr_quad_map.yaml missing at %s — empty universe", _MAP_PATH)
        return {"tickers": {}}
    with open(_MAP_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"tickers": {}}


def _quad_key(quad: str) -> str:
    """'Quad 2' -> 'quad_2'. Tolerates 'quad2', 'q2', '2'."""
    s = str(quad).strip().lower().replace("quad", "").replace("q", "").strip()
    if s in ("1", "2", "3", "4"):
        return f"quad_{s}"
    raise ValueError(f"unrecognized Quad label: {quad!r}")


def active_universe(side: Side,
                    monthly_quad: str | None = None,
                    quarterly_quad: str | None = None) -> list[str]:
    """Tickers tagged `side` for both the active monthly AND quarterly Quad.

    Args:
        side: 'long' or 'short'.
        monthly_quad / quarterly_quad: if None, pulled from tools.doctrine
            (which respects the CURRENT_*_QUAD_OVERRIDE env vars).
    Returns:
        Sorted list of tickers. Empty list when no map or no overlap.
    """
    if side not in ("long", "short"):
        raise ValueError(f"side must be 'long' or 'short', got {side!r}")
    if monthly_quad is None or quarterly_quad is None:
        from tools.doctrine import current_monthly_quad, current_quarterly_quad
        monthly_quad   = monthly_quad   or current_monthly_quad()
        quarterly_quad = quarterly_quad or current_quarterly_quad()

    mq = _quad_key(monthly_quad)
    qq = _quad_key(quarterly_quad)

    data = _load_map()
    out: list[str] = []
    for ticker, sides in (data.get("tickers") or {}).items():
        if not isinstance(sides, dict):
            continue
        if sides.get(mq) == side and sides.get(qq) == side:
            out.append(ticker.upper())
    return sorted(out)


def both_sides(monthly_quad: str | None = None,
               quarterly_quad: str | None = None) -> dict[str, list[str]]:
    """Convenience: {'long': [...], 'short': [...]} for the active Quads."""
    return {
        "long":  active_universe("long",  monthly_quad, quarterly_quad),
        "short": active_universe("short", monthly_quad, quarterly_quad),
    }


def _cli(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.active_slice")
    ap.add_argument("side", choices=("long", "short", "both"),
                    help="Which side of the active universe to print")
    ap.add_argument("--monthly", default=None,
                    help='Override monthly Quad (e.g. "Quad 2"). Defaults to env.')
    ap.add_argument("--quarterly", default=None,
                    help='Override quarterly Quad (e.g. "Quad 3"). Defaults to env.')
    ap.add_argument("--count", action="store_true",
                    help="Print counts only, not ticker lists")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if a.side == "both":
        sides = both_sides(a.monthly, a.quarterly)
        for s in ("long", "short"):
            print(f"=== {s.upper()} ({len(sides[s])}) ===")
            if not a.count:
                print(", ".join(sides[s]) or "(none)")
    else:
        uni = active_universe(a.side, a.monthly, a.quarterly)
        if a.count:
            print(len(uni))
        else:
            print("\n".join(uni))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
