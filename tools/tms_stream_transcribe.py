"""Transcribe Macro Show episodes straight from their stream — no audio file.

Kris (10/4): "transcribe the shows without downloading them". The signed JW
Player manifest (read from the show's player in Kris's signed-in Chrome) is
good for ~1 hour, so a batch works in two passes:
  1. decode every episode's audio track into memory (16 kHz mono int16,
     ~60 MB per 30-min show; ~30 s each) while the links are still valid;
  2. transcribe each from memory with faster-whisper on the laptop CPU.
Only the text is written: data/transcripts/macro_show/<date>.txt.
The links file is deleted after pass 1 (the links are expired anyway).

  py -m tools.tms_stream_transcribe <links.json>
  links.json = [["2026-10-02", "https://cdn.jwplayer.com/manifests/...m3u8?exp=..&sig=.."], ...]
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import urllib.request

import numpy as np

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "transcripts" / "macro_show"
SR = 16000


def _audio_variant(manifest_url: str) -> str:
    """Lowest-bandwidth rendition (same audio track, least video)."""
    lines = urllib.request.urlopen(manifest_url, timeout=30).read().decode().splitlines()
    best = None
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
            bw = int(ln.split("BANDWIDTH=")[1].split(",")[0])
            if best is None or bw < best[0]:
                best = (bw, lines[i + 1].strip())
    if not best:
        raise RuntimeError("no renditions in manifest")
    return best[1]


def decode(manifest_url: str) -> np.ndarray:
    import av
    c = av.open(_audio_variant(manifest_url))
    rs = av.AudioResampler(format="s16", layout="mono", rate=SR)
    chunks = []
    for fr in c.decode(c.streams.audio[0]):
        for o in rs.resample(fr):
            chunks.append(o.to_ndarray().flatten())
    c.close()
    return np.concatenate(chunks).astype(np.int16)


def _ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def transcribe_pcm(model, pcm: np.ndarray, date: str, model_name: str,
                   para_sec: float = 30.0) -> pathlib.Path:
    from tools.transcribe import VOCAB
    audio = pcm.astype(np.float32) / 32768.0
    segments, info = model.transcribe(audio, beam_size=1, vad_filter=True,
                                      initial_prompt=VOCAB,
                                      condition_on_previous_text=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"{date}.txt"
    tmp = dest.with_suffix(".part")
    para, start = [], None
    with tmp.open("w", encoding="utf-8") as f:
        f.write(f"THE MACRO SHOW — {date} · {_ts(info.duration)} · transcribed locally "
                f"({model_name}; auto speech-to-text, names/numbers can be misheard)\n\n")
        for seg in segments:
            if start is None:
                start = seg.start
            para.append(seg.text.strip())
            if seg.end - start >= para_sec:
                f.write(f"[{_ts(start)}] {' '.join(para)}\n\n")
                para, start = [], None
        if para:
            f.write(f"[{_ts(start)}] {' '.join(para)}\n")
    tmp.replace(dest)
    return dest


def run(links_path: str, model_name: str = "small.en") -> None:
    links = json.loads(pathlib.Path(links_path).read_text(encoding="utf-8"))
    todo = [(d, u) for d, u in links if not (OUT_DIR / f"{d}.txt").exists()]
    print(f"{len(links)} links, {len(todo)} to do", flush=True)
    pcm = {}
    for d, u in todo:                       # pass 1 — inside the ~1 h link window
        t = time.time()
        try:
            pcm[d] = decode(u)
            print(f"decoded {d}: {len(pcm[d]) / SR / 60:.1f} min in {time.time() - t:.0f}s", flush=True)
        except Exception as e:
            print(f"DECODE FAILED {d}: {e}", flush=True)
    pathlib.Path(links_path).unlink(missing_ok=True)
    # One batch transcribes at a time (a later batch may decode while an
    # earlier one is still transcribing — decoding is network-bound).
    lock = OUT_DIR / ".transcribe.lock"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            time.sleep(20)
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="cpu", compute_type="int8",
                             cpu_threads=os.cpu_count() or 4)
        for d in sorted(pcm):               # pass 2 — from memory
            t = time.time()
            dest = transcribe_pcm(model, pcm.pop(d), d, model_name)
            print(f"transcribed {d} in {(time.time() - t) / 60:.1f} min -> {dest.name}", flush=True)
    finally:
        lock.unlink(missing_ok=True)
    print("BATCH DONE", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    run(sys.argv[1])
