"""Auto-detect the live Hedgeye Quad regime from the latest Macro Show
dashboard and persist it to bot_state.

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


def _get_state(key: str) -> Optional[str]:
    try:
        import db_pg
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM bot_state WHERE key = %s", (key,))
            row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        log.warning("bot_state read failed (%s): %s", key, e)
        return None


def _set_state(key: str, value: str) -> None:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO bot_state (key, value, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (key) DO UPDATE
              SET value = EXCLUDED.value, updated_at = NOW()
            """,
            (key, value),
        )
        conn.commit()


def _record_rotation_alert(msg: str) -> None:
    """Write a quad_rotation row to alerts_fired (synthetic ticker
    '_QUAD', boundary 'quad_rotation') — one per day via the existing
    (ticker, boundary, signal_date) unique constraint."""
    try:
        import db_pg
        db_pg.record_alert(
            ticker="_QUAD",
            boundary="quad_rotation",
            signal_date=dt.date.today(),
            recommendation_text=msg,
            suggested_action="QUAD_ROTATION",
        )
    except Exception as e:
        log.warning("could not record quad_rotation alert: %s", e)


def _push_telegram(msg: str) -> None:
    try:
        from notifier import send_telegram
        send_telegram("QUAD ROTATION DETECTED", msg, priority=2)
    except Exception as e:
        log.warning("telegram push failed: %s", e)


def _tactical_note(prev_q, new_q, prev_m, new_m) -> str:
    if new_q and prev_q and new_q != prev_q:
        return ("QUARTERLY rotation — re-evaluate strategic universe and "
                "asset-class caps for the new regime.")
    if new_m and prev_m and new_m != prev_m:
        return ("MONTHLY rotation — tighten/loosen tactical bias and alert "
                "calibration; strategic universe unchanged.")
    return "First detection — baseline established."


def run(dry_run: bool = False) -> int:
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

    prev_q = _get_state("current_quarterly_quad")
    prev_m = _get_state("current_monthly_quad")

    _set_state("current_quarterly_quad", qq)
    _set_state("current_monthly_quad", mq or qq)
    _set_state("last_quad_detection_at", dt.datetime.utcnow().isoformat() + "Z")

    changed = (prev_q is not None and prev_q != qq) or \
              (prev_m is not None and (mq or qq) != prev_m)
    if changed:
        rot = "QUARTERLY" if (prev_q and prev_q != qq) else "MONTHLY"
        note = _tactical_note(prev_q, qq, prev_m, mq or qq)
        msg = (
            "🔄 QUAD ROTATION DETECTED\n"
            f"Yesterday: Quarterly {prev_q or '?'} / Monthly {prev_m or '?'}\n"
            f"Today:     Quarterly {qq} / Monthly {mq or qq}\n"
            f"Rotation type: {rot}\n"
            f"Tactical adjustment: {note}"
        )
        print(msg)
        _record_rotation_alert(msg)
        _push_telegram(msg)
    else:
        print("No rotation vs. previously stored Quad state.")
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
