"""tools/hedgeye_mail_archive.py — roadmap §2 step 1: archive the Hedgeye
email history for the stated-Quad back-parse.

Reuses email_parser.connect_imap() (Feed 2's own connection + .env
credentials — nothing new, nothing logged). STRICTLY READ-ONLY: every
folder is SELECTed with readonly=True and fetches use BODY.PEEK; no move,
no flag, no delete, ever.

Pull: all folders (INBOX + any folder whose name contains 'hedgeye'),
FROM containing "hedgeye" SINCE 01-Jan-2022. Each message saved raw as
data/hedgeye_mail/<YYYY-MM-DD>_<subject-slug>_<uid>.eml plus
manifest.csv (folder, uidvalidity, uid, date, from, subject, product
guess, file). Idempotent on (folder, uidvalidity, uid) — re-runs fetch
only new. data/hedgeye_mail/ is gitignored (subscriber content, personal
use only — roadmap §2).

    py tools/hedgeye_mail_archive.py                 # manifest + bodies
    py tools/hedgeye_mail_archive.py --manifest-only # headers + counts only
    py tools/hedgeye_mail_archive.py --counts        # re-print counts
"""
from __future__ import annotations

import argparse
import csv
import email
import email.utils
import re
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO))

import db_pg  # noqa: E402  (loads .env so email_parser's import-time env reads work)
db_pg._load_dotenv_fallback()

OUT = REPO / "data" / "hedgeye_mail"
MANIFEST = OUT / "manifest.csv"
FIELDS = ["folder", "uidvalidity", "uid", "date", "from", "subject",
          "product", "file"]

PRODUCTS = [
    ("early look", "Early Look"),
    ("macro show", "Macro Show"),
    ("the call", "The Call"),
    ("etf pro", "ETF Pro/Re-Rank"), ("re-rank", "ETF Pro/Re-Rank"),
    ("rerank", "ETF Pro/Re-Rank"),
    ("macro themes", "Themes/MidQ/Monitor"),
    ("mid-quarter", "Themes/MidQ/Monitor"), ("mid quarter", "Themes/MidQ/Monitor"),
    ("monthly monitor", "Themes/MidQ/Monitor"),
    ("inflation nowcast", "Inflation Nowcast"),
    ("risk range", "Risk Range"),
]

# Lean-pull set (operator, 2026-09-07): bodies only for the parse-relevant
# products; everything else stays manifest-only.
PARSE_PRODUCTS = {"Early Look", "Macro Show", "The Call", "ETF Pro/Re-Rank",
                  "Themes/MidQ/Monitor", "Inflation Nowcast", "Risk Range"}


def product_guess(subject: str) -> str:
    s = (subject or "").lower()
    for key, name in PRODUCTS:
        if key in s:
            return name
    return "other"


def slug(s: str, n=40) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s or "no-subject").strip("-")[:n]


def load_manifest() -> dict:
    rows = {}
    if MANIFEST.exists():
        with MANIFEST.open(newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                # reclassify on load so new product rules apply retroactively
                r["product"] = product_guess(r["subject"])
                rows[(r["folder"], r["uidvalidity"], r["uid"])] = r
    return rows


def save_manifest(rows: dict) -> None:
    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows.values(), key=lambda x: (x["date"], x["uid"])):
            w.writerow(r)


def decode_subj(raw) -> str:
    from email.header import decode_header
    out = []
    try:
        for part, enc in decode_header(raw or ""):
            out.append(part.decode(enc or "utf-8", "replace")
                       if isinstance(part, bytes) else part)
    except Exception:
        out.append(str(raw))
    return "".join(out)


def hedgeye_folders(conn) -> list:
    typ, data = conn.list()
    folders = ["INBOX"]
    for line in data or []:
        try:
            name = line.decode("utf-8", "replace").rsplit(' "/" ', 1)[-1].strip('"')
        except Exception:
            continue
        if "hedgeye" in name.lower() and name not in folders:
            folders.append(name)
    return folders


