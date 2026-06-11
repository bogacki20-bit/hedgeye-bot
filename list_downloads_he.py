"""One-shot helper: list recent HE_/PDF files in C:\\Users\\bogac\\Downloads.

Run via the bridge to debug what landed in Downloads from Chrome.
"""
from __future__ import annotations

import os
from pathlib import Path


def main() -> int:
    p = Path(r"C:\Users\bogac\Downloads")
    if not p.exists():
        print(f"path does not exist: {p}")
        return 1
    items = []
    for f in p.iterdir():
        if not f.is_file():
            continue
        name = f.name
        if not (name.upper().startswith("HE_") or name.lower().endswith(".pdf")):
            continue
        try:
            items.append((name, f.stat().st_size, f.stat().st_mtime))
        except OSError:
            continue
    items.sort(key=lambda x: -x[2])
    for name, size, mt in items[:20]:
        print(f"{name} | {size} bytes | mtime={mt}")
    if not items:
        print("no matching files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
