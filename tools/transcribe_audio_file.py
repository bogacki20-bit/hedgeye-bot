"""Transcribe a saved audio file with the same pipeline as tms_stream_transcribe.

Kris approved downloading show audio on 11/4/2026, replacing the signed-link
clipboard hand-off.

    py -m tools.transcribe_audio_file <audio> <YYYY-MM-DD> <subfolder> [title]

subfolder: the_call, macro_show, capital_allocation, ...
"""
import pathlib
import sys

import av
import numpy as np

import tools.tms_stream_transcribe as t


def main(path: str, date: str, sub: str, title: str = "") -> None:
    from faster_whisper import WhisperModel
    t.OUT_DIR = t._TRANSCRIPTS / sub
    t.TITLE = title or t.SHOWS.get(sub, sub.replace("_", " ").upper())
    c = av.open(path)
    rs = av.AudioResampler(format="s16", layout="mono", rate=t.SR)
    chunks = []
    for fr in c.decode(c.streams.audio[0]):
        for o in rs.resample(fr):
            chunks.append(o.to_ndarray().flatten())
    c.close()
    pcm = np.concatenate(chunks).astype(np.int16)
    model = WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=16)
    print(t.transcribe_pcm(model, pcm, date, "small.en"))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(*sys.argv[1:5])
