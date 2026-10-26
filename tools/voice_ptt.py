"""voice_ptt.py — push-to-talk into the Claude chat window, fully local.

Hold RIGHT CTRL and talk; let go. The clip is transcribed on this laptop with
faster-whisper (same engine as the show transcripts — no API, no cost),
then pasted into the Claude desktop window and sent with Enter.

    py -m tools.voice_ptt                 # hold Right Ctrl, auto-send
    py -m tools.voice_ptt --no-send       # paste only, you press Enter
    py -m tools.voice_ptt --key F9 --model base.en   (F-keys need Fn here)

Stdlib + PyAV (mic via DirectShow) + faster-whisper; no new installs.
The mic is only open while the key is held. Ctrl+C to quit.
Later the input can be swapped for the MemoMind glasses' mic stream.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import sys
import time
import winsound

import numpy as np

MIC = "audio=Microphone Array (Realtek(R) Audio)"
VK = {f"F{i}": 0x6F + i for i in range(1, 13)}
VK.update({"RCTRL": 0xA3, "RALT": 0xA5, "RSHIFT": 0xA1, "SCROLL": 0x91, "PAUSE": 0x13})
VK_CONTROL, VK_V, VK_RETURN = 0x11, 0x56, 0x0D

# tickers / desk words whisper tends to mangle
VOCAB = ("Hedgeye, Keith, Signal Strength, Position Monitor, trade line, trend line, "
         "risk range, top of the range, decelerating volume, Quad 2, Quad 4, Tier1 Alpha, "
         "SpotGamma, Macro Show, The Call, stalk list, check-in, VLO, PSX, MPC, DINO, CVI, "
         "CRAK, BNO, BWET, XLV, HTEC, ILMN, LH, MCK, DGX, UNH, ISRG, MA, V, CPAY, OMF, FAF, "
         "FNF, RKT, DRAM, SMCI, CG, ARES, BX, AGCO, DE, EWY, QQQ, RSP, IWM, SCHA, TLT, ZROZ, "
         "UUP, DBMF, IBIT, MSFT, MSFY, AAPL, META, METD, JOET, LCID, FICO, SOFI, CFG.")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# ── clipboard + keystrokes (win32 via ctypes) ─────────────────────────────
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]


def set_clipboard(text: str) -> None:
    data = (text + "\0").encode("utf-16-le")
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    p = kernel32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    user32.EmptyClipboard()
    user32.SetClipboardData(CF_UNICODETEXT, h)
    user32.CloseClipboard()


def tap(vk: int, up_only: bool = False) -> None:
    if not up_only:
        user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)


def focus_claude() -> bool:
    """Bring the Claude desktop window forward (title contains 'Claude')."""
    found = []
    proto = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if "Claude" in buf.value and "Chrome" not in buf.value:
                found.append(hwnd)
        return True

    user32.EnumWindows(proto(cb), 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.GetForegroundWindow() == hwnd:
        return True
    # Windows blocks a windowless (pyw) process from stealing focus. Attach our
    # input to the current foreground thread so SetForegroundWindow is honoured.
    # (No ALT-tap trick: in the Electron app ALT opens the menu bar and eats the
    # paste.)
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != me and user32.AttachThreadInput(me, fg_tid, True))
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)      # SW_RESTORE only if minimized
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_tid, False)
    time.sleep(0.35)
    return user32.GetForegroundWindow() == hwnd


def paste(text: str, send: bool) -> None:
    set_clipboard(text)
    focused = focus_claude()
    while key_down(0xA3) or key_down(0xA2) or key_down(0x12):   # wait for Ctrl/Alt to be up
        time.sleep(0.02)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    tap(VK_V)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    if send:
        time.sleep(0.15)
        tap(VK_RETURN)
    if not focused:
        print("   (couldn't focus the Claude window — pasted into whatever had focus)")
    return focused


# ── mic capture while the key is held ─────────────────────────────────────
def record_while_held(vk: int) -> np.ndarray:
    import av
    container = av.open(MIC, format="dshow", options={"audio_buffer_size": "50"})
    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    winsound.Beep(880, 80)                  # mic is live — start talking now
    chunks = []
    try:
        for frame in container.decode(stream):
            for rf in resampler.resample(frame):
                chunks.append(rf.to_ndarray().reshape(-1))
            if not key_down(vk):
                break
    finally:
        container.close()
    return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="RCTRL")   # F-keys are media keys on this laptop
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--no-send", action="store_true")
    a = ap.parse_args()
    vk = VK[a.key.upper()]

    from faster_whisper import WhisperModel
    print(f"loading {a.model} …", flush=True)
    model = WhisperModel(a.model, device="cpu", compute_type="int8")
    print(f"ready — hold {a.key} and talk (Ctrl+C to quit)", flush=True)

    import pathlib
    logf = pathlib.Path(__file__).resolve().parent.parent / "data" / "voice_ptt.log"

    def log(msg):
        print(msg, flush=True)
        with logf.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%m-%d %H:%M:%S')} {msg}\n")

    while True:
        if key_down(vk):
            t0 = time.time()
            try:
                pcm = record_while_held(vk)
                winsound.Beep(660, 80)
                secs = len(pcm) / 16000
                if secs < 0.4:                  # accidental tap
                    continue
                log(f"recorded {secs:.1f}s (held {time.time() - t0:.1f}s) — transcribing…")
                # long clips: faster greedy decode so it doesn't feel stuck
                segs, _ = model.transcribe(pcm, beam_size=5 if secs < 20 else 1,
                                           vad_filter=True, initial_prompt=VOCAB,
                                           language="en")
                text = " ".join(s.text.strip() for s in segs).strip()
                log(f"[{time.time() - t0:4.1f}s total] {len(text)} chars: {text[:200]}")
                if text:
                    ok = paste(text, send=not a.no_send)
                    log("pasted into Claude" if ok else "WARN: Claude window not focused")
                    winsound.Beep(990, 60) if ok else winsound.Beep(400, 150)
                else:
                    winsound.Beep(300, 200)     # heard nothing
            except Exception as e:              # keep listening after any glitch
                log(f"ERROR {type(e).__name__}: {e}")
                winsound.Beep(250, 300)
        time.sleep(0.03)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
