"""tools/raw_backup.py - copy every raw capture file to Kris's private Google Drive (Kris 11/24/2026: "all the data
should go to the bot or this Google Drive ... if the computer ever dies there goes all the data").

The nightly tools/db_backup.py covers the DATABASE. This covers the FILES: everything under data/ (SpotGamma raw
captures, Hedgeye reports and pages, transcripts, snapshots, TradingView and MFR files, the desk's logs) plus outputs/.
It only ever adds or updates files on the Drive side; it never deletes there and never touches the local copies.

    py -m tools.raw_backup            # copy what is new or changed, then print what moved
    py -m tools.raw_backup --check    # what would be copied, nothing written
    py -m tools.raw_backup --sizes    # size of each folder here and on the Drive

Target: G:\\My Drive\\Hedgeye Backups\\raw\\ (the same private, unshared folder the database backups use; never point
an outside model at it). Capture desk: run it after every evening database backup, and at midday on market days.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = pathlib.Path(r"G:\My Drive\Hedgeye Backups\raw")
FOLDERS = ["data", "outputs"]
LOG = ROOT / "data" / "raw_backup.log"


def _robocopy(src: pathlib.Path, dst: pathlib.Path, check: bool) -> tuple[int, str]:
    # /E subfolders, /XO skip files older than the Drive copy, /FFT 2-second time tolerance (cloud drives),
    # /R:1 /W:2 one retry, /XJ no junctions, /NFL /NDL /NP short output. No /PURGE or /MIR: nothing is ever deleted.
    cmd = ["robocopy", str(src), str(dst), "/E", "/XO", "/FFT", "/R:1", "/W:2", "/XJ", "/MT:8", "/NFL", "/NDL", "/NP",
           "/XF", "*.crdownload", "*.tmp", "/XD", "__pycache__"]
    if check:
        cmd.append("/L")
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    tail = [l.strip() for l in p.stdout.splitlines() if l.strip().startswith(("Dirs :", "Files :", "Bytes :"))]
    return p.returncode, " | ".join(tail)


def _size(p: pathlib.Path) -> tuple[int, int]:
    n = b = 0
    for dp, _, fs in os.walk(p):
        for f in fs:
            try:
                b += os.path.getsize(os.path.join(dp, f))
                n += 1
            except OSError:
                pass
    return n, b


def run(check: bool = False) -> int:
    if not TARGET.parent.exists():
        print(f"Google Drive folder not found: {TARGET.parent} (is Drive running?)")
        return 2
    worst = 0
    lines = []
    for name in FOLDERS:
        src = ROOT / name
        if not src.exists():
            continue
        rc, tail = _robocopy(src, TARGET / name, check)
        worst = max(worst, rc)
        lines.append(f"{name}: {'ok' if rc < 8 else 'FAILED rc=' + str(rc)} | {tail}")
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = f"raw_backup {stamp}{' (check only)' if check else ''} -> {TARGET}\n  " + "\n  ".join(lines)
    print(out)
    if not check:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(out + "\n")
    return 0 if worst < 8 else 1   # robocopy: 0-7 are success codes


def sizes() -> int:
    for name in FOLDERS:
        for where, p in (("here", ROOT / name), ("drive", TARGET / name)):
            if p.exists():
                n, b = _size(p)
                print(f"{name:8s} {where:6s} {n:8d} files {b / 1073741824:8.2f} GB")
            else:
                print(f"{name:8s} {where:6s} (missing)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--sizes", action="store_true")
    a = ap.parse_args()
    sys.exit(sizes() if a.sizes else run(a.check))
