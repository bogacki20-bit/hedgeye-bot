"""Local speech-to-text for Hedgeye shows (The Call, The Macro Show).

Runs faster-whisper on the laptop CPU — audio never leaves the machine.
Audio comes from the show's own player in Kris's signed-in Chrome (the
audio-only HLS rendition, saved as .ts); see docs/WORKSTATION.md.

  py tools/transcribe.py <audio> [--model small.en] [--out file.txt]

Writes a timestamped transcript ([hh:mm:ss] every ~30 s paragraph) next to
the audio unless --out is given.
"""
from __future__ import annotations

import argparse
import pathlib
import time

# Proper nouns and desk jargon, so the model spells them the way Hedgeye does.
VOCAB = ("Hedgeye, Keith McCullough, Daryl Jones, Josh Steiner, The Call @ Hedgeye, "
         "The Macro Show, GIP model, Quad 1, Quad 2, Quad 3, Quad 4, Risk Range, "
         "TRADE, TREND, TAIL, Real-Time Alerts, Early Look, Position Monitor, "
         "Signal Strength, SPX, NDX, RUT, VIX, TLT, XLE, XLF, XLI, XLK, XLY, XLP, "
         "Brent, WTI, Fed, FOMC, CPI, PCE, GDP, bps.")


def _ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcribe(audio: str, model: str = "small.en", out: str | None = None,
               para_sec: float = 30.0) -> pathlib.Path:
    from faster_whisper import WhisperModel
    t0 = time.time()
    wm = WhisperModel(model, device="cpu", compute_type="int8")
    segments, info = wm.transcribe(audio, beam_size=5, vad_filter=True,
                                   initial_prompt=VOCAB, condition_on_previous_text=True)
    src = pathlib.Path(audio)
    dest = pathlib.Path(out) if out else src.with_suffix(".transcript.txt")
    lines, para, start = [], [], None
    with dest.open("w", encoding="utf-8") as f:
        f.write(f"TRANSCRIPT — {src.name} · {_ts(info.duration)} · model {model} "
                f"(auto speech-to-text; names/numbers can be misheard)\n\n")
        for seg in segments:
            if start is None:
                start = seg.start
            para.append(seg.text.strip())
            if seg.end - start >= para_sec:
                f.write(f"[{_ts(start)}] {' '.join(para)}\n\n"); f.flush()
                print(f"  {_ts(seg.end)} / {_ts(info.duration)}", flush=True)
                para, start = [], None
        if para:
            f.write(f"[{_ts(start)}] {' '.join(para)}\n")
    print(f"done in {time.time() - t0:.0f}s -> {dest}")
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("audio")
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--out")
    a = ap.parse_args()
    transcribe(a.audio, a.model, a.out)
