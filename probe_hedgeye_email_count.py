"""Probe iCloud IMAP for total Hedgeye email count over a configurable lookback.

Standalone script — does NOT classify, save, or modify anything. Just counts.

Run via the bridge:
    queue {"command": "python_script",
           "args": {"script": "probe_hedgeye_email_count.py",
                    "args": ["--days", "1500"]}}

Reads ICLOUD_EMAIL and ICLOUD_APP_PASSWORD from env (.env or process env).

Output: counts per keyword + totals over various time windows so we can
size a real backfill before committing classification spend.
"""

from __future__ import annotations

import argparse
import imaplib
import os
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1500, help="lookback days (default 1500 = ~4 years)")
    args = ap.parse_args()

    _load_dotenv(ENV_FILE)
    email = os.environ.get("ICLOUD_EMAIL")
    pw = os.environ.get("ICLOUD_APP_PASSWORD")
    if not email or not pw:
        print("ERROR: ICLOUD_EMAIL and ICLOUD_APP_PASSWORD must be set in .env or process env")
        return 2

    print(f"Probing iCloud INBOX for Hedgeye emails (lookback: {args.days} days)...")

    conn = imaplib.IMAP4_SSL("imap.mail.me.com", 993)
    conn.login(email, pw)
    conn.select("INBOX")
    conn.sock.settimeout(60)

    keywords = ["hedgeye.com", "tier1alpha"]
    windows_days = [30, 90, 180, 365, 730, args.days]
    windows_days = sorted(set(windows_days))

    print()
    print(f"{'Window':>10}  {'hedgeye.com':>12}  {'tier1alpha':>12}  {'Total (de-duped)':>20}")
    print("-" * 70)

    for d in windows_days:
        since = (datetime.now() - timedelta(days=d)).strftime("%d-%b-%Y")
        per_keyword_uids = {}
        for kw in keywords:
            try:
                _, data = conn.search(None, f'(SINCE {since} FROM "{kw}")')
                uids = set(data[0].split() if data[0] else [])
                per_keyword_uids[kw] = uids
            except imaplib.IMAP4.error as e:
                print(f"  IMAP error for {kw!r}: {e}")
                per_keyword_uids[kw] = set()
        union = set().union(*per_keyword_uids.values())
        h = len(per_keyword_uids.get("hedgeye.com", set()))
        t = len(per_keyword_uids.get("tier1alpha", set()))
        total = len(union)
        label = f"{d}d" if d < 1000 else f"{d}d (~{d//365}y)"
        print(f"{label:>10}  {h:>12}  {t:>12}  {total:>20}")

    print()
    print("Notes:")
    print("- 'Total (de-duped)' = union of UIDs hit by either keyword search.")
    print("- This is FROM-line search only; iCloud may have additional Hedgeye-related")
    print("  mail in archives/folders this script doesn't see (only INBOX is selected).")
    print("- For classifier cost estimate: avg ~2.7K tokens/email * count * model rate.")
    print("  At Claude Haiku 4.5 rates (~$0.25/M in, $1.25/M out) -> ~$0.0008 per email.")
    print("  At Claude Sonnet 4.6 rates (~$3/M in, $15/M out) -> ~$0.011 per email.")

    try:
        conn.close()
    except Exception:
        pass
    conn.logout()
    return 0


if __name__ == "__main__":
    sys.exit(main())
