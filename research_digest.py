"""Research digest: what each Hedgeye report says, by report and by name.

Storage only needs Postgres. Extraction has two routes:
  - --load FILE: a digest written by hand at the desk (no API spend)
  - extract(): Claude API, gated by RESEARCH_DIGEST_ENABLED=1 like the
    classifier gate in classifier.py (operator turned per-email spend off
    2026-07-11; turning this on is Kris's call). The key is Railway-only.

Every thesis is a paraphrase. basis='stated' means the report says it;
'inferred' means the name was read through from the report and the report
never named a trade, so the two never read alike at the desk.

CLI: py research_digest.py [--pending [DAYS] [--dump DIR] | --load FILE | --run MESSAGE_ID |
                            --backfill DAYS | --lookup NAME | --recent DAYS]
"""
from __future__ import annotations

import argparse
import datetime as dt
from zoneinfo import ZoneInfo
import json
import logging
import os
import re
import sys

import parser_research_common as prc
from parser_research_notes import detect_product

log = logging.getLogger(__name__)

MODEL = os.environ.get("RESEARCH_DIGEST_MODEL", "claude-opus-5-5")
DIRECTIONS = {"long", "short", "bullish", "bearish", "mixed", "watch"}

# Logistics, and products whose rows dedicated parsers already store in full.
_SKIP = re.compile(
    r"\bINVITE\b|HAPPENING|CALL (NOW|TODAY|TOMORROW)|SUBMIT YOUR QUESTIONS|"
    r"GEOPOLITICAL EVENT|Access Here|Access Show|Good news:|"
    r"RISK\s+RANGE|Real-Time Alert|Signal Strength|Keith'?s Signal|Daily ETF Re-Rank|"
    r"ETF Pro Change|Position Monitors? *\||Position Monitors Update|Weekly Position Monitor|"
    r"Model Portfolio Changes|Trend & Tail Levels|Updated Levels|"
    r"ETF Pro Plus|MARKET SITUATION REPORT: VIDEO",
    re.I)

SYSTEM = (
    "You digest one Hedgeye research email for a risk desk. Paraphrase; never quote "
    "more than a few words. Report only what the email supports. Set basis to 'stated' "
    "when the email itself names the ticker or theme and its direction; 'inferred' when "
    "you are reading a name through from the content. Leave calls empty if there are none."
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "regime_read", "process_read", "calls"],
    "properties": {
        "summary": {"type": "string"},
        "regime_read": {"type": "string"},
        "process_read": {"type": "string"},
        "calls": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "name", "direction", "basis", "thesis"],
            "properties": {
                "kind": {"type": "string", "enum": ["ticker", "theme"]},
                "name": {"type": "string"},
                "direction": {"type": "string", "enum": sorted(DIRECTIONS)},
                "basis": {"type": "string", "enum": ["stated", "inferred"]},
                "thesis": {"type": "string"},
            },
        }},
    },
}


def is_research(subject: str) -> bool:
    s = (subject or "").strip()
    return bool(s) and not _SKIP.search(s)


def product_of(subject: str) -> str:
    known = detect_product(subject)
    if known:
        return known
    head = re.split(r"[|:]", re.sub(r"\s+", " ", subject or ""))[0]
    return re.sub(r"[^a-z0-9]+", "_", head.lower()).strip("_")[:40] or "other"


def same_report(subject: str) -> str:
    """Reissues differ only by a tag and line wrap: '(*Chartbook Link Fixed*) ...'."""
    s = re.sub(r"^\s*\(\*.*?\*\)", "", subject or "")
    return re.sub(r"\s+", " ", s).strip().lower()


def is_reissue(key: str, day: dt.date, seen: set) -> bool:
    """Pure: a reissue lands the same ET day as the original. The same title on another day is a
    new report (Go-Anywhere, L1 Watch, the monthly Nowcast), not a repeat."""
    return (key, day) in seen


