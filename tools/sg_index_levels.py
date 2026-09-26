"""SpotGamma index key levels: Founder's Note markdown -> sg_index_levels.

The canary's founders_note capture saves the note as
data/snapshots/spotgamma/<date>/founders_note_pm.md. Its level table is a
header row (/ESZ26 SPX SPY NDX QQQ RUT IWM) then one "Label:" line per metric
followed by one value per column. Price rows carry 7 values (with /ES);
the tilt / notional / risk-reversal rows carry 6 (no /ES).

    py -m tools.sg_index_levels                 # today's note
    py -m tools.sg_index_levels --date 2026-09-25
    py -m tools.sg_index_levels --backfill      # every note on disk
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import pathlib
import re

log = logging.getLogger(__name__)

SNAP_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "snapshots" / "spotgamma"
SYMS7 = ("ES", "SPX", "SPY", "NDX", "QQQ", "RUT", "IWM")
SYMS6 = SYMS7[1:]
ROWS = {                                   # note label -> column
    "Reference Price": "ref_price",
    "SG Volatility Trigger™": "vol_trigger",
    "Zero Gamma Level": "zero_gamma",
    "Call Wall": "call_wall",
    "Put Wall": "put_wall",
    "Absolute Gamma Strike": "abs_gamma_strike",
    "Gamma Tilt": "gamma_tilt",
    "Gamma Notional (MM)": "gamma_notional_mm",
    "25 Delta Risk Reversal": "rr25",
    "SG Implied 1-Day Move High": "implied_move_high",
    "SG Implied 1-Day Move Low": "implied_move_low",
}
_NUM = re.compile(r"^[‑\-−]?\$?[‑\-−]?[\d,]*\.?\d+[KMB%]?$")


def _num(tok: str) -> float | None:
    t = tok.replace("‑", "-").replace("−", "-").replace("$", "").replace(",", "")
    mult = {"K": 1e-3, "M": 1.0, "B": 1e3}.get(t[-1:], None)   # notional -> $MM
    if mult is not None:
        t = t[:-1]
    if t.endswith("%"):
        return None
    try:
        v = float(t)
    except ValueError:
        return None
    return v * mult if mult is not None else v


def parse_note(text: str) -> dict[str, dict]:
    """{sym: {column: value}} from one Founder's Note."""
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    out: dict[str, dict] = {s: {} for s in SYMS7}
    i = 0
    while i < len(lines):
        label = lines[i].rstrip(":")
        col = ROWS.get(label) if lines[i].endswith(":") else None
        if not col:
            i += 1
            continue
        vals, j = [], i + 1
        while j < len(lines) and _NUM.match(lines[j]) and len(vals) < 7:
            vals.append(_num(lines[j]))
            j += 1
        syms = SYMS7 if len(vals) == 7 else SYMS6 if len(vals) == 6 else None
        if col.startswith("implied_move"):
            syms = ("SPX", "SPY")[:len(vals)]  # SPX / SPY only
        if syms:
            for s, v in zip(syms, vals):
                if v is not None:
                    out[s][col] = v
        else:
            log.warning("sg levels: %r had %d values — skipped", label, len(vals))
        i = j
    m = re.search(r"Pivot:\s*([\d,]+)", text)
    if m:
        out["SPX"]["risk_pivot"] = float(m.group(1).replace(",", ""))
    return {s: d for s, d in out.items() if d}


def ingest_file(path: pathlib.Path) -> int:
    import db_pg
    note_date = dt.date.fromisoformat(path.parent.name)
    rows = parse_note(path.read_text(encoding="utf-8", errors="replace"))
    cols = sorted({c for d in rows.values() for c in d})
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        for sym, d in rows.items():
            cur.execute(
                f"INSERT INTO sg_index_levels (note_date, sym, source_file, {', '.join(cols)}) "
                f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(cols))}) "
                f"ON CONFLICT (note_date, sym) DO UPDATE SET source_file=EXCLUDED.source_file, "
                + ", ".join(f"{c}=EXCLUDED.{c}" for c in cols) + ", parsed_at=now()",
                [note_date, sym, str(path), *[d.get(c) for c in cols]])
        conn.commit()
    return len(rows)


def latest(sym: str, on_or_before: dt.date) -> dict | None:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM sg_index_levels WHERE sym=%s AND note_date<=%s "
                    "ORDER BY note_date DESC LIMIT 1", (sym, on_or_before))
        row = cur.fetchone()
        return dict(zip([c.name for c in cur.description], row)) if row else None


def _cli() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--backfill", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import db_pg
    db_pg._load_dotenv_fallback()
    def note_for(day_dir: pathlib.Path) -> pathlib.Path:   # prefer PM, else AM
        pm = day_dir / "founders_note_pm.md"
        return pm if pm.exists() else day_dir / "founders_note_am.md"

    if a.backfill:
        files = [note_for(d) for d in sorted(p for p in SNAP_DIR.iterdir() if p.is_dir())]
    else:
        files = [note_for(SNAP_DIR / (a.date or dt.date.today().isoformat()))]
    for f in files:
        if f.exists():
            print(f"{f.parent.name}: {ingest_file(f)} symbols")
        else:
            print(f"{f.parent.name}: no founders_note_pm.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
