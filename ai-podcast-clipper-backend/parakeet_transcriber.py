"""
NVIDIA Parakeet TDT 0.6B v2 (English) through onnx-asr / ONNX Runtime.

Why v2: on this project's own podcasts it differed from the old WhisperX
transcripts by 5.2% (Andrew Ng) and 11.5% (child conversation), vs 6.6% / 12.4%
for the multilingual v3 and 15.8% / 26.2% for Whistle; its model-card English
average is 6.05% WER (v3: 6.34%). CC-BY-4.0, word timestamps, punctuation.

The GPU container runs the fp32 ONNX export on the CUDA execution provider and
transcribes pause-aligned chunks (see audio_utils) one at a time: batching padded
chunks changed ~4% of words and raised the difference to 12.9% (from 11.5%) on
the child conversation, and a 28 s chunk takes only milliseconds on a GPU anyway.
The same code runs on CPU (`quantization="int8"`) for tests.
"""

from __future__ import annotations

import os
import time

import audio_utils

MODEL_NAME = "nemo-parakeet-tdt-0.6b-v2"
ONNX_REPO = "istupakov/parakeet-tdt-0.6b-v2-onnx"
FP32_FILES = ["config.json", "vocab.txt", "nemo128.onnx", "decoder_joint-model.onnx",
              "encoder-model.onnx", "encoder-model.onnx.data"]
INT8_FILES = ["config.json", "vocab.txt", "nemo128.onnx", "decoder_joint-model.int8.onnx",
              "encoder-model.int8.onnx"]

# Only token *start* times are exposed; a word ends where the next word starts,
# capped so a word before a long pause does not stretch across it.
MAX_TRAILING_TOKEN_S = 0.3


def download(local_dir: str, quantization: str | None = None) -> str:
    from huggingface_hub import snapshot_download

    files = INT8_FILES if quantization == "int8" else FP32_FILES
    return snapshot_download(ONNX_REPO, local_dir=local_dir, allow_patterns=files)


def tokens_to_words(tokens: list[str], timestamps: list[float], offset_s: float,
                    chunk_end_s: float) -> list[dict]:
    """SentencePiece tokens (" word" starts a word) -> words with absolute times."""
    words: list[dict] = []
    current = None
    for token, ts in zip(tokens or [], timestamps or []):
        start = offset_s + float(ts)
        if current is None or token.startswith(" ") or token.startswith("▁"):
            if current is not None:
                words.append(current)
            current = {"word": token.strip(" ▁"), "start": start, "last": start}
        else:
            current["word"] += token
            current["last"] = start
    if current is not None:
        words.append(current)

    out = []
    for i, w in enumerate(words):
        if not w["word"]:
            continue
        next_start = words[i + 1]["start"] if i + 1 < len(words) else chunk_end_s
        end = min(next_start, w["last"] + MAX_TRAILING_TOKEN_S, chunk_end_s)
        out.append({"start": round(w["start"], 3), "end": round(max(end, w["start"] + 0.05), 3),
                    "word": w["word"]})
    return out


class ParakeetEngine:
    def __init__(self, model_dir: str | None = None, device: str = "cuda", quantization: str | None = None):
        import onnx_asr
        import onnxruntime as ort

        providers = ["CPUExecutionProvider"]
        if device == "cuda":
            if hasattr(ort, "preload_dlls"):
                try:  # load CUDA/cuDNN from the nvidia-* pip wheels torch ships
                    ort.preload_dlls()
                except Exception as exc:  # noqa: BLE001
                    print(f"onnxruntime.preload_dlls failed: {exc!r}")
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        started = time.time()
        model = onnx_asr.load_model(MODEL_NAME, model_dir, quantization=quantization, providers=providers)
        self.model = model.with_timestamps()
        self.device = device
        print(f"Parakeet v2 loaded in {time.time() - started:.1f}s; ONNX Runtime providers "
              f"{ort.get_available_providers()}")
        if device == "cuda" and "CUDAExecutionProvider" not in ort.get_available_providers():
            print("WARNING: CUDAExecutionProvider unavailable - Parakeet is running on the CPU")

    def transcribe(self, samples, offset_s: float = 0.0, batch_size: int = 1, log=print) -> list[dict]:
        """16 kHz mono float32 samples -> words [{start, end, word}] in absolute seconds."""
        import numpy as np

        audio = np.asarray(samples, dtype=np.float32)
        spans = audio_utils.split_on_pauses(audio)
        started, words = time.time(), []
        for b in range(0, len(spans), batch_size):
            batch = spans[b:b + batch_size]
            results = self.model.recognize([audio[s:e] for s, e in batch], sample_rate=audio_utils.SAMPLE_RATE)
            for (s, e), result in zip(batch, results):
                words.extend(tokens_to_words(result.tokens, result.timestamps,
                                             offset_s + s / audio_utils.SAMPLE_RATE,
                                             offset_s + e / audio_utils.SAMPLE_RATE))
        took = time.time() - started
        audio_s = len(audio) / audio_utils.SAMPLE_RATE
        if audio_s > 60:
            log(f"Parakeet: {audio_s:.0f}s audio, {len(spans)} chunks in {took:.1f}s "
                f"({audio_s / max(took, 1e-6):.0f}x real time)")
        return audio_utils.merge_words(words)


def default_model_dir() -> str | None:
    return os.environ.get("PARAKEET_MODEL_DIR") or None
