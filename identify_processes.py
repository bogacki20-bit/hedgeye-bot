"""Identify each pythonw.exe by its command line so we can tell bridge vs main.py."""
import json
import subprocess
import sys

out = {"processes": []}

try:
    cp = subprocess.run(
        ["wmic", "process", "where",
         "name='pythonw.exe' or name='python.exe'",
         "get", "ProcessId,CommandLine", "/FORMAT:LIST"],
        capture_output=True, text=True, timeout=15,
        shell=False, creationflags=0x08000000,
    )
    # Parse the LIST format
    blocks = cp.stdout.split("\n\n")
    for blk in blocks:
        if not blk.strip():
            continue
        entry = {}
        for line in blk.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                entry[k.strip()] = v.strip()
        if entry.get("ProcessId"):
            out["processes"].append(entry)
except Exception as e:
    out["err"] = str(e)

print(json.dumps(out, indent=2), flush=True)
sys.stdout.flush()