def validate(d: dict) -> dict:
    """Raises ValueError on anything that would mislead at the desk."""
    if not (d.get("summary") or "").strip():
        raise ValueError("digest has no summary")
    seen = set()
    for c in d.get("calls") or []:
        if c.get("kind") not in ("ticker", "theme"):
            raise ValueError(f"bad kind: {c}")
        if c.get("direction") not in DIRECTIONS:
            raise ValueError(f"bad direction: {c}")
        if c.get("basis") not in ("stated", "inferred"):
            raise ValueError(f"bad basis: {c}")
        if not (c.get("name") or "").strip() or not (c.get("thesis") or "").strip():
            raise ValueError(f"call needs name and thesis: {c}")
        if c["kind"] == "ticker":
            c["name"] = c["name"].upper().strip()
        key = (c["kind"], c["name"])
        if key in seen:
            raise ValueError(f"duplicate call: {key}")
        seen.add(key)
    return d


def store(message_id: str, d: dict, extractor: str) -> dict:
    """Upsert one digest and its calls, then note tickers in the inventory.

    position stays None: a research mention must not overwrite Keith's
    current_position, which the signal parsers own.
    """
    import db_pg
    import ticker_inventory as ti

    validate(d)
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT subject, received_at FROM hedgeye_emails_raw WHERE message_id=%s",
                    (message_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"no email {message_id}")
        subject, received = row[0], row[1]
        product = d.get("product") or product_of(subject)
        day = d.get("signal_date") or received.date()
        cur.execute(
            """INSERT INTO research_digest
                 (message_id, signal_date, product, title, summary, regime_read,
                  process_read, data, extractor)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (message_id) DO UPDATE SET
                 signal_date=EXCLUDED.signal_date, product=EXCLUDED.product,
                 title=EXCLUDED.title, summary=EXCLUDED.summary,
                 regime_read=EXCLUDED.regime_read, process_read=EXCLUDED.process_read,
                 data=EXCLUDED.data, extractor=EXCLUDED.extractor
               RETURNING id""",
            (message_id, day, product, re.sub(r"\s+", " ", subject), d["summary"],
             d.get("regime_read"), d.get("process_read"),
             json.dumps(d.get("data") or {}), extractor))
        digest_id = cur.fetchone()[0]
        cur.execute("DELETE FROM research_calls WHERE digest_id=%s", (digest_id,))
        for c in d.get("calls") or []:
            cur.execute(
                """INSERT INTO research_calls
                     (digest_id, signal_date, product, kind, name, direction, basis, thesis)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (digest_id, day, product, c["kind"], c["name"], c["direction"],
                 c["basis"], c["thesis"]))
        conn.commit()
    noted = 0
    for c in d.get("calls") or []:
        if c["kind"] == "ticker" and ti.note_ticker(
                c["name"], ti.SOURCE_RESEARCH_DIGEST, message_id=message_id,
                metadata={"product": product, "direction": c["direction"],
                          "basis": c["basis"], "thesis": c["thesis"]}):
            noted += 1
    return {"digest_id": digest_id, "calls": len(d.get("calls") or []), "inventory": noted}


def extract(subject: str, body: str) -> dict:
    """One structured-output call. Raises on refusal or truncation."""
    import anthropic

    resp = anthropic.Anthropic().messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "medium",
                       "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": f"SUBJECT: {subject}\n\n{body}"}],
    )
    if resp.stop_reason != "end_turn":
        raise RuntimeError(f"digest stopped: {resp.stop_reason}")
    return json.loads(next(b.text for b in resp.content if b.type == "text"))


def process_one(message_id: str) -> dict:
    """Parser hook: no-op unless the gate is open, the email is research, and undigested."""
    if os.environ.get("RESEARCH_DIGEST_ENABLED", "0") != "1":
        return {"skipped": "gated"}
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.subject, e.text_body, e.html_body, d.id
                 FROM hedgeye_emails_raw e
                 LEFT JOIN research_digest d ON d.message_id = e.message_id
                WHERE e.message_id=%s""", (message_id,))
        row = cur.fetchone()
    if not row or row[3] or not is_research(row[0]):
        return {"skipped": "not research or done"}
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT title FROM research_digest
                        WHERE signal_date >= CURRENT_DATE - 7""")
        if same_report(row[0]) in {same_report(t[0]) for t in cur.fetchall()}:
            return {"skipped": "reissue already digested"}
    return store(message_id, extract(row[0], prc.text_of(row[1], row[2])), MODEL)


def pending(days: int) -> list[tuple]:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT e.message_id, e.received_at, e.subject
                 FROM hedgeye_emails_raw e
                 LEFT JOIN research_digest d ON d.message_id = e.message_id
                WHERE d.id IS NULL AND e.received_at > NOW() - make_interval(days => %s)
                ORDER BY e.received_at""", (days,))
        rows = cur.fetchall()
        cur.execute("""SELECT title, signal_date FROM research_digest
                        WHERE signal_date >= CURRENT_DATE - %s::int""", (days + 7,))
        seen = {(same_report(t), d) for t, d in cur.fetchall()}
    out = []
    for r in rows:
        key, day = same_report(r[2]), r[1].astimezone(ZoneInfo("America/New_York")).date()
        if is_research(r[2]) and not is_reissue(key, day, seen):
            seen.add((key, day))
            out.append(r)
    return out


