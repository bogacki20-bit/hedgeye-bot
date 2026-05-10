"""Hedgeye University video acquisition.

Hedgeye serves all course videos via Wistia. Each lesson page embeds a
Wistia player whose master HLS manifest is at
`https://fast.wistia.com/embed/medias/{media_id}.m3u8`. The media_id is
discoverable via Chrome MCP network capture (the manifest URL appears as
the player initializes).

This module takes a known Wistia media_id and downloads the smallest
available format to local disk. yt-dlp's Wistia extractor handles auth
(none needed for embedded videos), HLS manifest parsing, and segment
download/concatenation. We pick the lowest-bitrate format because Whisper
only needs intelligible audio; smaller files = faster downloads + fewer
chances of hitting the 25MB Whisper API cap.

CLI:
    py hu_download.py --wistia-id exixiozehw \\
       --out data/snapshots/hedgeye/test_audio/ch1_lesson2.mp4

    py hu_download.py --probe exixiozehw   # list available formats, no download
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

WISTIA_URL_TEMPLATE = "https://fast.wistia.com/medias/{media_id}"


def probe_formats(media_id: str) -> Optional[dict]:
    """Return the available format list for `media_id` without downloading.

    Used to verify a media_id resolves and to pick the smallest format.
    """
    try:
        import yt_dlp
    except ImportError:
        log.error("yt-dlp not installed. pip install yt-dlp")
        return None

    url = WISTIA_URL_TEMPLATE.format(media_id=media_id)
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        formats = info.get("formats") or []
        return {
            "media_id": media_id,
            "title": info.get("title"),
            "duration": info.get("duration"),
            "format_count": len(formats),
            "formats": [
                {
                    "format_id": f.get("format_id"),
                    "ext":       f.get("ext"),
                    "resolution": f.get("resolution") or f.get("format_note"),
                    "vbr":       f.get("vbr"),
                    "abr":       f.get("abr"),
                    "filesize":  f.get("filesize") or f.get("filesize_approx"),
                    "protocol":  f.get("protocol"),
                }
                for f in formats
            ],
        }
    except Exception as e:
        log.error("Wistia probe failed for %s: %s", media_id, e)
        return None


def download_by_media_id(
    media_id: str,
    out_path: Path | str,
    *,
    format_spec: str = "worst/worstvideo+bestaudio/best",
) -> Optional[Path]:
    """Download `media_id` to `out_path` using yt-dlp.

    Returns the resolved output path on success, None on failure. Never raises.

    `format_spec` defaults to 'worst/worstvideo+bestaudio/best' — try the
    single lowest-bitrate format first, fall back to combining streams,
    finally accept best if Wistia only exposes one quality.
    """
    try:
        import yt_dlp
    except ImportError:
        log.error("yt-dlp not installed. pip install yt-dlp")
        return None

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    url = WISTIA_URL_TEMPLATE.format(media_id=media_id)
    # %(ext)s in template lets yt-dlp pick the right extension
    template = str(out_path.with_suffix("")) + ".%(ext)s"

    ydl_opts = {
        "format":      format_spec,
        "outtmpl":     template,
        "noplaylist":  True,
        "quiet":       True,
        "no_warnings": True,
        # Restrict to a single video file to keep downloads predictable
        "writethumbnail": False,
        "writesubtitles": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        # yt-dlp returns the requested format's actual file path via
        # the post-download 'requested_downloads' list, but that's version-
        # dependent. Easier: enumerate the directory for files matching
        # the stem.
        stem = out_path.stem
        candidates = sorted(out_path.parent.glob(stem + ".*"))
        # Filter out partials and json sidecars
        candidates = [c for c in candidates if c.suffix not in (".part", ".json", ".info.json")]
        if not candidates:
            log.error("download appeared to succeed but no output file found at %s.* ", out_path.with_suffix(""))
            return None
        return candidates[0]
    except Exception as e:
        log.error("Wistia download failed for %s: %s", media_id, e)
        return None


def _cli() -> None:
    ap = argparse.ArgumentParser(
        description="HU video downloader — pull a Wistia-hosted lesson by media_id"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--wistia-id", help="Wistia media_id (e.g. 'exixiozehw'). Used with --out.")
    g.add_argument("--probe", metavar="MEDIA_ID", help="List available formats, no download")
    ap.add_argument("--out", help="Output path (extension is auto-resolved by yt-dlp)")
    ap.add_argument("--format", default="worst/worstvideo+bestaudio/best",
                    help="yt-dlp format spec (default: smallest available)")
    args = ap.parse_args()

    if args.probe:
        info = probe_formats(args.probe)
        if not info:
            print(f"probe failed for {args.probe!r}")
            return
        print(json.dumps(info, indent=2, default=str))
        return

    if args.wistia_id:
        if not args.out:
            print("--wistia-id requires --out PATH")
            return
        result = download_by_media_id(args.wistia_id, args.out, format_spec=args.format)
        if not result:
            print(json.dumps({"ok": False, "media_id": args.wistia_id, "out": args.out}, indent=2))
            return
        size = result.stat().st_size if result.exists() else 0
        print(json.dumps({
            "ok":         True,
            "media_id":   args.wistia_id,
            "downloaded": str(result),
            "size_bytes": size,
            "size_mb":    round(size / (1024 * 1024), 3),
        }, indent=2))
        return


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    _cli()
