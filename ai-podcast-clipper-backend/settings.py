"""
Backend settings from `ai-podcast-clipper-backend/.env` (see `.env.example`).

Read by main.py when you run `modal deploy`, and by runner.py for every job.
Values already set in the shell win over the file.
"""

from __future__ import annotations

import os
import pathlib

ENV_FILE = pathlib.Path(__file__).resolve().parent / ".env"


def load_env_file(path: pathlib.Path = ENV_FILE) -> dict[str, str]:
    """Minimal KEY=VALUE parser (comments, blank lines, optional quotes)."""
    loaded: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return loaded
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


load_env_file()


def get_int(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name) or default))
    except ValueError:
        return default


def get_str(name: str, default: str) -> str:
    return os.environ.get(name) or default


# GPU for transcription and for each clip's render container. Read at `modal deploy`
# time. A comma-separated list ("L40S,A10") is a fallback order: Modal uses the first
# type that is free. Pick GPUs with an NVENC video encoder (T4, L4, A10, L40S, RTX PRO
# 6000); A100/H100/H200/B200 have none, so clips would be encoded on the CPU.
CLIPPER_GPU = get_str("CLIPPER_GPU", "A10")
NO_NVENC_GPUS = ("A100", "H100", "H200", "B200", "B300")


def gpu_spec(value: str | None = None) -> str | list[str]:
    """CLIPPER_GPU as Modal's `gpu=` argument: one type, or a list tried in order."""
    names = [part.strip() for part in (value if value is not None else CLIPPER_GPU).split(",") if part.strip()]
    if not names:
        return "A10"
    return names[0] if len(names) == 1 else names


# Render containers: one per clip, all at once (the Starter plan allows 10 GPUs at a time).
RENDER_MAX_CONTAINERS = get_int("RENDER_MAX_CONTAINERS", 8)
# CPU cores reserved for each render container (decoding and framing run on the CPU).
RENDER_CPU = get_int("RENDER_CPU", 4)


# Where YouTube videos are downloaded (read by runner.py for every job):
#   cloud  - straight into the Modal jobs volume; the laptop takes over if YouTube blocks Modal
#   laptop - on this laptop, then uploaded to Modal (the old way)
YOUTUBE_DOWNLOAD = "laptop" if get_str("YOUTUBE_DOWNLOAD", "cloud").strip().lower() == "laptop" else "cloud"
# yt-dlp and Deno (its JavaScript runtime) in the Modal download image. YouTube changes
# often: bump YTDLP_VERSION and run `modal deploy main.py` when downloads start failing.
# Only the small download image is rebuilt, not the GPU image.
YTDLP_VERSION = get_str("YTDLP_VERSION", "2026.10.21")
DENO_VERSION = get_str("DENO_VERSION", "2.9.8")


# Moment-selection and rendering settings, read inside the containers (moments.py,
# render.py). main.py forwards them as a Modal secret because the containers have no .env file.
RANKING_KEYS = ("SPEAKER_MIN_HOLD_SECONDS", "FLASH_FRAMES_MAX",
                "MIN_CLIPS", "CLIP_MIN_SECONDS", "CLIP_MAX_SECONDS", "CLIP_GRACE_SECONDS", "GEMINI_MODELS",
                "GEMINI_BUSY_RETRIES", "GEMINI_BUSY_WAIT_SECONDS", "GEMINI_THINKING_LEVEL",
                "GEMINI_TEMPERATURE", "GEMINI_TIMEOUT_SECONDS", "RANKING_TIME_LIMIT_SECONDS")
CONTAINER_SETTINGS = {key: os.environ[key] for key in RANKING_KEYS if os.environ.get(key)}
