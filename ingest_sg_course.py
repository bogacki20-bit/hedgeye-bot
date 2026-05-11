"""Ingest the SpotGamma "How to Use SpotGamma" course corpus.

Pipeline:
  1. Read C:\\Users\\bogac\\Downloads\\sg_course_methodology.json (107 lessons,
     captured via the Chrome MCP from the user's logged-in spotgamma.com session)
  2. Write each lesson to data/snapshots/spotgamma/methodology/{slug}.md
     with a standard frontmatter for FTS+attribution
  3. Insert each into corpus_documents with source='spotgamma_methodology' so
     decision_engine + classifier RAG calls can pull it via FTS

Idempotent. Re-running with the same JSON overwrites markdown files and
upserts corpus rows (ON CONFLICT DO UPDATE on text/title).

Run via the bridge: queue
    {"command": "python_script", "args": {"script": "ingest_sg_course.py", "args": []}}
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

DOWNLOADS_JSON = Path(r"C:\Users\bogac\Downloads\sg_course_methodology.json")
REPO_ROOT = Path(__file__).parent
METHODOLOGY_DIR = REPO_ROOT / "data" / "snapshots" / "spotgamma" / "methodology"


def _write_markdown(slug: str, title: str, text: str) -> Path:
    """Write one lesson as a markdown file under data/snapshots/spotgamma/methodology/."""
    METHODOLOGY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = METHODOLOGY_DIR / f"{slug}.md"
    body = [
        f"# SpotGamma Methodology — {title}",
        "",
        f"Source: https://spotgamma.com/courses/how-to-use-spotgamma/lessons/{slug}/",
        f"Slug: {slug}",
        f"Captured: {date.today().isoformat()}",
        "",
        "## Transcript",
        "",
        text.strip(),
    ]
    out_path.write_text("\n".join(body), encoding="utf-8")
    return out_path


def _upsert_corpus(slug: str, title: str, text: str, capture_date: date) -> bool:
    """Insert / update corpus_documents for one lesson.

    source='spotgamma_methodology', source_ref=slug. ON CONFLICT DO UPDATE so
    re-ingesting with refreshed content overwrites cleanly.
    """
    try:
        import db_pg
        metadata = {
            "slug": slug,
            "course": "how-to-use-spotgamma",
            "captured_at": capture_date.isoformat(),
            "url": f"https://spotgamma.com/courses/how-to-use-spotgamma/lessons/{slug}/",
        }
        with db_pg.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO corpus_documents (
                        source, source_date, source_ref,
                        document_type, page_or_segment,
                        title, full_text, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source, COALESCE(source_ref, ''), source_date, COALESCE(page_or_segment, -1))
                    DO UPDATE SET
                        title     = EXCLUDED.title,
                        full_text = EXCLUDED.full_text,
                        metadata  = EXCLUDED.metadata
                    """,
                    (
                        "spotgamma_methodology",
                        capture_date,
                        slug,
                        "course_lesson",
                        None,
                        title,
                        text,
                        json.dumps(metadata),
                    ),
                )
            conn.commit()
        return True
    except Exception as e:
        log.error("corpus upsert failed for %s: %s", slug, e)
        return False


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )

    if not DOWNLOADS_JSON.exists():
        print(json.dumps({"error": f"JSON not found at {DOWNLOADS_JSON}"}))
        return 1

    try:
        data = json.loads(DOWNLOADS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        print(json.dumps({"error": f"could not parse JSON: {e}"}))
        return 1

    if not isinstance(data, dict):
        print(json.dumps({"error": "JSON root must be a dict {slug -> {title, text}}"}))
        return 1

    today = date.today()
    summary = {
        "considered": 0,
        "md_written": 0,
        "corpus_ingested": 0,
        "corpus_failed": 0,
        "skipped_empty": 0,
        "errors": [],
    }

    for slug, lesson in data.items():
        summary["considered"] += 1
        if not isinstance(lesson, dict):
            summary["errors"].append(f"{slug}: lesson is not a dict")
            continue
        title = (lesson.get("title") or slug).strip()
        text = (lesson.get("text") or "").strip()
        if not text or len(text) < 50:
            summary["skipped_empty"] += 1
            continue

        try:
            _write_markdown(slug, title, text)
            summary["md_written"] += 1
        except Exception as e:
            summary["errors"].append(f"{slug}: md write failed: {e}")
            continue

        if _upsert_corpus(slug, title, text, today):
            summary["corpus_ingested"] += 1
        else:
            summary["corpus_failed"] += 1

    print(json.dumps(summary, indent=2))
    return 0 if summary["corpus_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
