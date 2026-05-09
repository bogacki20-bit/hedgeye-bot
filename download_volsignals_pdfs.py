"""Download VolSignals PDFs from a list of URLs.

Reads C:\\Users\\bogac\\Downloads\\VS_URL_LIST.json (a JSON array of URLs)
and downloads each PDF directly via urllib (no auth needed - Webflow CDN
URLs are publicly accessible). Saves to data/snapshots/volsignals/paid_course/pdfs/.

Run via the bridge:
    queue {"command": "python_script",
           "args": {"script": "download_volsignals_pdfs.py", "args": []}}

Optional args:
    --max N    cap downloads per run (default 50)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DOWNLOADS = Path(r"C:\Users\bogac\Downloads")
URL_LIST = DOWNLOADS / "VS_URL_LIST.json"
REPO = Path(r"C:\Projects\hedgeye-bot")
DEST = REPO / "data" / "snapshots" / "volsignals" / "paid_course" / "pdfs"
TXT_DEST = REPO / "data" / "snapshots" / "volsignals" / "paid_course" / "text"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def filename_from_url(url: str) -> str:
    """Pull a sensible filename out of a Webflow CDN URL."""
    parsed = urllib.parse.urlparse(url)
    name = parsed.path.split("/")[-1]
    name = urllib.parse.unquote(name)
    # Strip the leading 24-hex-char Webflow id and underscore so filenames are readable
    name = re.sub(r"^[a-f0-9]{20,32}_", "", name)
    # Replace whitespace with _, strip surrounding spaces
    name = re.sub(r"\s+", "_", name.strip())
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return "VS_" + name


def download_one(url: str, dst: Path, timeout: int = 60) -> tuple[bool, str]:
    if dst.exists() and dst.stat().st_size > 1024:
        return True, "exists"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        if len(data) < 1024:
            return False, f"too small ({len(data)} bytes)"
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.rename(dst)
        return True, f"{len(data)} bytes"
    except urllib.error.HTTPError as e:
        return False, f"http {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=50)
    args = ap.parse_args()

    if not URL_LIST.exists():
        print(f"ERROR: {URL_LIST} not found")
        return 2

    DEST.mkdir(parents=True, exist_ok=True)
    TXT_DEST.mkdir(parents=True, exist_ok=True)

    urls = json.loads(URL_LIST.read_text(encoding="utf-8"))
    print(f"Loaded {len(urls)} URLs from {URL_LIST.name}")

    ok = 0
    skip = 0
    fail = 0
    extracted = 0
    pdftotext_missing = False

    for i, url in enumerate(urls):
        if ok + fail >= args.max:
            print(f"reached max ({args.max}), stopping")
            break
        fn = filename_from_url(url)
        dst = DEST / fn
        result_ok, msg = download_one(url, dst)
        if result_ok and msg == "exists":
            print(f"  [-] {fn}  (already exists)")
            skip += 1
            continue
        if result_ok:
            print(f"  [+] {fn}  ({msg})")
            ok += 1
        else:
            print(f"  [!] {fn}  ({msg})")
            fail += 1
            continue

        # Try pdftotext
        if pdftotext_missing:
            continue
        txt_dst = TXT_DEST / (dst.stem + ".txt")
        if txt_dst.exists():
            extracted += 1
            continue
        try:
            r = subprocess.run(
                ["pdftotext", "-layout", str(dst), str(txt_dst)],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0 and txt_dst.exists() and txt_dst.stat().st_size > 100:
                extracted += 1
        except FileNotFoundError:
            pdftotext_missing = True
            print("  (pdftotext not on PATH; PDFs downloaded but text extraction skipped)")
        except Exception as e:
            print(f"  pdftotext err on {fn}: {e}")

        time.sleep(0.5)

    print(f"summary: downloaded={ok} skipped={skip} failed={fail} text_extracted={extracted}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
