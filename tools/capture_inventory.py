"""Capture inventory: one row per Hedgeye item, and what of it is in the bot (capture desk, 11/14/2026).

Kris pays for every email, the full portal report behind it, the video and the slides.
This lists each item from two sources and checks each piece on disk:
  - hedgeye_emails_raw (every email the bot received)
  - data/hedgeye/portal_list.json (portal items, incl. portal-only videos and sector notes;
    refreshed from the signed-in Hedgeye tab by the capture desk)

    py -m tools.capture_inventory                 # rebuild data/capture_inventory.csv (14 days) + print gaps
    py -m tools.capture_inventory --days 3 --gaps # gaps only
    py -m tools.capture_inventory --merge-portal <file.json>   # merge a fresh portal listing

Columns: date, team, item, email, full_report_path, transcript_path, slides_path, in_bot,
feed_id, needs (the missing pieces). Read-only on the database.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = pathlib.Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "data" / "hedgeye" / "research"
TRANSCRIPTS = ROOT / "data" / "transcripts"
PORTAL = ROOT / "data" / "hedgeye" / "portal_list.json"
OUT = ROOT / "data" / "capture_inventory.csv"
COLS = ["date", "team", "item", "email", "full_report_path", "transcript_path", "slides_path",
        "in_bot", "feed_id", "needs"]

VERTICALS = {  # portal vertical -> team
    "Classic Retail Pro": "retail", "Classic Financials Pro": "financials", "Capital Allocation Pro": "cap-alloc",
    "Digital Assets Pro": "crypto", "Bitcoin Trend Tracker": "crypto", "Momentum Stock Tracker": "momo",
    "HedgAI Signals": "financials", "Market Situation Report": "t1a", "The Big Theme": "special",
    "Macro Policy": "macro", "Pro Risk Manager": "macro", "ETF Pro Plus": "macro", "Early Look": "macro",
    "Founder's Choice": "macro", "From the Desk": "macro", "Go-Anywhere Portfolio": "macro", "Insights": "macro",
    "Investing Ideas": "macro", "Quarterly Investment Outlook": "macro", "Real-Time Alerts": "macro",
    "Risk Range™ Signals": "macro", "Signal Strength Shorts": "macro", "Signal Strength Stocks": "macro",
    "The Call @ Hedgeye": "macro", "The Macro Show": "macro", "Macro": "macro",
}
_VERT_RE = "|".join(re.escape(v) for v in sorted(VERTICALS, key=len, reverse=True))
_PORTAL_RE = re.compile(rf"^(\d\d)/(\d\d)/(\d\d) \d\d?:\d\d [AP]M E[SD]T\s*({_VERT_RE})?\s*(.*)$")
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}

# Items that carry a video/audio: (pattern, transcript subfolder, how to date it, days of slack)
VIDEO_RULES = [
    (r"^VIDEO \| Retail \(", "retail_video", "title", 0),
    (r"^VIDEO \| Financials, Macro & Housing", "financials_video", "title", 0),
    (r"Market Situation Report Webcast", "msr_webcast", "title", 0),
    (r"The Big Theme \| AI Bubble \| .* Update", "big_theme", "item", 1),
    (r"The Call @ Hedgeye \| (REPLAY|Replay & Summary)", "the_call", "title", 0),
    (r"THE CALL @ HEDGEYE \| (REPLAY|Replay)", "the_call", "title", 0),
    (r"Summary Notes & Replay \|", "macro_show", "title", 0),
    (r"CALL REPLAY \| Q\d 20\d\d MACRO THEMES", "macro_themes", "item", 4),   # replay re-sent days later (11/15 for the 11/12 call)
    (r"^REPLAY \| ([A-Z]{1,5}) \|", "{tkr}_call", "item", 1),
    (r"Call Replay \| .*Housing", "housing_call", "item", 6),
    (r"Capital Allocation Pro\"? Webcast", "capital_allocation", "title", 7),
    (r"Key Callouts|VIDEO \| Model Portfolio Update", "capital_allocation", "title", 0),
    (r"Protect the Pile Episode", "protect_the_pile", "item", 1),   # Kris 11/15: capture every episode
]
SLIDE_HINT = re.compile(r"slide|deck|chartbook|black book|webcast|big theme|macro show.*access", re.I)
# Free marketing / Insights pieces: listed for completeness, never a gap.
INSIGHTS = re.compile(r"Protect the Pile|Prediction Market Desk|REAL CONVERSATIONS|CHART OF THE DAY|^McCullough:|"
                      r"^Why |The Team That Called|Investing Summit|Preparing for Quad 4", re.I)
SKIP = re.compile(r"\bINVITE\b|FLASH EVENT TODAY|HAPPENING (NOW|TODAY|TOMORROW|IN)|SUBMIT YOUR QUESTIONS|CALL (TODAY|NOW)\b|"
                  r"^EVENT \||Hedgeye Investing Summit", re.I)


def team_for(sender: str, subject: str, vertical: str = "") -> str:
    s, subj = (sender or "").lower(), subject or ""
    if re.search(r"^VIDEO \| Financials", subj, re.I):
        return "financials"
    if re.search(r"The Call @ Hedgeye|THE MACRO SHOW|Summary Notes & Replay", subj, re.I):
        return "macro"
    if INSIGHTS.search(subj):
        return "insights"
    if re.search(r"\bHOUSING\b|Housing Market Tracker", subj, re.I):
        return "housing"
    if vertical in VERTICALS and VERTICALS[vertical] != "macro":
        return VERTICALS[vertical]
    if "retail pro" in s or re.search(r"Retail (Soundbites|EDGE|Callouts)|RETAIL CALLOUTS|^VIDEO \| Retail", subj, re.I):
        return "retail"
    if "financials pro" in s or re.search(r"HedgAI|Credit Card|Financials? Earnings Recap|Loan Officer|L1 Watch|"
                                          r"^VIDEO \| Financials", subj, re.I):
        return "financials"
    if re.search(r"Capital Allocation|Model Portfolio|Key Callouts|MP Cheat Sheet|Updated Levels \| Daily Dashboard", subj, re.I):
        return "cap-alloc"
    if re.search(r"CRYPTO QUANT|Digital Assets|Bitcoin|Exchange Tracker", subj, re.I):
        return "crypto"
    if re.search(r"MOMO Tracker|Momentum Stock", subj, re.I):
        return "momo"
    if re.search(r"\bQIO\b", subj):
        return "qio"
    if re.search(r"MARKET SITUATION REPORT|Tier ?1 ?Alpha", subj, re.I):
        return "t1a"
    if re.search(r"Big Theme|REPLAY \||Call Replay|Webcast|INVITE", subj, re.I):
        return "special"
    if "sectorpro" in s:
        return "sector-other"
    return "macro"


def _title_date(title: str) -> dt.date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", title)
    if m:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.search(r"([A-Za-z]+) (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})", title)
    if m and m.group(1).lower() in MONTHS:
        return dt.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
    return None


def transcript_for(title: str, item_date: dt.date) -> tuple[bool, str]:
    """(video expected?, transcript path or '')."""
    for pat, sub, how, slack in VIDEO_RULES:
        m = re.search(pat, title)
        if not m:
            continue
        if "{tkr}" in sub:
            sub = sub.format(tkr=m.group(1).lower())
        d = (_title_date(title) if how == "title" else None) or item_date
        folder = TRANSCRIPTS / sub
        for k in range(-slack, 2 if slack else 1):
            f = folder / f"{(d + dt.timedelta(days=k)).isoformat()}.txt"
            if f.exists():
                return True, str(f.relative_to(ROOT))
        return True, ""
    return False, ""


def _report_folders() -> dict[str, pathlib.Path]:
    out = {}
    if RESEARCH.exists():
        for day in RESEARCH.iterdir():
            if day.is_dir() and re.fullmatch(r"\d{4}-\d\d-\d\d", day.name):
                for f in day.iterdir():
                    m = re.match(r"(\d{5,7})_", f.name)
                    if m and f.is_dir():
                        out[m.group(1)] = f
    return out


def _slides(folder: pathlib.Path | None, title: str, d: dt.date) -> str:
    if folder is not None:
        pdfs = sorted(folder.glob("*.pdf")) + sorted(folder.glob("*.xlsx"))
        if pdfs:
            return ";".join(str(p.relative_to(ROOT)) for p in pdfs[:3])
    if re.search(r"Big Theme", title, re.I):
        for k in range(-2, 2):
            snap = ROOT / "data" / "snapshots" / "hedgeye" / (d + dt.timedelta(days=k)).isoformat()
            pdfs = sorted(snap.glob("*.pdf")) if snap.exists() else []
            if pdfs:
                return ";".join(str(p.relative_to(ROOT)) for p in pdfs[:3])
    return ""


def load_portal() -> dict[str, dict]:
    raw = json.loads(PORTAL.read_text(encoding="utf-8")) if PORTAL.exists() else {}
    out = {}
    for fid, text in raw.items():
        m = _PORTAL_RE.match(text.strip())
        if m:   # advanced-search card: "MM/DD/YY HH:MM AM EST<Vertical><Title>"
            mm, dd, yy, vert, title = m.groups()
            out[fid] = {"date": dt.date(2000 + int(yy), int(mm), int(dd)), "vertical": vert or "", "title": title.strip()}
        else:   # keyword-search card: title only; dated in build() from the title or neighbouring feed ids
            out[fid] = {"date": None, "vertical": "", "title": " ".join(text.split())}
    return out


def _date_from_fid(fid: str, known: list[tuple[int, dt.date]]) -> dt.date | None:
    """Feed ids rise with time: take the date of the nearest emailed feed id at or below it."""
    n, best = int(fid), None
    for k, d in known:
        if k <= n:
            best = d
        else:
            break
    return best


def merge_portal(path: str) -> int:
    new = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    cur = json.loads(PORTAL.read_text(encoding="utf-8")) if PORTAL.exists() else {}
    before = len(cur)
    cur.update({str(k): v for k, v in new.items()})
    PORTAL.parent.mkdir(parents=True, exist_ok=True)
    PORTAL.write_text(json.dumps(dict(sorted(cur.items())), indent=0, ensure_ascii=False), encoding="utf-8")
    return len(cur) - before


def build(days: int = 14) -> list[dict]:
    import db_pg
    db_pg._load_dotenv_fallback()
    since = dt.datetime.now(ET).date() - dt.timedelta(days=days)
    folders = _report_folders()
    rows: dict[str, dict] = {}
    with db_pg.get_conn() as c, c.cursor() as cur:
        cur.execute(r"""SELECT (received_at AT TIME ZONE 'America/New_York')::date, sender, subject, message_id,
                               substring(coalesce(html_body,'') || ' ' || coalesce(text_body,'')
                                         from 'app\.hedgeye\.com/feed_items/(\d+)'),
                               full_report_fetched_at IS NOT NULL
                          FROM hedgeye_emails_raw WHERE received_at >= %s ORDER BY received_at""", (since,))
        for d, sender, subj, mid, fid, fetched in cur.fetchall():
            subj = " ".join((subj or "").split())
            key = fid or f"mail:{mid}"
            r = rows.setdefault(key, {"date": d, "team": team_for(sender, subj), "item": subj, "email": "yes",
                                      "feed_id": fid or "", "_fetched": fetched})
            r["_fetched"] = r["_fetched"] or fetched
    known = sorted((int(r["feed_id"]), r["date"]) for r in rows.values() if r["feed_id"])
    for fid, p in load_portal().items():
        if p["date"] is None:
            p["date"] = _title_date(p["title"]) or _date_from_fid(fid, known)
        if p["date"] is None or p["date"] < since:
            continue
        r = rows.get(fid)
        if r is None:
            rows[fid] = {"date": p["date"], "team": team_for("", p["title"], p["vertical"]), "item": p["title"],
                         "email": "no", "feed_id": fid, "_fetched": False}
        elif p["vertical"] in VERTICALS and VERTICALS[p["vertical"]] != "macro" and r["team"] in ("macro", "special"):
            r["team"] = team_for("", r["item"], p["vertical"])
    out = []
    for key, r in rows.items():
        folder = folders.get(r["feed_id"])
        video, tpath = transcript_for(r["item"], r["date"])
        skip = bool(SKIP.search(r["item"]))
        report = str(folder.relative_to(ROOT)) if folder else ("(in email row)" if r["_fetched"] else "")
        slides = _slides(folder, r["item"], r["date"])
        needs = []
        optional = r["team"] == "insights" and r["email"] == "no"
        if not report and not skip and not optional and not (video and tpath):
            needs.append("report")
        if video and not tpath:
            needs.append("transcript")
        if SLIDE_HINT.search(r["item"]) and not slides and not skip and not video:
            needs.append("slides?")
        out.append({"date": r["date"].isoformat(), "team": r["team"], "item": r["item"][:160], "email": r["email"],
                    "full_report_path": report, "transcript_path": tpath, "slides_path": slides,
                    "in_bot": "yes" if (r["email"] == "yes" or folder or tpath) else "no",
                    "feed_id": r["feed_id"],
                    "needs": "skip (invite/event)" if skip else ("optional (free insights)" if optional else "+".join(needs))})
    out.sort(key=lambda x: (x["date"], x["team"], x["item"]))
    return out


def write(rows: list[dict]) -> None:
    """Rewrite the CSV: rows inside the rebuilt window replace the old ones, and older rows
    already in the file are kept (11/17: a rebuild used to drop everything past 14 days)."""
    if rows and OUT.exists():
        start = min(r["date"] for r in rows)
        with OUT.open(newline="", encoding="utf-8") as f:
            older = [{c: r.get(c, "") for c in COLS} for r in csv.DictReader(f)
                     if (r.get("date") or "") < start]
        rows = sorted(older + rows, key=lambda r: r["date"])
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def gaps(rows: list[dict], days: int | None = None) -> list[dict]:
    cut = (dt.datetime.now(ET).date() - dt.timedelta(days=days)).isoformat() if days else ""
    return [r for r in rows if r["needs"] and not r["needs"].startswith(("skip", "optional")) and r["date"] >= cut]


def _cli() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--gaps", action="store_true", help="print gaps only, don't rewrite the CSV")
    ap.add_argument("--merge-portal")
    a = ap.parse_args()
    if a.merge_portal:
        print(f"portal list: +{merge_portal(a.merge_portal)} items -> {PORTAL}")
    rows = build(max(a.days, 14))
    if not a.gaps:
        write(rows)
        print(f"{OUT}: {len(rows)} rows")
    g = gaps(rows, a.days)
    by = {}
    for r in g:
        by.setdefault(r["needs"], []).append(r)
    print(f"GAPS (last {a.days} days): {len(g)}")
    for need, rs in sorted(by.items()):
        print(f"  {need}: {len(rs)}")
        for r in rs[:40]:
            print(f"    {r['date']} {r['team']:11} {r['feed_id']:7} {r['item'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
