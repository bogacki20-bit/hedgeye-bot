"""Extract per-ticker Macro Show commentary (Keith's daily take).

Reads the latest macro_show transcript .txt (form-feed delimited
slides), and for every ticker in the Hedgeye doctrine universe captures
the ~100-300 char window of surrounding context where it is discussed.
Writes data/snapshots/hedgeye/<date>/macro_show/per_ticker_commentary.json
({ticker: snippet}). Idempotent: skips if today's JSON already exists.

decision_engine._get_macro_commentary_for_ticker() reads this file and
injects it into the per-ticker prompt block.

CLI:
    python -m tools.extract_macro_show_commentary
    python -m tools.extract_macro_show_commentary --force
"""

from __future__ import annotations

import re
import sys
import json
import glob
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent
_WINDOW = 220   # chars each side of a ticker hit (~100-300 total context)


def _latest_macro_txt() -> Path | None:
    dirs = sorted(glob.glob(str(_REPO / "data" / "snapshots" / "hedgeye" / "20*")),
                  reverse=True)
    for d in dirs:
        ms = Path(d) / "macro_show"
        if not ms.is_dir():
            continue
        txts = sorted(ms.glob("HE_TMS_*.txt"), reverse=True)
        if txts:
            return txts[0]
    return None


def _candidate_tickers() -> list[str]:
    try:
        from tools.doctrine import load_doctrine
        d = load_doctrine()
        ts = set(d.get("ticker_to_asset_class", {}).keys())
        ts |= set(d.get("expected_returns", {}).keys())
        return sorted(ts)
    except Exception as e:
        log.warning("doctrine universe unavailable (%s); using fallback set", e)
        return ["SPY", "QQQ", "IWM", "TLT", "UUP", "GLD", "USO", "XLE",
                "XLK", "XLF", "XLU", "XLP", "GBTC"]


def _snippet_for(text: str, ticker: str) -> str | None:
    """First mention of `ticker` as a whole word -> trimmed context window."""
    m = re.search(rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])", text)
    if not m:
        return None
    lo = max(0, m.start() - _WINDOW)
    hi = min(len(text), m.end() + _WINDOW)
    snip = re.sub(r"\s+", " ", text[lo:hi]).strip()
    if len(snip) < 30:
        return None
    return ("…" if lo > 0 else "") + snip + ("…" if hi < len(text) else "")


def run(force: bool = False) -> int:
    txt = _latest_macro_txt()
    if not txt:
        print("ERROR: no macro_show HE_TMS_*.txt found.", file=sys.stderr)
        return 2

    out_path = txt.parent / "per_ticker_commentary.json"
    if out_path.exists() and not force:
        print(f"[idempotent] {out_path} already exists — skipping. "
              f"(use --force to regenerate)")
        return 0

    body = txt.read_text(encoding="utf-8", errors="replace")
    # Form-feed delimited slides — join with spacing so context windows
    # don't span unrelated slides too aggressively.
    slides = body.split("\f")
    joined = "\n\n".join(s.strip() for s in slides if s.strip())

    commentary: dict[str, str] = {}
    for t in _candidate_tickers():
        snip = _snippet_for(joined, t)
        if snip:
            commentary[t] = snip

    out_path.write_text(json.dumps(commentary, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"Wrote {out_path} — {len(commentary)} tickers with commentary "
          f"(from {txt.name}, {len(slides)} slides).")
    return 0


def _cli(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="tools.extract_macro_show_commentary")
    p.add_argument("--force", action="store_true",
                   help="regenerate even if today's JSON exists")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return run(force=a.force)


if __name__ == "__main__":
    raise SystemExit(_cli())
