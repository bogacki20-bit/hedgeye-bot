"""Fetch YouTube auto-transcripts for VolSignals videos and save to corpus.

Uses youtube-transcript-api which pulls captions through the public youtube
captions JSON endpoint - no video download, no API key needed.

Run via the bridge:
    queue {"command": "python_script", "args": {"script": "fetch_volsignals_transcripts.py", "args": ["--max", "5"]}}

The script will auto-install youtube-transcript-api if missing.

Idempotent: any video whose transcript file already exists in corpus is skipped.

Args:
    --max N         maximum transcripts to fetch this run (default 5)
    --priority      only fetch the high-priority videos (long-form + primers)
    --vid VIDEO_ID  fetch a single specific video
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(r"C:\Projects\hedgeye-bot")
CORPUS = REPO / "data" / "snapshots" / "volsignals" / "youtube"
CORPUS.mkdir(parents=True, exist_ok=True)

# All 52 videos as (vid, title) tuples, latest-first.
VIDEOS = [
    ("8tk6w0K9AIU", "90% of Traders Wont Know This"),
    ("lK8Tln9CQ8o", "The Stock Market Doesnt Care About The War (Heres Why)"),
    ("BOealVmrPDs", "This Is A Critical Moment For The Stock Market"),
    ("tJSTEYyG5vw", "Something Strange is Happening Here"),
    ("MBrxgbsqvSM", "Introduction To Pinning at Expiration (Charm + Gamma)"),
    ("PB_TFhg5IEM", "This is JUST Mechanics (Heres Why)"),
    ("9SeUxSfOHN4", "The Signal Just Triggered Again"),
    ("0--a3xR4UOs", "Introduction To Reading Market Direction (Charm)"),
    ("-iwcSqftgMk", "Introduction To Reading Market Volatility (Gamma)"),
    ("a9uf2egYIV0", "This is a Problem"),
    ("nciBwODbdao", "Everyones Talking About Iran But Theyre Missing This"),
    ("6QWMMIGUHLI", "The Next 24 Hours Could Change Everything"),
    ("QyNCg8HWrlM", "Everyone Is Wrong About What Will Happen Next Week"),
    ("OAnrptLn4tM", "What Market Makers Hope You NEVER Figure Out"),
    ("WYWVPNxbfDc", "This Signal Just Triggered (Why it Matters)"),
    ("0xiRJng2EUY", "If Youre Anxious About The Stock Market Watch This"),
    ("deF9OzDvWUU", "I F-ked Up"),
    ("jlqSTaaKk-k", "If You See This Its a RED FLAG"),
    ("f_k4B26F1ZA", "This Is What Actually Predicts Market Direction"),
    ("D0Xk8-F0tAc", "Heres What Market Makers Dont Want You To Learn"),
    ("kUZ5J_9FvW0", "How To Trade With the Dealer Flows"),
    ("u8z9zbYWgSk", "You CAN Predict Market Actions (Heres How)"),
    ("Y-EHYeK9SOY", "How Dealer Hedging Moves Markets (TGIF)"),
    ("uQDR5_1hy4Q", "Introduction To Dealer Hedging Dynamics (TGIF)"),
    ("IDj7RyQY450", "How Hedging Flows Will Affect Today"),
    ("_CcqBtZtAeE", "Vol Just Got Crushed (What You Need To Know)"),
    ("0EGGcH4srQM", "Why Todays Range Is So Choppy (And the Levels That Matter)"),
    ("0e0EMoqiAaw", "Introduction to Dealer Hedging Flows"),
    ("zxWdvSzk_RE", "How Options Flow Shapes Todays Price Action"),
    ("rBhSWCYcl_w", "These Are The Levels Were Watching Into Todays FOMC"),
    ("4ZuO7NLYtJ0", "Captain Condors Christmas Eve Blowup"),
    ("Y6FMewX9HOA", "These Two Levels Will Decide Today"),
    ("g7cExX1cOlA", "How This Safe Strategy Wiped Out 50 Million on Christmas Eve"),
    ("WaoSpmhmDRY", "Dealer Hedging Basics VolSignals Webinar"),
    ("Wh--p1Qdofs", "The Landscape - Who are the key market participants"),
    ("qyNGJU4bIf4", "VolSignals Morning Meeting (Nov 10 2025)"),
    ("uPDXubHnPIw", "VolSignals - Learn How SPX Option Hedging Actually Moves Markets"),
    ("dMI5Az3Xc2g", "Volsignals Podcast - When Flows Outsize Markets"),
    ("6uiV80951lc", "VolSignals Podcast - May SPX SOQ (OPEX)"),
    ("b-QOGhETPRU", "The Other Greeks VolSignals Webinar"),
    ("_0tWu5im7GU", "VolSignals Podcast - SPX Volatility What Comes Next"),
    ("zVz-lksySFc", "VolSignals Podcast - Welcome to OPEX Week"),
    ("QiyeraUbhCM", "VolSignals Podcast - OPEX Week Price Action Review"),
    ("qwSeAebEyUo", "Intro The Greeks - Delta Gamma and Theta VolSignals Webinar"),
    ("lCuSRfwA9a8", "TGIF - VolSignals Morning Meeting (Sep 26 2025)"),
    ("x52vcacvLIM", "VolSignals Morning Meeting (Weds Mar 5 2025)"),
    ("Hck6IQ47Cec", "Martingaling JUST DO IT"),
    ("0FRrV-M3U54", "VolSignals Morning Meeting + Return to Mean Launch (Dec 6 2024)"),
    ("mhz9LtyhsNU", "VolSignals & Trading Closed - RETURN TO MEAN"),
    ("bcYUoSpcCTY", "VolSignals TGIF-ing Over"),
    ("Q4bbhywKts8", "Welcome to VolSignals"),
    ("yhcOtcM4kMQ", "10 26 Webcast UBS Equity Derivatives and Cboe Global Markets Discuss Market Insights"),
]

# Highest-priority video IDs (the ⭐⭐ long-form + ⭐ framework primers from the inventory)
PRIORITY_IDS = [
    "kUZ5J_9FvW0",  # How To Trade With the Dealer Flows (long-form)
    "Y-EHYeK9SOY",  # How Dealer Hedging Moves Markets (TGIF)
    "uQDR5_1hy4Q",  # Introduction To Dealer Hedging Dynamics (TGIF)
    "0e0EMoqiAaw",  # Introduction to Dealer Hedging Flows
    "MBrxgbsqvSM",  # Introduction To Pinning at Expiration (Charm + Gamma)
    "0--a3xR4UOs",  # Introduction To Reading Market Direction (Charm)
    "-iwcSqftgMk",  # Introduction To Reading Market Volatility (Gamma)
    "OAnrptLn4tM",  # What Market Makers Hope You NEVER Figure Out
    "D0Xk8-F0tAc",  # Heres What Market Makers Dont Want You To Learn
    "WaoSpmhmDRY",  # Dealer Hedging Basics
    "Wh--p1Qdofs",  # The Landscape - key market participants
    "uPDXubHnPIw",  # SPX Option Hedging Actually Moves Markets
    "b-QOGhETPRU",  # The Other Greeks
    "qwSeAebEyUo",  # Intro The Greeks - Delta Gamma and Theta
]


def slugify(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 _-]+", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:80]


def ensure_yta():
    try:
        import youtube_transcript_api  # noqa
        return True
    except ImportError:
        print("youtube-transcript-api not found, installing...")
        rc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "youtube-transcript-api"],
            capture_output=True, text=True
        )
        if rc.returncode != 0:
            print(f"pip install failed: {rc.stderr[:500]}")
            return False
        return True


def fetch_one(vid: str, title: str) -> str:
    """Returns 'ok' / 'skip' / 'err: <reason>'."""
    out_file = CORPUS / f"{vid}_{slugify(title)}.md"
    if out_file.exists():
        return "skip"
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as e:
        return f"err: {e}"
    try:
        # API compat: 1.x uses instance .fetch(), 0.x uses classmethod .get_transcript()
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            transcript = YouTubeTranscriptApi.get_transcript(vid, languages=["en", "en-US"])
        else:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(vid, languages=["en", "en-US"])
            transcript = []
            for s in fetched:
                transcript.append({
                    "text": getattr(s, "text", ""),
                    "start": getattr(s, "start", 0),
                    "duration": getattr(s, "duration", 0),
                })
    except Exception as e:
        return f"err: {e.__class__.__name__}: {str(e)[:200]}"

    # Format with timestamps
    lines = [
        f"# {title}",
        "",
        f"**Video ID:** {vid}",
        f"**URL:** https://www.youtube.com/watch?v={vid}",
        f"**Source:** YouTube auto-generated captions (en)",
        f"**Segments:** {len(transcript)}",
        "",
        "---",
        "",
    ]
    cur_min = -1
    para = []
    for seg in transcript:
        sec = int(seg.get("start", 0))
        text = seg.get("text", "").strip()
        text = text.replace("\n", " ")
        m = sec // 60
        if m != cur_min:
            if para:
                lines.append(" ".join(para))
                lines.append("")
                para = []
            lines.append(f"## {m:02d}:00")
            lines.append("")
            cur_min = m
        para.append(text)
    if para:
        lines.append(" ".join(para))

    out_file.write_text("\n".join(lines), encoding="utf-8")
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=5, help="max transcripts to fetch this run")
    ap.add_argument("--priority", action="store_true", help="only fetch priority videos")
    ap.add_argument("--vid", help="fetch a single video by ID")
    ap.add_argument("--delay", type=float, default=8.0, help="seconds between fetches (avoid IP block)")
    args = ap.parse_args()

    if not ensure_yta():
        return 2

    if args.vid:
        title = "Direct fetch"
        for v, t in VIDEOS:
            if v == args.vid:
                title = t
                break
        targets = [(args.vid, title)]
    elif args.priority:
        title_by_vid = dict(VIDEOS)
        targets = [(v, title_by_vid.get(v, v)) for v in PRIORITY_IDS]
    else:
        targets = list(VIDEOS)

    print(f"Targets: {len(targets)} | max this run: {args.max}")
    ok = 0
    skip = 0
    err = 0
    for i, (vid, title) in enumerate(targets):
        if ok + err >= args.max:
            print(f"reached max ({args.max}), stopping")
            break
        result = fetch_one(vid, title)
        status = result.split(":")[0]
        marker = "+" if status == "ok" else ("-" if status == "skip" else "!")
        print(f"  [{marker}] {vid}  {title[:60]}  ->  {result}")
        if status == "ok":
            ok += 1
        elif status == "skip":
            skip += 1
        else:
            err += 1

    print(f"summary: ok={ok} skip={skip} err={err}")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
