"""Capture the full Hedgeye reports behind the emails.

Report links need Kris's signed-in Chrome (plain requests get 403, headless is
reCAPTCHA-blocked), so this splits the job around one in-page script:

    py -m tools.report_capture queue    # picks uncaptured emails, prints the JS to run
    (run the JS in an app.hedgeye.com tab: it saves one bundle to Downloads)
    py -m tools.report_capture ingest   # unpacks it into data/hedgeye/research/<date>/
    py -m tools.report_capture status

No LLM calls. The feed-item id comes from the email body, so no link gets clicked.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

import db_pg

ROOT = Path(__file__).resolve().parents[1] / "data" / "hedgeye" / "research"
DOWNLOADS = Path.home() / "Downloads"
QUEUE = ROOT / "_queue.json"
STATE = ROOT / "_state.json"
INDEX = ROOT / "_index.json"
LOG = ROOT / "_capture_log.csv"
JS = Path(__file__).with_suffix(".js")
ET = ZoneInfo("America/New_York")

# Event notices, not reports.
SKIP_SUBJECT = re.compile(r"\bINVITE\b|HAPPENING (NOW|TODAY|TOMORROW|IN)|SUBMIT YOUR QUESTIONS|FLASH EVENT TODAY", re.I)
MAX_FAILS = 3
EXT = {"application/pdf": ".pdf", "text/html": ".html", "image/png": ".png", "image/jpeg": ".jpg",
       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
       "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx"}


def _read(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=0), encoding="utf-8")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60] or "report"


def pending(days: int) -> dict[str, dict]:
    """Uncaptured feed items, newest first: {feed_id: {subject, received_at, message_ids}}."""
    db_pg._load_dotenv_fallback()
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(
            r"""SELECT substring(coalesce(html_body,'') || ' ' || coalesce(text_body,'')
                                 from 'app\.hedgeye\.com/feed_items/(\d+)') AS fid,
                       message_id, subject, received_at
                  FROM hedgeye_emails_raw
                 WHERE received_at >= now() - make_interval(days => %s)
                   AND full_report_fetched_at IS NULL
                 ORDER BY received_at DESC""",
            (days,),
        )
        rows = cur.fetchall()
    fails = _read(STATE, {})
    out: dict[str, dict] = {}
    for fid, mid, subj, when in rows:
        if not fid or SKIP_SUBJECT.search(subj or "") or fails.get(fid, 0) >= MAX_FAILS:
            continue
        rec = out.setdefault(fid, {"subject": " ".join((subj or "").split()),
                                   "received_at": when.isoformat(), "message_ids": []})
        rec["message_ids"].append(mid)
    return out


def cmd_queue(days: int, limit: int) -> None:
    all_pending = pending(days)
    batch = dict(list(all_pending.items())[:limit])
    _write(QUEUE, batch)
    if not batch:
        print("QUEUE EMPTY")
        return
    print(f"QUEUE {len(batch)} of {len(all_pending)} pending")
    print(JS.read_text(encoding="utf-8").replace("__IDS__", json.dumps([int(k) for k in batch])))


def _extract_pdf(path: Path, folder: Path) -> int:
    """Write <stem>.txt; render pages that are mostly pictures so charts can be read."""
    import pymupdf
    doc = pymupdf.open(path)
    parts, rendered = [], 0
    for i, page in enumerate(doc, 1):
        text = page.get_text().strip()
        parts.append(f"--- page {i} ---\n{text}")
        if len(text) < 200 and rendered < 40:
            page.get_pixmap(dpi=110).save(folder / f"{path.stem}_p{i}.png")
            rendered += 1
    path.with_suffix(".txt").write_text("\n\n".join(parts), encoding="utf-8")
    pages = doc.page_count
    doc.close()
    return pages


def _html_text(html: str) -> str:
    from bs4 import BeautifulSoup
    return re.sub(r"\n{3,}", "\n\n", BeautifulSoup(html, "html.parser").get_text("\n", strip=True))


def _save_files(fh, base: int, files: list[dict], folder: Path, stats: Counter) -> tuple[list, list]:
    saved, problems = [], []
    for f in files:
        if "offset" not in f:
            problems.append(f)
            stats["file_problem"] += 1
            continue
        fh.seek(base + f["offset"])
        data = fh.read(f["size"])
        # S3 often labels PDFs as binary/octet-stream, so trust the file's own header.
        ext = ".pdf" if data[:5] == b"%PDF-" else EXT.get(f["ct"].split(";")[0].strip().lower(), ".bin")
        target = folder / (_slug(f["name"]) + ext)
        target.write_bytes(data)
        entry = {"file": target.name, "size": f["size"]}
        if ext == ".pdf":
            try:
                entry["pages"] = _extract_pdf(target, folder)
            except Exception as exc:  # one bad PDF shouldn't lose the rest of the bundle
                problems.append({"name": f["name"], "err": str(exc)[:100]})
        elif ext == ".html":
            target.with_suffix(".txt").write_text(_html_text(target.read_text("utf-8", "replace")), encoding="utf-8")
        saved.append(entry)
        stats["files"] += 1
    return saved, problems


def _unpack(bundle: Path, queue: dict) -> Counter:
    stats: Counter = Counter()
    fails, index = _read(STATE, {}), _read(INDEX, {})
    rows = []
    db_pg._load_dotenv_fallback()
    with bundle.open("rb") as fh, db_pg.get_conn() as c, c.cursor() as cur:
        head = json.loads(fh.readline().decode("utf-8"))
        base = fh.tell()
        for it in head["items"]:
            fid = str(it["id"])
            rec = queue.get(fid)
            if rec is None:
                stats["unknown"] += 1
                continue
            if it.get("st") != 200 or not it.get("html"):
                fails[fid] = fails.get(fid, 0) + 1
                stats["failed"] += 1
                print(f"  FAILED {fid} st={it.get('st')} {it.get('err', '')} {rec['subject'][:60]}")
                continue
            day = dt.datetime.fromisoformat(rec["received_at"]).astimezone(ET).date().isoformat()
            folder = ROOT / day / f"{fid}_{_slug(rec['subject'])}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "page.html").write_text(it["html"], encoding="utf-8")
            (folder / "page.txt").write_text(f"{rec['subject']}\n\n{_html_text(it['html'])}", encoding="utf-8")
            saved, problems = _save_files(fh, base, it["files"], folder, stats)
            (folder / "meta.json").write_text(json.dumps(
                {"feed_id": fid, "subject": rec["subject"], "received_at": rec["received_at"],
                 "message_ids": rec["message_ids"], "files": saved, "problems": problems}, indent=1), encoding="utf-8")
            cur.execute("UPDATE hedgeye_emails_raw SET full_report_html=%s, full_report_fetched_at=now() WHERE message_id=ANY(%s)",
                        (it["html"], rec["message_ids"]))
            c.commit()
            rel = str(folder.relative_to(ROOT))
            for mid in rec["message_ids"]:
                index[mid] = rel
            fails.pop(fid, None)
            stats["reports"] += 1
            rows.append((dt.datetime.now(ET).isoformat(timespec="seconds"), day, fid, rec["subject"],
                         len(saved), len(problems), rel))
            print(f"  {day} {fid} files={len(saved)} problems={len(problems)} {rec['subject'][:70]}")
    _write(STATE, fails)
    _write(INDEX, index)
    fresh = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as out:
        w = csv.writer(out)
        if fresh:
            w.writerow(["captured_at", "email_date", "feed_id", "subject", "files", "problems", "folder"])
        w.writerows(rows)
    return stats


def cmd_ingest() -> None:
    bundles = sorted(DOWNLOADS.glob("hedgeye_bundle_*.txt"))
    if not bundles:
        print("NO BUNDLE in Downloads")
        return
    queue = _read(QUEUE, {})
    keep = ROOT / "_bundles"
    keep.mkdir(exist_ok=True)
    for b in bundles:
        print(b.name, f"{b.stat().st_size / 1e6:.1f} MB")
        print("  ", dict(_unpack(b, queue)))
        shutil.move(str(b), keep / b.name)


def cmd_status(days: int) -> None:
    p = pending(days)
    print(f"{len(p)} reports not captured in the last {days} days; {len(_read(STATE, {}))} with failed attempts")


def captured_text(message_id: str, cap: int = 60000) -> str:
    """Full captured text for an email ('' if none): the page plus any report files."""
    rel = _read(INDEX, {}).get(message_id)
    if not rel:
        return ""
    folder = ROOT / rel
    names = ["page.txt"] + sorted(p.name for p in folder.glob("*.txt") if p.name != "page.txt")
    return "\n\n".join(f"=== {n} ===\n{(folder / n).read_text(encoding='utf-8')}" for n in names)[:cap]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["queue", "ingest", "status"])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=20)  # Hedgeye returns 429 after ~30 quick fetches
    a = ap.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "queue":
        cmd_queue(a.days, a.limit)
    elif a.cmd == "ingest":
        cmd_ingest()
    else:
        cmd_status(a.days)


if __name__ == "__main__":
    main()
