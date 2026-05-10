"""Hedgeye University transcription pipeline.

Takes an audio (or video — ffmpeg auto-extracts) file, sends it to OpenAI's
Whisper API, saves the resulting transcript as markdown under
`data/snapshots/hedgeye/{date}/university/transcripts/{chapter}/{slug}.md`,
and ingests it into corpus_documents (source='hedgeye_u_lesson_transcript')
so FTS over the corpus surfaces Keith's spoken framework alongside the
existing text-callout quotes.

Designed as the Python *output* side of the HU pipeline. Video acquisition
(authenticated downloads from university.hedgeye.com) lives in hu_download.py
once that's built — for now this module accepts any local audio/video file
that gets fed to it.

Required env: OPENAI_API_KEY in .env. Loaded by command_bridge at startup.

CLI:
    py hu_transcribe.py --audio C:\\path\\to\\lesson.mp4 \\
       --chapter 2 --slug what-is-fullcycle-investing \\
       --title "What Is #FullCycle Investing" \\
       --lesson-id 57857269

    py hu_transcribe.py --reingest                  # walk existing transcripts
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent
SNAPSHOTS_ROOT = REPO_ROOT / "data" / "snapshots" / "hedgeye"

# Whisper API limits: max 25MB per request. For long lessons (>15 min at high
# bitrate) we'd need to split — most HU lessons are 2-7 min so this is rare.
WHISPER_MAX_MB = 25
WHISPER_MODEL = os.environ.get("OPENAI_WHISPER_MODEL", "whisper-1")


def _resolve_openai_key() -> Optional[str]:
    return (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENAI_TOKEN")
    )


# ─────────────────────────── Transcribe ───────────────────────────

def transcribe_audio(audio_path: Path | str) -> Optional[dict]:
    """Send `audio_path` to OpenAI Whisper, return {text, duration, language, segments}.

    Returns None on missing key, oversized file, or API error. Never raises.
    """
    p = Path(audio_path)
    if not p.exists():
        log.error("transcribe_audio: file does not exist: %s", p)
        return None

    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > WHISPER_MAX_MB:
        log.error(
            "transcribe_audio: %s is %.1fMB > Whisper API max %sMB. "
            "Need to split (ffmpeg -ss/-t) before transcribing.",
            p, size_mb, WHISPER_MAX_MB,
        )
        return None

    key = _resolve_openai_key()
    if not key:
        log.error("OPENAI_API_KEY not set in env; cannot transcribe %s", p)
        return None

    try:
        from openai import OpenAI
    except ImportError:
        log.error("openai package not installed. pip install openai")
        return None

    try:
        client = OpenAI(api_key=key)
        with p.open("rb") as f:
            resp = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        # verbose_json gives us {text, duration, language, segments[{start,end,text}, ...]}
        return {
            "text": resp.text,
            "duration": getattr(resp, "duration", None),
            "language": getattr(resp, "language", None),
            "segments": [
                {"start": s.start, "end": s.end, "text": s.text}
                for s in (getattr(resp, "segments", None) or [])
            ],
        }
    except Exception as e:
        log.error("Whisper transcription failed for %s: %s", p, e)
        return None


# ─────────────────────────── Save markdown ───────────────────────────

def _format_segments_md(segments: list[dict]) -> str:
    """Render a Whisper segments list as a timestamped bullet list.

    Each segment becomes `- (HH:MM:SS) text` so a reader can jump to a
    rough timecode. FTS still indexes the raw text either way.
    """
    if not segments:
        return ""
    out = []
    for s in segments:
        start = float(s.get("start") or 0)
        h = int(start // 3600)
        m = int((start % 3600) // 60)
        sec = int(start % 60)
        text = (s.get("text") or "").strip()
        out.append(f"- ({h:02d}:{m:02d}:{sec:02d}) {text}")
    return "\n".join(out)


def save_transcript_md(
    *,
    chapter: int,
    slug: str,
    title: str,
    lesson_id: Optional[str],
    transcript: dict,
    audio_path: Optional[Path] = None,
    capture_date: Optional[date] = None,
) -> Path:
    """Write the transcript to data/snapshots/hedgeye/{date}/university/transcripts/{chapter}/{slug}.md.

    Returns the path written. Caller is responsible for ingesting via corpus_ingest.
    """
    if capture_date is None:
        capture_date = date.today()

    out_dir = (
        SNAPSHOTS_ROOT
        / capture_date.isoformat()
        / "university"
        / "transcripts"
        / f"chapter{chapter}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{slug}.md"

    text = (transcript.get("text") or "").strip()
    duration = transcript.get("duration")
    language = transcript.get("language")
    segments = transcript.get("segments") or []

    duration_str = "—"
    if duration is not None:
        try:
            d = float(duration)
            duration_str = f"{int(d // 60)}m {int(d % 60)}s"
        except (TypeError, ValueError):
            duration_str = str(duration)

    lines = [
        f"# Hedgeye University — Chapter {chapter} — {title}",
        "",
        f"**Lesson ID:** {lesson_id or '—'}",
        f"**Slug:** {slug}",
        f"**Audio source:** {audio_path or '—'}",
        f"**Whisper duration:** {duration_str}",
        f"**Language:** {language or '—'}",
        f"**Captured:** {capture_date.isoformat()}",
        "",
        "## Transcript (full text)",
        "",
        text,
        "",
        "## Segments (timestamped)",
        "",
        _format_segments_md(segments) or "—",
    ]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ─────────────────────────── Corpus ingest ───────────────────────────

def ingest_to_corpus(
    transcript_path: Path,
    *,
    chapter: int,
    slug: str,
    title: str,
    lesson_id: Optional[str],
    capture_date: Optional[date] = None,
) -> bool:
    """Insert the transcript markdown into corpus_documents.

    Source = 'hedgeye_u_lesson_transcript' (distinct from existing
    'hedgeye_u_lesson' which holds text-callout quotes). This lets RAG
    consumers filter by source if they want only Keith's spoken material
    vs the curated written quotes.

    Returns True on success, False on any error. Never raises.
    """
    if capture_date is None:
        capture_date = date.today()
    try:
        import db_pg
        body = transcript_path.read_text(encoding="utf-8")
        metadata = {
            "chapter": chapter,
            "slug": slug,
            "title": title,
            "lesson_id": lesson_id,
            "transcript_path": str(transcript_path.relative_to(REPO_ROOT)).replace("\\", "/"),
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
                        "hedgeye_u_lesson_transcript",
                        capture_date,
                        f"chapter{chapter}/{slug}",
                        "video_transcript",
                        None,
                        f"Ch{chapter} — {title}",
                        body,
                        json.dumps(metadata, default=str),
                    ),
                )
            conn.commit()
        return True
    except Exception as e:
        log.error("ingest_to_corpus failed for %s: %s", transcript_path, e)
        return False


# ─────────────────────────── Pipeline ───────────────────────────

def process_audio_file(
    audio_path: Path | str,
    *,
    chapter: int,
    slug: str,
    title: str,
    lesson_id: Optional[str] = None,
) -> Optional[dict]:
    """End-to-end: transcribe → save markdown → ingest. Returns summary dict or None on failure."""
    audio_path = Path(audio_path)
    log.info("Transcribing %s (chapter=%s slug=%s)", audio_path, chapter, slug)
    transcript = transcribe_audio(audio_path)
    if not transcript:
        return None
    md_path = save_transcript_md(
        chapter=chapter, slug=slug, title=title,
        lesson_id=lesson_id, transcript=transcript, audio_path=audio_path,
    )
    log.info("Wrote transcript: %s (%d chars)", md_path, len(transcript.get("text") or ""))
    ingest_ok = ingest_to_corpus(
        md_path, chapter=chapter, slug=slug, title=title, lesson_id=lesson_id,
    )
    log.info("Corpus ingest: %s", "ok" if ingest_ok else "FAILED")
    return {
        "audio_path": str(audio_path),
        "transcript_path": str(md_path),
        "duration": transcript.get("duration"),
        "language": transcript.get("language"),
        "char_count": len(transcript.get("text") or ""),
        "segment_count": len(transcript.get("segments") or []),
        "corpus_ingested": ingest_ok,
    }


def reingest_existing_transcripts() -> dict:
    """Walk data/snapshots/hedgeye/*/university/transcripts/**/*.md and re-ingest each.

    Useful after fixing a corpus bug to refresh existing rows. Idempotent
    via the ON CONFLICT DO UPDATE in ingest_to_corpus.
    """
    summary = {"considered": 0, "ingested": 0, "failed": 0}
    base = SNAPSHOTS_ROOT
    if not base.exists():
        return summary
    import re as _re
    for f in base.rglob("*/university/transcripts/*/*.md"):
        summary["considered"] += 1
        # Path: data/snapshots/hedgeye/{YYYY-MM-DD}/university/transcripts/chapter{N}/{slug}.md
        try:
            chapter_part = f.parent.name  # 'chapter2'
            chapter_match = _re.match(r"^chapter(\d+)$", chapter_part)
            if not chapter_match:
                continue
            chapter = int(chapter_match.group(1))
            slug = f.stem
            # Pull title from the first line (`# Hedgeye University — Chapter 2 — Title`)
            first_line = f.read_text(encoding="utf-8").splitlines()[0]
            title = first_line.split("—")[-1].strip() if "—" in first_line else slug
            # Extract capture date from path
            from datetime import date as _date
            date_part = f.parts[-5]  # .../{date}/university/transcripts/chapter{N}/{slug}.md
            try:
                cd = _date.fromisoformat(date_part)
            except ValueError:
                cd = None
            ok = ingest_to_corpus(
                f, chapter=chapter, slug=slug, title=title,
                lesson_id=None, capture_date=cd,
            )
            if ok:
                summary["ingested"] += 1
            else:
                summary["failed"] += 1
        except Exception as e:
            log.warning("reingest failed for %s: %s", f, e)
            summary["failed"] += 1
    return summary


# ─────────────────────────── CLI ───────────────────────────

def _cli() -> None:
    ap = argparse.ArgumentParser(
        description="HU transcription pipeline: audio file -> Whisper -> markdown -> corpus_documents"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--audio", help="Path to audio (or video) file to transcribe")
    g.add_argument("--reingest", action="store_true",
                   help="Walk existing transcripts and re-insert into corpus_documents")
    g.add_argument("--check-key", action="store_true",
                   help="Verify OPENAI_API_KEY is loaded (prints presence, not value)")
    ap.add_argument("--chapter", type=int, help="Chapter number (1-4)")
    ap.add_argument("--slug", help="Lesson slug (kebab-case)")
    ap.add_argument("--title", help="Human-readable lesson title")
    ap.add_argument("--lesson-id", help="Hedgeye lesson ID from _full_lesson_index.md")
    args = ap.parse_args()

    if args.check_key:
        key = _resolve_openai_key()
        print(json.dumps({
            "openai_key_present": bool(key),
            "key_length": len(key) if key else 0,
            "model": WHISPER_MODEL,
        }, indent=2))
        return

    if args.reingest:
        summary = reingest_existing_transcripts()
        print(json.dumps(summary, indent=2))
        return

    if args.audio:
        if not (args.chapter and args.slug and args.title):
            print("--audio requires --chapter, --slug, --title")
            return
        summary = process_audio_file(
            args.audio,
            chapter=args.chapter,
            slug=args.slug,
            title=args.title,
            lesson_id=args.lesson_id,
        )
        print(json.dumps(summary, indent=2, default=str))
        return


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
