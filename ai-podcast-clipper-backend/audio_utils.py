"""
Audio helpers shared by the Parakeet (GPU) and Whistle (local CPU) transcribers.

Both models transcribe long podcasts as pause-aligned chunks of at most 28 s:
cutting inside silences avoids split words, keeps memory flat and lets the GPU
batch chunks. On TED-LIUM, pause chunks were faster and more accurate than
letting the Whistle engine window the whole file itself (WER 3.89% vs 4.11%).

numpy is imported lazily so the module imports anywhere.
"""

from __future__ import annotations

SAMPLE_RATE = 16000

CHUNK_MIN_S = 15.0
CHUNK_MAX_S = 28.0
FRAME_S = 0.05          # RMS frame for the pause search
SMOOTH_FRAMES = 6       # 300 ms smoothing so cuts land inside real pauses


def split_on_pauses(samples, min_s: float = CHUNK_MIN_S, max_s: float = CHUNK_MAX_S) -> list[tuple[int, int]]:
    """Cut 16 kHz mono audio into [start, end) sample spans of at most `max_s`,
    each cut placed at the quietest 300 ms stretch between `min_s` and `max_s`."""
    import numpy as np

    audio = np.asarray(samples)
    total = len(audio)
    if total == 0:
        return []
    frame = int(FRAME_S * SAMPLE_RATE)
    n_frames = total // frame
    if n_frames == 0:
        return [(0, total)]

    framed = audio[: n_frames * frame].astype(np.float32).reshape(n_frames, frame)
    rms = np.sqrt(np.mean(framed * framed, axis=1) + 1e-12)
    rms = np.convolve(rms, np.ones(SMOOTH_FRAMES) / SMOOTH_FRAMES, mode="same")

    spans, start = [], 0
    max_len, min_len = int(max_s * SAMPLE_RATE), int(min_s * SAMPLE_RATE)
    while start < total:
        if total - start <= max_len:
            spans.append((start, total))
            break
        lo = (start + min_len) // frame
        hi = min((start + max_len) // frame, n_frames)
        if hi <= lo:
            cut = start + max_len
        else:
            cut = (lo + int(np.argmin(rms[lo:hi]))) * frame + frame // 2
        spans.append((start, cut))
        start = cut
    return spans


def merge_words(words: list[dict]) -> list[dict]:
    """Sort words and enforce monotonic, non-overlapping times."""
    words = sorted(words, key=lambda w: w["start"])
    for prev, cur in zip(words, words[1:]):
        if cur["start"] < prev["end"]:
            if prev["start"] < cur["start"]:
                prev["end"] = cur["start"]
            else:
                cur["start"] = prev["end"]
        if cur["end"] < cur["start"]:
            cur["end"] = cur["start"]
    return words


def pcm16_to_float(pcm16):
    import numpy as np

    return np.asarray(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
