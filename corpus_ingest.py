"""Walk data/snapshots/ and insert markdown captures into corpus_documents.

Designed to run on the user's laptop via the bridge, OR on Railway directly
(DATABASE_URL is auto-injected on Railway, DATABASE_PUBLIC_URL needed locally).

Idempotent: the corpus_documents UNIQUE constraint on
(source, source_ref, source_date, page_or_segment) prevents duplicate inserts.
Re-running this script is safe and only adds new captures.

Run via the bridge:
    queue {"command": "python_script",
           "args": {"script": "corpus_ingest.py", "args": ["--dry-run"]}}

Args:
    --dry-run       parse files but don't insert
    --source FILTER only ingest captures matching this source name
    --since DATE    only ingest documents with source_date >= DATE (YYYY-MM-DD)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, date as date_cls
from pathlib import Path

REPO = Path(r"C:\Projects\hedgeye-bot")
SNAPSHOTS = REPO / "data" / "snapshots"
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


# Discoverers: each yields dicts {source, source_ref, source_date, document_type, title, full_text, metadata}
# from the file paths under data/snapshots/.

def discover_macro_show(root: Path):
    """data/snapshots/hedgeye/{YYYY-MM-DD}/macro_show/*.md"""
    base = root / "hedgeye"
    if not base.exists():
        return
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_dir.name)
        if not m:
            continue
        try:
            d = date_cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        ms = date_dir / "macro_show"
        if not ms.is_dir():
            continue
        for f in sorted(ms.iterdir()):
            if f.suffix != ".md":
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if len(text) < 100:
                continue
            # Determine source flavor by filename
            name = f.name.lower()
            if "summary_notes" in name:
                source = "macro_show_summary_notes"
            elif "dashboard" in name:
                source = "macro_show_dashboard"
            elif "deck_keyslides" in name or "extract" in name:
                source = "macro_show_deck"
            else:
                source = "macro_show_other"
            yield {
                "source": source,
                "source_ref": f.stem,
                "source_date": d,
                "document_type": "webpage_text" if "summary" in name or "dashboard" in name else "slide_deck_text",
                "page_or_segment": None,
                "title": f.stem.replace("_", " ").strip(),
                "full_text": text,
                "metadata": {"file_name": f.name, "file_size": len(text)},
            }
        # Also ingest the raw deck text dump if it exists (HE_TMS_DS_FV_*.txt)
        for f in ms.glob("HE_TMS_DS_FV_*.txt"):
            text = f.read_text(encoding="utf-8", errors="replace")
            slides = text.split("\f")
            for i, s in enumerate(slides):
                s = s.strip()
                if len(s) < 50:
                    continue
                yield {
                    "source": "macro_show_deck",
                    "source_ref": f.stem,
                    "source_date": d,
                    "document_type": "slide_deck_text",
                    "page_or_segment": i + 1,
                    "title": f"{f.stem} — slide {i+1}",
                    "full_text": s,
                    "metadata": {"file_name": f.name, "slide_count": len(slides)},
                }


def discover_university(root: Path):
    """data/snapshots/hedgeye/{YYYY-MM-DD}/university/**/*.md"""
    base = root / "hedgeye"
    if not base.exists():
        return
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_dir.name)
        if not m:
            continue
        try:
            d = date_cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        u = date_dir / "university"
        if not u.is_dir():
            continue
        for f in sorted(u.rglob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            if len(text) < 100:
                continue
            rel = f.relative_to(u)
            yield {
                "source": "hedgeye_u_lesson" if "quiz" not in f.name.lower() else "hedgeye_u_quiz",
                "source_ref": str(rel).replace("\\", "/"),
                "source_date": d,
                "document_type": "webpage_text",
                "page_or_segment": None,
                "title": f.stem.replace("_", " ").strip(),
                "full_text": text,
                "metadata": {"chapter": rel.parts[0] if rel.parts else None, "file_name": f.name},
            }


def discover_volsignals_paid(root: Path):
    """data/snapshots/volsignals/paid_course/text/*.txt — Natenberg + VolStudies PDFs as text"""
    base = root / "volsignals" / "paid_course" / "text"
    if not base.exists():
        return
    for f in sorted(base.iterdir()):
        if f.suffix != ".txt":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if len(text) < 200:
            continue
        # filename-derived ref (drop VS_ prefix)
        ref = f.stem
        if ref.startswith("VS_"):
            ref = ref[3:]
        d = date_cls.fromtimestamp(f.stat().st_mtime)
        title = ref.replace("_", " ").strip()
        # Categorize by filename heuristics
        name = f.name.lower()
        if "natenberg" in name and "second_edition" in name:
            doc_subtype = "natenberg_book"
        elif "trading_volatility" in name and "bennett" in name:
            doc_subtype = "bennett_book"
        elif "sinclair" in name:
            doc_subtype = "sinclair_book"
        elif "technical_analysis" in name:
            doc_subtype = "ta_reference"
        elif "squeezemetrics" in name:
            doc_subtype = "squeezemetrics_paper"
        elif "ubs_q-series" in name or "cta" in name:
            doc_subtype = "systematics_research"
        elif "volstudies_text" in name or "volstudies_-_chapter" in name:
            doc_subtype = "volstudies_natenberg_chapter"
        elif "volstudies_slides" in name:
            doc_subtype = "volstudies_slides"
        elif "syllabus" in name:
            doc_subtype = "volstudies_syllabus"
        elif "vip_mentorship" in name:
            doc_subtype = "vip_mentorship"
        elif "framework" in name:
            doc_subtype = "volsignals_framework"
        elif "hedging_flows" in name:
            doc_subtype = "infographic"
        elif "dhd" in name or "boot_camp" in name:
            doc_subtype = "dealer_hedging_dynamics"
        elif "systematics" in name:
            doc_subtype = "systematics"
        elif "core_greeks" in name:
            doc_subtype = "core_greeks_refresher"
        else:
            doc_subtype = "other"
        yield {
            "source": "volsignals_paid_course",
            "source_ref": ref,
            "source_date": d,
            "document_type": "transcript",   # PDF text body, treated as transcript-equivalent
            "page_or_segment": None,
            "title": title,
            "full_text": text,
            "metadata": {"file_name": f.name, "subtype": doc_subtype, "char_count": len(text)},
        }


def discover_volsignals(root: Path):
    """data/snapshots/volsignals/youtube/*.md"""
    base = root / "volsignals" / "youtube"
    if not base.exists():
        return
    for f in sorted(base.iterdir()):
        if f.suffix != ".md":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if len(text) < 100:
            continue
        # Parse video ID from filename - pattern: {videoId}_{slug}.md
        m = re.match(r"^([A-Za-z0-9_-]{11})_(.*)\.md$", f.name)
        vid = m.group(1) if m else None
        title = m.group(2).replace("_", " ").strip() if m else f.stem
        # Use file mtime as source_date (no better signal in transcripts)
        d = date_cls.fromtimestamp(f.stat().st_mtime)
        yield {
            "source": "volsignals_youtube",
            "source_ref": vid or f.stem,
            "source_date": d,
            "document_type": "transcript",
            "page_or_segment": None,
            "title": title or f.stem,
            "full_text": text,
            "metadata": {"video_id": vid, "file_name": f.name, "url": f"https://www.youtube.com/watch?v={vid}" if vid else None},
        }


def discover_spotgamma(root: Path):
    """data/snapshots/spotgamma/{YYYY-MM-DD}/**/*.md"""
    base = root / "spotgamma"
    if not base.exists():
        return
    for date_dir in sorted(base.iterdir()):
        if not date_dir.is_dir():
            continue
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_dir.name)
        if not m:
            continue
        try:
            d = date_cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        for f in sorted(date_dir.rglob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            if len(text) < 100:
                continue
            rel = f.relative_to(date_dir)
            # Categorize by filename / path
            name = f.name.lower()
            if "founders_note" in name:
                source = "spotgamma_founders_note"
            elif "flowpatrol" in name:
                source = "spotgamma_flowpatrol"
            elif "market_overview" in name:
                source = "spotgamma_market_overview"
            elif "equityhub" in str(rel).lower():
                source = "spotgamma_equityhub"
            else:
                source = "spotgamma_other"
            yield {
                "source": source,
                "source_ref": str(rel).replace("\\", "/"),
                "source_date": d,
                "document_type": "webpage_text",
                "page_or_segment": None,
                "title": f.stem.replace("_", " ").strip(),
                "full_text": text,
                "metadata": {"file_name": f.name, "subpath": str(rel).replace("\\", "/")},
            }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source")
    ap.add_argument("--since")
    args = ap.parse_args()

    _load_dotenv(ENV_FILE)
    db_url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not db_url and not args.dry_run:
        print("ERROR: DATABASE_PUBLIC_URL or DATABASE_URL required (or use --dry-run)")
        return 2

    since_d = None
    if args.since:
        try:
            since_d = date_cls.fromisoformat(args.since)
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got {args.since!r}")
            return 2

    discoverers = [
        discover_macro_show,
        discover_university,
        discover_volsignals,
        discover_volsignals_paid,
        discover_spotgamma,
    ]

    docs = []
    for fn in discoverers:
        for doc in fn(SNAPSHOTS):
            if args.source and doc["source"] != args.source:
                continue
            if since_d and doc["source_date"] < since_d:
                continue
            docs.append(doc)

    print(f"discovered: {len(docs)} candidate documents")
    by_source = {}
    for d in docs:
        by_source.setdefault(d["source"], 0)
        by_source[d["source"]] += 1
    for s in sorted(by_source):
        print(f"  {s:40s}  {by_source[s]:>5}")

    if args.dry_run:
        print("dry-run, not inserting")
        return 0

    try:
        import psycopg2
        from psycopg2.extras import Json
    except ImportError:
        print("psycopg2 not installed; install via pip install psycopg2-binary")
        return 2

    conn = psycopg2.connect(db_url)
    inserted = 0
    skipped = 0
    failed = 0
    with conn:
        with conn.cursor() as cur:
            for d in docs:
                try:
                    cur.execute(
                        """
                        INSERT INTO corpus_documents
                            (source, source_date, source_ref, document_type, page_or_segment, title, full_text, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (source, COALESCE(source_ref, ''), source_date, COALESCE(page_or_segment, -1))
                        DO NOTHING
                        """,
                        (
                            d["source"], d["source_date"], d.get("source_ref"),
                            d["document_type"], d.get("page_or_segment"),
                            d["title"], d["full_text"], Json(d.get("metadata", {})),
                        ),
                    )
                    if cur.rowcount > 0:
                        inserted += 1
                    else:
                        skipped += 1
                except Exception as e:
                    failed += 1
                    print(f"  FAIL [{d['source']}/{d.get('source_ref')}]: {e}")
    conn.close()
    print(f"summary: inserted={inserted} skipped(dup)={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
