"""Country quads from Hedgeye's /macro_dashboard table, kept in the bot (Kris, 11/5/2026).

The table is read by hand in Kris's Chrome: click the "Global Quad" header, then save the
rows as 'Country|4Q24|...|3Q27E' lines. Each read is stored so moves show up between reads.

    py -m tools.country_quads --load data/hedgeye/quad_countries/2026-11-05.txt
    py -m tools.country_quads --watch

--watch lists forward-path changes since the prior read, and countries where the next
quarter crosses between growth-up quads (1, 2) and growth-down quads (3, 4), each with its
ETF and where it sits against Trade. It flags; the rule card decides.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

COUNTRY_ETF = {
    "Argentina": "ARGT", "Australia": "EWA", "Austria": "EWO", "Belgium": "EWK", "Brazil": "EWZ", "Canada": "EWC",
    "Chile": "ECH", "China": "FXI", "Colombia": "GXG", "Denmark": None, "Czech Republic": None,
    "Emerging Markets": "EEM", "Eurozone": "EZU", "Finland": "EFNL", "France": "EWQ", "Germany": "EWG",
    "Greece": "GREK", "Hong Kong": "EWH", "India": "INDA", "Indonesia": "EIDO", "Ireland": "EIRL", "Israel": "EIS",
    "Italy": "EWI", "Japan": "EWJ", "Malaysia": "EWM", "Mexico": "EWW", "Netherlands": "EWN", "New Zealand": "ENZL",
    "Norway": "NORW", "Peru": "EPU", "Philippines": "EPHE", "Poland": "EPOL", "Portugal": "PGAL", "Russia": None,
    "Saudi Arabia": "KSA", "Singapore": "EWS", "South Africa": "EZA", "South Korea": "EWY", "Spain": "EWP",
    "Sweden": "EWD", "Switzerland": "EWL", "Taiwan": "EWT", "Thailand": "THD", "Turkey": "TUR",
    "United Kingdom": "EWU", "United States": "SPY",
}


def parse_table(text: str) -> list[tuple[str, str, int]]:
    """Pure: (country, period, quad) rows. Raises on a row that doesn't fit the header."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    head = lines[0].split("|")
    if head[0] != "Country" or len(head) < 3:
        raise ValueError(f"not a country quad table: {lines[0][:60]}")
    out = []
    for ln in lines[1:]:
        cells = ln.split("|")
        if cells[0].startswith("MODE"):
            continue
        if len(cells) != len(head):
            raise ValueError(f"row doesn't match the header: {ln[:60]}")
        out += [(cells[0], p, int(q)) for p, q in zip(head[1:], cells[1:]) if q]
    return out


def path_changes(old: dict, new: dict) -> list[tuple]:
    """Pure: (country, period, was, now) for estimated quarters that moved between two reads."""
    return sorted((c, p, old[(c, p)], q) for (c, p), q in new.items()
                  if p.endswith("E") and (c, p) in old and old[(c, p)] != q)


def crossing(path: list[int]) -> str:
    """Pure: 'up' or 'down' when the next quarter crosses growth-up (1, 2) and growth-down (3, 4)."""
    if len(path) < 2 or (path[0] <= 2) == (path[1] <= 2):
        return ""
    return "up" if path[1] <= 2 else "down"


def store(rows: list[tuple], read_date: dt.date) -> int:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO hedgeye_country_quads (read_date, country, period, quad) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (read_date, country, period) DO UPDATE SET quad=EXCLUDED.quad, read_at=NOW()",
            [(read_date, c, p, q) for c, p, q in rows])
        conn.commit()
    return len(rows)


def watch_lines(cur) -> list[str]:
    """Desk lines from the two newest reads. Takes a cursor so research_tilt can share it."""
    cur.execute("SELECT DISTINCT read_date FROM hedgeye_country_quads ORDER BY 1 DESC LIMIT 2")
    dates = [r[0] for r in cur.fetchall()]
    if not dates:
        return ["  no country quad read stored"]
    cur.execute("SELECT read_date, country, period, quad FROM hedgeye_country_quads WHERE read_date = ANY(%s)", (dates,))
    reads = {d: {} for d in dates}
    for d, c, p, q in cur.fetchall():
        reads[d][(c, p)] = q
    new = reads[dates[0]]
    forward = sorted({p for _, p in new if p.endswith("E")}, key=lambda p: (p[2:4], p[0]))
    cur.execute("SELECT DISTINCT ON (ticker) ticker, bar_close, vola_b FROM tv_levels WHERE ticker = ANY(%s) "
                "ORDER BY ticker, bar_date DESC", ([e for e in COUNTRY_ETF.values() if e],))
    lv = {t: (x, v) for t, x, v in cur.fetchall()}
    cur.execute("SELECT DISTINCT symbol FROM book_positions WHERE snapshot_date = (SELECT max(snapshot_date) FROM book_positions)")
    held = {r[0] for r in cur.fetchall()}

    def tag(c: str) -> str:
        e = COUNTRY_ETF.get(c)
        if not e:
            return "no ETF"
        x, v = lv.get(e, (None, None))
        side = "" if x is None or v is None else (" above Trade" if x > v else " below Trade")
        return f"{e}{' HELD' if e in held else ''}{side}"

    def path(c: str) -> str:
        return " > ".join(f"Q{new[(c, p)]}" for p in forward[:3] if (c, p) in new)

    out = [f"  read {dates[0]:%m/%d}" + (f", compared with {dates[1]:%m/%d}" if len(dates) > 1 else ", first read stored")
           + f"; path = {' > '.join(forward[:3])}"]
    if len(dates) > 1:
        moved = path_changes(reads[dates[1]], new)
        out.append("  Path changes:" if moved else "  Path changes: none")
        out += [f"    {c} {p}: Q{was} to Q{now} | {tag(c)}" for c, p, was, now in moved]
    for label, want in (("Turning up next quarter (Q3/Q4 to Q1/Q2)", "up"), ("Turning down next quarter (Q1/Q2 to Q3/Q4)", "down")):
        names = sorted({c for c, _ in new if crossing([new[(c, p)] for p in forward[:2] if (c, p) in new]) == want})
        out.append(f"  {label}: " + ("none" if not names else ""))
        out += [f"    {c}: {path(c)} | {tag(c)}" for c in names]
    return out


def _cli() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--load", help="saved table file, named YYYY-MM-DD.txt")
    g.add_argument("--watch", action="store_true")
    a = ap.parse_args()
    import db_pg
    db_pg._load_dotenv_fallback()
    if a.load:
        p = pathlib.Path(a.load)
        print("stored", store(parse_table(p.read_text(encoding="utf-8")), dt.date.fromisoformat(p.stem[:10])))
    else:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            print("\n".join(watch_lines(cur)))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
