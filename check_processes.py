"""Diagnostic — is the main email loop process actually running?"""
import json
import subprocess
import sys

procs = []
try:
    cp = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq pyw.exe", "/V", "/FO", "CSV"],
        capture_output=True, text=True, timeout=10,
        shell=False, creationflags=0x08000000,
    )
    procs.append({"image": "pyw.exe", "raw": cp.stdout[:2000]})
except Exception as e:
    procs.append({"error": str(e)})

try:
    cp2 = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq python.exe", "/V", "/FO", "CSV"],
        capture_output=True, text=True, timeout=10,
        shell=False, creationflags=0x08000000,
    )
    procs.append({"image": "python.exe", "raw": cp2.stdout[:2000]})
except Exception as e:
    procs.append({"error": str(e)})

print(json.dumps(procs, indent=2), flush=True)
sys.stdout.flush()
