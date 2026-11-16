"""SpotGamma written reports -> files + table sg_reports (capture desk, Kris 2026-11-15).

FlowPatrol and Opening Setup (every trading day) and the Sunday Note (weekly) live at
dashboard.spotgamma.com/reports. The PDFs sit behind signed S3 links, so the capture desk runs a
small in-page script in Kris's FIRST Chrome: click each report in the list, re-fetch the page's own
signed PDF, keep the rendered text, and save ONE bundle Downloads/sg_reports_<ts>.txt
("##FILE <Type>_<YYYY-MM-DD>.txt|.pdf <bytes>\\n" + bytes, repeated).

    py -m tools.sg_reports --ingest      # split every Downloads/sg_reports_*.txt, load the table, archive the bundle

Files land in data/snapshots/spotgamma/<date>/<type>_<date>.pdf/.txt; the text goes into sg_reports via
tools.sg_pages (the desk's loader, migration 142_sg_pages). The PDF path is stored in sg_reports.url.
No LLM calls. Write a research brief for each new report (briefs/<date>.md).
"""
from __future__ import annotations

import pathlib
import re
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOWNLOADS = pathlib.Path.home() / "Downloads"
SNAP = ROOT / "data" / "snapshots" / "spotgamma"
TYPES = {"FlowPatrol": "flowpatrol", "Opening_Setup": "opening_setup", "Sunday_Note": "sunday_note"}
BOILER = re.compile(r"The information in this report is proprietary.*?SpotGamma\s*", re.S)


def _title(text: str) -> str | None:
    t = BOILER.sub("", text)
    for ln in (x.strip() for x in t.splitlines()):
        if len(ln) > 12 and not re.match(r"^(Page \d+|FlowPatrol|TM|Opening Setup|Sunday Note|\W+)$", ln, re.I):
            return ln[:200]
    return None


def split(bundle: pathlib.Path) -> list[tuple[str, str, pathlib.Path]]:
    data, i, out = bundle.read_bytes(), 0, []
    while i < len(data):
        nl = data.index(b"\n", i)
        _, name, size = data[i:nl].decode().split(" ")
        body = data[nl + 1:nl + 1 + int(size)]
        i = nl + 1 + int(size)
        m = re.match(r"(FlowPatrol|Opening_Setup|Sunday_Note)_(\d{4}-\d{2}-\d{2})\.(txt|pdf)$", name)
        if not m:
            continue
        kind, day, ext = m.groups()
        folder = SNAP / day
        folder.mkdir(parents=True, exist_ok=True)
        f = folder / f"{TYPES[kind]}_{day}.{ext}"
        f.write_bytes(body)
        out.append((TYPES[kind], day, f))
    return out


def ingest() -> list[str]:
    """Split each bundle into PDF/text files, then load the text through tools.sg_pages (table sg_reports,
    migration 142_sg_pages: report_date, kind, title, url, pages, text) so there is one loader and one table."""
    import datetime as dt
    import db_pg
    from tools import sg_pages
    db_pg._load_dotenv_fallback()
    keep = SNAP / "_bundles"
    keep.mkdir(parents=True, exist_ok=True)
    kinds = {"flowpatrol": "FlowPatrol", "opening_setup": "Opening Setup", "sunday_note": "Sunday Note"}
    msgs = []
    for b in sorted(list(DOWNLOADS.glob("sg_reports_*.txt")) + list(keep.glob("sg_reports_*.txt.reload"))):
        files = split(b)
        if b.suffix == ".txt":
            shutil.move(str(b), keep / b.name)
        by = {}
        for kind, day, f in files:
            by.setdefault((kind, day), {})[f.suffix] = f
        reports = []
        for (kind, day), fs in sorted(by.items()):
            txt = fs.get(".txt")
            text = (txt.read_text(encoding="utf-8", errors="replace") if txt else "").replace("\x00", "")
            reports.append({"report_date": day, "kind": kinds[kind], "title": _title(text) or f"{kinds[kind]} {day}",
                            "url": str(fs[".pdf"].relative_to(ROOT)) if ".pdf" in fs else None, "pages": None, "text": text})
        with db_pg.get_conn() as conn:
            r = sg_pages._store_reports(conn, {"captured_at": dt.datetime.now(dt.timezone.utc).isoformat(), "reports": reports}, b.name)
        msgs.append(f"{b.name}: {r['reports']} reports loaded ({sum(1 for v in by.values() if '.pdf' in v)} with PDF on disk)")
    return msgs


def main() -> int:
    if "--ingest" in sys.argv:
        print("\n".join(ingest()) or "NO BUNDLE in Downloads")
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
