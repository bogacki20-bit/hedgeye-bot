"""Move VolSignals PDF downloads from C:\\Users\\bogac\\Downloads into the corpus.

Triggered after Chrome bulk-downloads the VolStudies course PDFs (40 files
named VS_*.pdf). Walks the Downloads folder, moves each VS_*.pdf into
data/snapshots/volsignals/paid_course/pdfs/, and runs pdftotext on each
to extract text.

Idempotent: existing destination files are not overwritten; source files are
moved (not copied).

Run via the bridge:
    queue {"command": "python_script",
           "args": {"script": "ingest_volsignals_pdfs.py", "args": []}}
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

DOWNLOADS = Path(r"C:\Users\bogac\Downloads")
REPO = Path(r"C:\Projects\hedgeye-bot")
DEST = REPO / "data" / "snapshots" / "volsignals" / "paid_course" / "pdfs"
TXT_DEST = REPO / "data" / "snapshots" / "volsignals" / "paid_course" / "text"


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    TXT_DEST.mkdir(parents=True, exist_ok=True)

    if not DOWNLOADS.exists():
        print(f"ERROR: Downloads folder not found at {DOWNLOADS}")
        return 2

    moved = 0
    skipped = 0
    failed = 0
    extracted = 0
    extract_failed = 0

    for src in sorted(DOWNLOADS.iterdir()):
        if not src.is_file() or not src.suffix.lower() == ".pdf":
            continue
        if not src.name.startswith("VS_"):
            continue

        dst = DEST / src.name
        if dst.exists():
            print(f"skip (already in corpus): {src.name}")
            skipped += 1
            continue

        try:
            shutil.move(str(src), str(dst))
            print(f"moved: {src.name}")
            moved += 1
        except Exception as e:
            print(f"FAIL move {src.name}: {e}")
            failed += 1
            continue

        # Try pdftotext. On Windows, we need pdftotext.exe (poppler) on PATH.
        # If not installed, just log and continue.
        txt_dst = TXT_DEST / (src.stem + ".txt")
        try:
            r = subprocess.run(
                ["pdftotext", "-layout", str(dst), str(txt_dst)],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0 and txt_dst.exists() and txt_dst.stat().st_size > 100:
                extracted += 1
            else:
                extract_failed += 1
        except FileNotFoundError:
            extract_failed += 1
            if extract_failed == 1:
                print("  (pdftotext not on PATH; PDFs moved but text extraction skipped)")
        except Exception as e:
            print(f"  pdftotext err on {src.name}: {e}")
            extract_failed += 1

    print(f"summary: moved={moved} skipped={skipped} failed={failed} "
          f"text_extracted={extracted} text_failed={extract_failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