def lookup(name: str) -> list[tuple]:
    import db_pg
    with db_pg.get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT signal_date, product, direction, basis, thesis FROM research_calls
                WHERE upper(name)=upper(%s) ORDER BY signal_date DESC, id DESC""", (name,))
        return cur.fetchall()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    import db_pg
    db_pg._load_dotenv_fallback()
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending", type=int, nargs="?", const=7)
    ap.add_argument("--load")
    ap.add_argument("--dump", help="write undigested emails from --pending DAYS (default 2) to this folder")
    ap.add_argument("--run")
    ap.add_argument("--backfill", type=int)
    ap.add_argument("--lookup")
    ap.add_argument("--recent", type=int)
    a = ap.parse_args()
    if a.load:
        with open(a.load, encoding="utf-8") as f:
            docs = json.load(f)
        for d in docs if isinstance(docs, list) else [docs]:
            print(d["message_id"][:12], store(d.pop("message_id"), d, d.pop("extractor", "desk")))
    elif a.dump:
        rows = pending(a.pending if a.pending is not None else 2)
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            for i, (mid, when, subj) in enumerate(rows):
                cur.execute("SELECT text_body, html_body FROM hedgeye_emails_raw WHERE message_id=%s",
                            (mid,))
                body = prc.text_of(*cur.fetchone())
                with open(os.path.join(a.dump, f"{i:03d}.txt"), "w", encoding="utf-8") as f:
                    f.write(f"ID {mid}\nDATE {when.date()}\nSUBJ {re.sub(r'\s+', ' ', subj)}\n\n{body}")
        print(f"{len(rows)} written to {a.dump}")
    elif a.run:
        print(process_one(a.run))
    elif a.backfill is not None:
        for mid, _, subj in pending(a.backfill):
            try:
                print(subj[:60].replace("\r\n", " "), process_one(mid))
            except Exception as e:
                print("FAILED", subj[:60], e)
    elif a.lookup:
        for r in lookup(a.lookup):
            print(*r, sep=" | ")
    elif a.recent is not None:
        with db_pg.get_conn() as conn, conn.cursor() as cur:
            cur.execute("""SELECT signal_date, product, title, summary FROM research_digest
                            WHERE signal_date >= CURRENT_DATE - %s::int
                            ORDER BY signal_date DESC""", (a.recent,))
            for r in cur.fetchall():
                print(*r, sep=" | ")
    else:
        rows = pending(a.pending if a.pending is not None else 7)
        for _, when, subj in rows:
            print(when.date(), re.sub(r"\s+", " ", subj)[:90])
        print(f"{len(rows)} undigested")


if __name__ == "__main__":
    main()
