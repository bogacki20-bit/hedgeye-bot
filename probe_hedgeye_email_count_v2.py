"""Probe iCloud IMAP for total Hedgeye email count across ALL folders.

v2 upgrade: list every mailbox/folder iCloud exposes, then search Hedgeye
mail in each one. Earlier v1 only searched INBOX which dramatically
undercounted (the user's iPhone showed 118K+ unread in his VIP smart folder).

Run via the bridge:
    queue {"command": "python_script",
           "args": {"script": "probe_hedgeye_email_count_v2.py",
                    "args": ["--days", "1500"]}}
"""

from __future__ import annotations

import argparse
import imaplib
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(r"C:\Projects\hedgeye-bot")
ENV_FILE = REPO / ".env"


def _load_dotenv(path: Path):
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def list_mailboxes(conn):
    """Return list of folder names from LIST."""
    typ, data = conn.list()
    if typ != "OK" or not data:
        return []
    out = []
    pattern = re.compile(rb'\((?P<flags>[^)]*)\) "(?P<delim>[^"]*)" (?P<name>.+)$')
    for raw in data:
        if not raw:
            continue
        m = pattern.match(raw)
        if not m:
            continue
        name = m.group("name").decode("utf-8", errors="replace").strip()
        # Strip surrounding quotes if present
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        # Skip noselect folders (containers, not searchable)
        flags = m.group("flags").decode("utf-8", errors="replace").lower()
        if "\\noselect" in flags:
            continue
        out.append(name)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1500, help="lookback days (default 1500 = ~4 years)")
    ap.add_argument("--all-folders", action="store_true", default=True, help="scan all folders (default)")
    args = ap.parse_args()

    _load_dotenv(ENV_FILE)
    email = os.environ.get("ICLOUD_EMAIL")
    pw = os.environ.get("ICLOUD_APP_PASSWORD")
    if not email or not pw:
        print("ERROR: ICLOUD_EMAIL and ICLOUD_APP_PASSWORD must be set")
        return 2

    conn = imaplib.IMAP4_SSL("imap.mail.me.com", 993)
    conn.login(email, pw)
    conn.sock.settimeout(120)

    folders = list_mailboxes(conn)
    print(f"Folders found: {len(folders)}")
    for f in folders:
        print(f"  - {f}")

    keywords = ["hedgeye.com", "tier1alpha"]
    since = (datetime.now() - timedelta(days=args.days)).strftime("%d-%b-%Y")

    print()
    print(f"Searching for Hedgeye mail SINCE {since} across all folders...")
    print()
    print(f"{'Folder':<40} {'hedgeye.com':>12} {'tier1alpha':>12} {'Total':>10}")
    print("-" * 80)

    grand_total = 0
    folder_totals = {}

    for folder in folders:
        try:
            # Some folder names may need quoting
            mbox = f'"{folder}"' if " " in folder or "/" in folder else folder
            typ, _ = conn.select(mbox, readonly=True)
            if typ != "OK":
                print(f"  [skip] {folder}: select failed")
                continue
        except Exception as e:
            print(f"  [skip] {folder}: {e}")
            continue

        per_kw = {}
        for kw in keywords:
            try:
                _, data = conn.search(None, f'(SINCE {since} FROM "{kw}")')
                uids = set(data[0].split() if data[0] else [])
                per_kw[kw] = uids
            except Exception as e:
                per_kw[kw] = set()
        union = set().union(*per_kw.values())
        h = len(per_kw.get("hedgeye.com", set()))
        t = len(per_kw.get("tier1alpha", set()))
        total = len(union)
        if total > 0:
            print(f"{folder:<40} {h:>12} {t:>12} {total:>10}")
            folder_totals[folder] = total
            grand_total += total

    print("-" * 80)
    print(f"{'GRAND TOTAL (de-duped per folder)':<65} {grand_total:>10}")
    print()
    print("Notes:")
    print("- Each folder counted independently. Same email moved across folders")
    print("  is counted twice (once per folder it currently lives in).")
    print("- For classifier cost: avg ~2.7K tokens/email * count * model rate.")
    print(f"  At Claude Haiku 4.5 (~$0.0008/email): ~${grand_total * 0.0008:.2f}")
    print(f"  At Claude Sonnet 4.6 (~$0.011/email): ~${grand_total * 0.011:.2f}")

    try:
        conn.close()
    except Exception:
        pass
    conn.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
