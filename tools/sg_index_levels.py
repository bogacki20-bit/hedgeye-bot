"""SpotGamma index key levels: Founder's Note markdown -> sg_index_levels.

The canary's founders_note capture saves the note as
data/snapshots/spotgamma/<date>/founders_note_pm.md. Its level table is a
header row (/ESZ26 SPX SPY NDX QQQ RUT IWM) then one "Label:" line per metric
followed by one value per column. Price rows carry 7 values (with /ES);
the tilt / notional / risk-reversal rows carry 6 (no /ES).

    py -m tools.sg_index_levels                 # today's note
    py -m tools.sg_index_levels --date 2026-09-25
    py -m tools.sg_index_levels --backfill      # every note on disk
    py -m tools.sg_index_levels --from-reports [--date D]   # from sg_reports (sg_pages --ingest-downloads does this itself)
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


def ingest_text(note_date: dt.date, text: str, source: str, conn=None) -> int:
    """Upsert one note's levels. Only the columns this note has are written, so AM then PM merges
    (PM wins where both have a value; the AM risk pivot survives a PM note without one)."""
    import db_pg
    if conn is None:
        with db_pg.get_conn() as c:
            return ingest_text(note_date, text, source, c)
    rows = parse_note(text)
    cols = sorted({c for d in rows.values() for c in d})
    if not cols:
        return 0
    with conn.cursor() as cur:
        for sym, d in rows.items():
            cur.execute(
                f"INSERT INTO sg_index_levels (note_date, sym, source_file, {', '.join(cols)}) "
                f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(cols))}) "
                f"ON CONFLICT (note_date, sym) DO UPDATE SET source_file=EXCLUDED.source_file, "
                + ", ".join(f"{c}=COALESCE(EXCLUDED.{c}, sg_index_levels.{c})" for c in cols) + ", parsed_at=now()",
                [note_date, sym, source, *[d.get(c) for c in cols]])
    conn.commit()
    return len(rows)


def ingest_file(path: pathlib.Path) -> int:
    note_date = dt.date.fromisoformat(path.parent.name)
    return ingest_text(note_date, path.read_text(encoding="utf-8", errors="replace"), str(path))


def ingest_from_reports(dates=None, conn=None) -> dict:
    """11/19: levels straight from the AM / PM Note text in sg_reports (tools/sg_report_capture.js ->
    sg_pages --ingest-downloads), so no hand-written founders_note_*.md is needed. dates=None: the
    note dates in the newest 5 days. AM first, then PM over it."""
    import db_pg
    if conn is None:
        with db_pg.get_conn() as c:
            return ingest_from_reports(dates, c)
    with conn.cursor() as cur:
        if dates:
            cur.execute("SELECT report_date, kind, text FROM sg_reports WHERE kind IN ('AM Note','PM Note') "
                        "AND report_date = ANY(%s::date[]) ORDER BY report_date, kind", ([str(x) for x in dates],))
        else:
            cur.execute("SELECT report_date, kind, text FROM sg_reports WHERE kind IN ('AM Note','PM Note') "
                        "AND report_date >= (SELECT max(report_date) - 4 FROM sg_reports WHERE kind IN ('AM Note','PM Note')) "
                        "ORDER BY report_date, kind")
        notes = cur.fetchall()
    out = {}
    for d, kind, text in notes:                       # 'AM Note' sorts before 'PM Note'
        out[f"{d} {kind}"] = ingest_text(d, text or "", f"sg_reports:{kind}", conn)
    return out


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
    ap.add_argument("--from-reports", action="store_true", help="read the AM/PM Note text in sg_reports (default route)")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    import db_pg
    db_pg._load_dotenv_fallback()
    def note_for(day_dir: pathlib.Path) -> pathlib.Path:   # prefer PM, else AM
        pm = day_dir / "founders_note_pm.md"
        return pm if pm.exists() else day_dir / "founders_note_am.md"

    if a.from_reports:
        for k, v in ingest_from_reports([dt.date.fromisoformat(a.date)] if a.date else None).items():
            print(f"{k}: {v} symbols")
        return 0
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
