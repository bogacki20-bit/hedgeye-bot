"""Ingest Hedgeye-pattern files from the Windows Downloads folder into the corpus.

Looks in C:\\Users\\bogac\\Downloads\\ for files matching known Hedgeye filename
patterns (HE_TMS_DS_*.pdf, etc.), and moves each into the appropriate corpus
folder under data/snapshots/hedgeye/{YYYY-MM-DD}/macro_show/. Date is parsed
from the filename when possible, otherwise today's date is used.

Existing files in the destination are preserved (no overwrite). The source file
is moved (not copied) to avoid Downloads bloat.

Run via the bridge:
    queue {"command": "python_script", "args": {"script": "ingest_hedgeye_downloads.py"}}
"""

from __future__ import annotations

import datetime as dt
import re
import shutil
import sys
from pathlib import Path

DOWNLOADS = Path(r"C:\Users\bogac\Downloads")
REPO = Path(r"C:\Projects\hedgeye-bot")
CORPUS = REPO / "data" / "snapshots" / "hedgeye"

# Filename patterns: each entry is (regex, sub_folder_under_date)
PATTERNS = [
    # The Macro Show daily deck: HE_TMS_{HOST}_{COHOST}_M.D.YYYY.pdf
    # Hosts vary day-to-day: HE_TMS_DS_FV_5.8.2026.pdf, HE_TMS_KM_DJ_5.11.2026.pdf, etc.
    (re.compile(r"^HE_TMS_[A-Z]+_[A-Z]+_(\d{1,2})\.(\d{1,2})\.(\d{4})\.pdf$", re.IGNORECASE), "macro_show"),
    # Quarterly Macro Themes Update deck: pattern TBD - guess for now
    (re.compile(r"^HE_QMT_.*\.pdf$", re.IGNORECASE), "quarterly_themes"),
    # Mid-quarter Update: HE_MQ_*
    (re.compile(r"^HE_MQ_.*\.pdf$", re.IGNORECASE), "mid_quarter_themes"),
    # Quarterly Investment Outlook
    (re.compile(r"^HE_QIO_.*\.pdf$", re.IGNORECASE), "quarterly_investment_outlook"),
    # The Call @ Hedgeye daily deck (if it exists)
    (re.compile(r"^HE_TC_.*\.pdf$", re.IGNORECASE), "the_call"),
]


def _parse_date_from_name(name: str) -> dt.date:
    """Try to pull MM.DD.YYYY out of the filename. Fall back to today."""
    m = re.search(r"(\d{1,2})[._-](\d{1,2})[._-](\d{4})", name)
    if not m:
        return dt.date.today()
    month, day, year = (int(x) for x in m.groups())
    try:
        return dt.date(year, month, day)
    except ValueError:
        return dt.date.today()


def _match_pattern(name: str) -> str | None:
    for rx, folder in PATTERNS:
        if rx.match(name):
            return folder
    return None


def main() -> int:
    if not DOWNLOADS.exists():
        print(f"ERROR: Downloads folder not found at {DOWNLOADS}")
        return 2

    moved = 0
    skipped = 0
    failed = 0

    for src in sorted(DOWNLOADS.iterdir()):
        if not src.is_file():
            continue
        sub = _match_pattern(src.name)
        if sub is None:
            continue

        date = _parse_date_from_name(src.name)
        date_str = date.isoformat()
        dst_dir = CORPUS / date_str / sub
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name

        if dst.exists():
            print(f"skip (already in corpus): {src.name}")
            skipped += 1
            continue

        try:
            shutil.move(str(src), str(dst))
            print(f"moved: {src.name} -> {dst}")
            moved += 1
        except Exception as e:
            print(f"FAILED to move {src.name}: {e}")
            failed += 1

    # Sweep stray slide_*.tmp scratch files out of any macro_show date folder.
    # These are produced by Linux-side pdftotext split scratch work that can't
    # delete its own files across the Windows mount; we tidy here.
    tmp_swept = 0
    for tmp in CORPUS.glob("*/macro_show/slide_*.tmp"):
        try:
            tmp.unlink()
            tmp_swept += 1
        except Exception as e:
            print(f"FAILED to sweep tmp {tmp}: {e}")

    print(f"summary: moved={moved} skipped={skipped} failed={failed} tmp_swept={tmp_swept}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
