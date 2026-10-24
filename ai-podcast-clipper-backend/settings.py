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


# GPU for the whole cloud pipeline (Parakeet transcription, Gemini call, rendering).
# Read at `modal deploy` time.
CLIPPER_GPU = get_str("CLIPPER_GPU", "A10")


# Moment-selection settings, read inside the GPU container (moments.py). main.py
# forwards them as a Modal secret because the container has no .env file.
RANKING_KEYS = ("MIN_CLIPS", "CLIP_MIN_SECONDS", "CLIP_MAX_SECONDS", "CLIP_GRACE_SECONDS", "GEMINI_MODELS",
                "GEMINI_BUSY_RETRIES", "GEMINI_BUSY_WAIT_SECONDS", "GEMINI_THINKING_LEVEL",
                "GEMINI_TEMPERATURE", "GEMINI_TIMEOUT_SECONDS", "RANKING_TIME_LIMIT_SECONDS")
CONTAINER_SETTINGS = {key: os.environ[key] for key in RANKING_KEYS if os.environ.get(key)}
