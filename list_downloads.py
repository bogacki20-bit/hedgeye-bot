"""List PDF/file inventory in user's Downloads folder for sanity check."""
from pathlib import Path
DOWNLOADS = Path(r"C:\Users\bogac\Downloads")
if not DOWNLOADS.exists():
    print(f"ERROR: {DOWNLOADS} not found")
    exit(2)
all_files = sorted(DOWNLOADS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:30]
print(f"Most recent 30 entries in {DOWNLOADS}:")
for f in all_files:
    if not f.is_file():
        continue
    sz = f.stat().st_size
    print(f"  {sz:>10} bytes  {f.name}")