def counts(rows: dict) -> None:
    from collections import Counter
    c = Counter()
    for r in rows.values():
        yr = (r["date"] or "????")[:4]
        c[(r["product"], yr)] += 1
    prods = sorted({p for p, _ in c})
    years = sorted({y for _, y in c})
    print(f"\n{'product':<22}" + "".join(f"{y:>7}" for y in years) + f"{'total':>8}")
    for p in prods:
        row = [c.get((p, y), 0) for y in years]
        print(f"{p:<22}" + "".join(f"{n:>7}" for n in row) + f"{sum(row):>8}")
    tot = [sum(c.get((p, y), 0) for p in prods) for y in years]
    print(f"{'TOTAL':<22}" + "".join(f"{n:>7}" for n in tot) + f"{sum(tot):>8}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest-only", action="store_true")
    ap.add_argument("--counts", action="store_true")
    ap.add_argument("--all-bodies", action="store_true",
                    help="pull every body (default: PARSE_PRODUCTS only)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_manifest()
    if args.counts:
        counts(rows)
        return 0

    from email_parser import connect_imap
    conn = connect_imap()
    try:
        for folder in hedgeye_folders(conn):
            typ, sel = conn.select(f'"{folder}"', readonly=True)
            if typ != "OK":
                print(f"  {folder}: select failed ({typ}) — skipped")
                continue
            typ, uv = conn.response("UIDVALIDITY")
            uidvalidity = (uv[0].decode() if uv and uv[0] else "0")
            typ, data = conn.uid("search", None,
                                 "FROM", '"hedgeye"', "SINCE", "01-Jan-2022")
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            new = [u for u in uids
                   if (folder, uidvalidity, u.decode()) not in rows]
            print(f"  {folder}: {len(uids)} matches, {len(new)} new")
            # header pass (manifest)
            for i in range(0, len(new), 100):
                batch = b",".join(new[i:i + 100])
                typ, parts = conn.uid(
                    "fetch", batch,
                    "(BODY.PEEK[HEADER.FIELDS (DATE FROM SUBJECT)])")
                if typ != "OK":
                    print(f"    header batch {i}: {typ}")
                    continue
                for j in range(0, len(parts) - 1, 2):
                    meta, hdr = parts[j]
                    m = re.search(rb"UID (\d+)", meta)
                    if not m:
                        continue
                    uid = m.group(1).decode()
                    msg = email.message_from_bytes(hdr)
                    dt = email.utils.parsedate_to_datetime(msg.get("Date"))
                    subj = decode_subj(msg.get("Subject"))
                    rows[(folder, uidvalidity, uid)] = {
                        "folder": folder, "uidvalidity": uidvalidity,
                        "uid": uid, "date": dt.date().isoformat() if dt else "",
                        "from": msg.get("From", ""), "subject": subj,
                        "product": product_guess(subj), "file": ""}
                save_manifest(rows)
            # body pass (.eml), resumable — lean pull: parse products only
            # unless --all-bodies
            if not args.manifest_only:
                pend = [k for k, r in rows.items()
                        if k[0] == folder and k[1] == uidvalidity
                        and not r["file"]
                        and (args.all_bodies
                             or r["product"] in PARSE_PRODUCTS)]
                for n, key in enumerate(pend):
                    r = rows[key]
                    typ, parts = conn.uid("fetch", r["uid"], "(BODY.PEEK[])")
                    if typ != "OK" or not parts or parts[0] is None:
                        print(f"    body uid {r['uid']}: {typ} — skipped")
                        continue
                    raw = parts[0][1]
                    fname = f"{r['date']}_{slug(r['subject'])}_{r['uid']}.eml"
                    (OUT / fname).write_bytes(raw)
                    r["file"] = fname
                    if (n + 1) % 100 == 0:
                        save_manifest(rows)
                        print(f"    bodies: {n + 1}/{len(pend)}")
                save_manifest(rows)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    done = sum(1 for r in rows.values() if r["file"])
    print(f"\nmanifest: {len(rows)} messages; bodies saved: {done}")
    counts(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
