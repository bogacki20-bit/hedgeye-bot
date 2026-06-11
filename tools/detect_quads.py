"""Auto-detect the live Hedgeye Quad regime from the latest Macro Show
dashboard and persist it to bot_state.

DISABLED IN THE LIVE PATH AS OF 2026-05-24. The canonical Quad input is now
the manual env vars CURRENT_QUARTERLY_QUAD_OVERRIDE and
CURRENT_MONTHLY_QUAD_OVERRIDE, set by the operator after reading the macro
show. To re-enable autodetect, set QUAD_AUTODETECT=1 in the environment;
without it, this script is a no-op that exits 0 (so the existing
quad_detector_launcher.ps1 Task Scheduler entry can remain in place).

Reads:
    data/snapshots/hedgeye/<latest>/macro_show/dashboard_<date>.md

Detects BOTH timeframes:
    - quarterly Quad (strategic) — explicit "quarterly Quad N" phrasing,
      else the dominant Quad mention in the dashboard
    - monthly Quad (tactical)   — explicit "monthly Quad N" phrasing,
      else falls back to the quarterly Quad

Persists to bot_state: current_quarterly_quad, current_monthly_quad,
last_quad_detection_at. On a change vs. the previously stored values it
records an alerts_fired row (boundary='quad_rotation', synthetic
ticker '_QUAD' — alerts_fired has no `kind` column; boundary is the
discriminator) and pushes a Telegram alert via notifier.send_telegram.

Env escape hatches CURRENT_QUARTERLY_QUAD_OVERRIDE /
CURRENT_MONTHLY_QUAD_OVERRIDE bypass detection entirely.

CLI:
    python -m tools.detect_quads            # detect + persist + alert
    python -m tools.detect_quads --dry-run  # detect + print, no writes
"""

from __future__ import annotations

import os
import re
import sys
import glob
import logging
import datetime as dt
from collections import Counter
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent
_SNAP_GLOB = str(_REPO / "data" / "snapshots" / "hedgeye" / "20*")

_QUARTERLY_RE = re.compile(r"quarter(?:ly)?[^.]{0,40}?\bQuad\s*([1-4])", re.I)
_MONTHLY_RE = re.compile(r"month(?:ly)?[^.]{0,40}?\bQuad\s*([1-4])", re.I)
_ANY_QUAD_RE = re.compile(r"\bQuad\s*([1-4])\b", re.I)
# Phrases that signal the *current* regime call (weight these higher).
_DOMINANT_RE = re.compile(
    r"(?:remains?|solidly|currently|still|stay[s]?|framework remains)\D{0,30}?"
    r"\bQuad\s*([1-4])", re.I)


def _latest_dashboard() -> Optional[Path]:
    dirs = sorted(glob.glob(_SNAP_GLOB), reverse=True)
    for d in dirs:
        date = os.path.basename(d)
        md = Path(d) / "macro_show" / f"dashboard_{date}.md"
        if md.is_file():
            return md
    return None


def _detect_from_text(text: str) -> tuple[Optional[str], Optional[str], dict]:
    """Return (quarterly_quad, monthly_quad, debug)."""
    debug: dict = {}

    q_explicit = _QUARTERLY_RE.search(text)
    m_explicit = _MONTHLY_RE.search(text)

    dominant = Counter(int(n) for n in _DOMINANT_RE.findall(text))
    any_counts = Counter(int(n) for n in _ANY_QUAD_RE.findall(text))
    debug["dominant_phrase_counts"] = dict(dominant)
    debug["any_quad_counts"] = dict(any_counts)

    fallback = None
    if dominant:
        fallback = dominant.most_common(1)[0][0]
    elif any_counts:
        fallback = any_counts.most_common(1)[0][0]

    quarterly = int(q_explicit.group(1)) if q_explicit else fallback
    monthly = int(m_explicit.group(1)) if m_explicit else quarterly

    debug["quarterly_explicit"] = bool(q_explicit)
    debug["monthly_explicit"] = bool(m_explicit)

    qq = f"Quad {quarterly}" if quarterly else None
    mq = f"Quad {monthly}" if monthly else None
    return qq, mq, debug


# bot_state writes go through tools.quad_regime.set_quads() — see run()
# below. Keep this module focused on detection (dashboard text → Quad).


def run(dry_run: bool = False) -> int:
    # Feature flag — autodetect is OFF by default as of 2026-05-24.
    # Manual env vars (CURRENT_QUARTERLY_QUAD_OVERRIDE /
    # CURRENT_MONTHLY_QUAD_OVERRIDE) are the canonical Quad input.
    # Set QUAD_AUTODETECT=1 to re-enable this script's behavior.
    if os.environ.get("QUAD_AUTODETECT") != "1":
        print("QUAD_AUTODETECT not set — manual env vars are canonical. "
              "Skipping detect_quads (no DB writes, no alerts).")
        return 0

    # Env overrides short-circuit detection entirely.
    env_q = os.environ.get("CURRENT_QUARTERLY_QUAD_OVERRIDE")
    env_m = os.environ.get("CURRENT_MONTHLY_QUAD_OVERRIDE")
    if env_q or env_m:
        print(f"Override active: quarterly={env_q or '(none)'} "
              f"monthly={env_m or '(none)'} — detection skipped.")
        return 0

    md = _latest_dashboard()
    if not md:
        print("ERROR: no Macro Show dashboard markdown found under "
              f"{_SNAP_GLOB}", file=sys.stderr)
        return 2

    text = md.read_text(encoding="utf-8", errors="replace")
    qq, mq, debug = _detect_from_text(text)
    print(f"Source: {md}")
    print(f"Detected quarterly={qq}  monthly={mq}")
    print(f"Debug: {debug}")

    if not qq:
        print("ERROR: could not detect any Quad in dashboard.", file=sys.stderr)
        return 3

    if dry_run:
        print("[dry-run] no bot_state writes, no alerts.")
        return 0

    # One door into bot_state / quad_regime_history (2026-06-10): set_quads()
    # owns validation, the dual short+legacy bot_state writes, the history
    # append, the alerts_fired row, and the rotation Telegram push. Don't
    # add raw INSERTs here — extend set_quads() instead.
    from tools.quad_regime import set_quads
    result = set_quads(
        monthly_quad=mq or qq,
        quarterly_quad=qq,
        source="cron",
        notes=f"detect_quads dashboard={md.name}",
        alert_on_change=True,
    )
    print(f"set_quads: {result}")
    return 0


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.detect_quads")
    p.add_argument("--dry-run", action="store_true",
                   help="detect and print only; no DB writes, no alerts")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(_cli())
