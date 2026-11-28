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


def get_switch(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "off", "false", "no")


def get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


# YouTube "Most replayed" graph and SponsorBlock (read by runner.py for every run, no
# redeploy). Both are fetched on this laptop before the video goes to Modal.
#   MOST_REPLAYED  on/off - use the graph's replay hotspots and the audience score
#   SPONSORBLOCK   on/off - keep sponsor reads, intros, outros and previews out of clips
#   REPLAY_WEIGHT  share of the final score from the audience, 0-1 (0.4 = 60% Gemini, 40% viewers)
# Rendered clips downloaded from Modal at the same time (1-16). 2 was the old value.
CLIP_DOWNLOADS_AT_ONCE = min(max(get_int("CLIP_DOWNLOADS_AT_ONCE", 8), 1), 16)

# YouTube downloads on this laptop use IPv4 (off: let the system choose). Read by runner.py.
YOUTUBE_IPV4 = get_switch("YOUTUBE_IPV4", True)

# Videos kept on this laptop after a successful run, so a re-rank needs no new download
# (videostore.py). Read by runner.py when it needs them (functions: tests change them).
#   VIDEO_KEEP_DAYS     days a video is kept after its last use (0 = keep none, delete after each run)
#   VIDEO_CACHE_MAX_GB  total size of the kept videos; least recently used go first (0 = no cap)
#   VIDEO_CACHE_DIR     where (empty = %LOCALAPPDATA%\ai-podcast-clipper\videos, outside OneDrive)
def video_keep_days() -> float:
    return max(get_float("VIDEO_KEEP_DAYS", 7.0), 0.0)


def video_cache_max_gb() -> float:
    return max(get_float("VIDEO_CACHE_MAX_GB", 10.0), 0.0)


def video_cache_dir() -> str:
    return (os.environ.get("VIDEO_CACHE_DIR") or "").strip()

MOST_REPLAYED = get_switch("MOST_REPLAYED", True)
SPONSORBLOCK = get_switch("SPONSORBLOCK", True)
# The credit card over the last seconds of every clip from a YouTube video (endcard.py).
END_CARD = get_switch("END_CARD", True)
REPLAY_WEIGHT = min(max(get_float("REPLAY_WEIGHT", 0.4), 0.0), 1.0)
MAX_CLIPS = 5

# The app's Settings page saves <data>/settings.json; its values win over .env.
USER_SETTINGS_FILE = "settings.json"
MAX_CLIPS_RANGE = (3, 8)
REPLAY_WEIGHT_MAX = 0.6


def apply_user_settings(data_dir) -> dict:
    """Read <data>/settings.json (written by the web app) and apply it to this module:
    {"maxClips": 5, "mostReplayed": true, "sponsorBlock": true, "replayWeight": 0.4,
     "endCard": true, "currency": "usd" | "inr"}. Missing or invalid values keep the .env / default ones.
    Returns the settings that were applied."""
    import json
    import pathlib

    global MOST_REPLAYED, SPONSORBLOCK, REPLAY_WEIGHT, MAX_CLIPS, END_CARD
    try:
        raw = json.loads((pathlib.Path(data_dir) / USER_SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    if isinstance(raw.get("mostReplayed"), bool):
        MOST_REPLAYED = raw["mostReplayed"]
    if isinstance(raw.get("sponsorBlock"), bool):
        SPONSORBLOCK = raw["sponsorBlock"]
    if isinstance(raw.get("endCard"), bool):
        END_CARD = raw["endCard"]
    if isinstance(raw.get("replayWeight"), (int, float)) and not isinstance(raw["replayWeight"], bool):
        REPLAY_WEIGHT = min(max(float(raw["replayWeight"]), 0.0), REPLAY_WEIGHT_MAX)
    if isinstance(raw.get("maxClips"), int) and not isinstance(raw["maxClips"], bool):
        MAX_CLIPS = min(max(raw["maxClips"], MAX_CLIPS_RANGE[0]), MAX_CLIPS_RANGE[1])
    return {"maxClips": MAX_CLIPS, "mostReplayed": MOST_REPLAYED, "sponsorBlock": SPONSORBLOCK,
            "replayWeight": REPLAY_WEIGHT, "endCard": END_CARD}


# Moment-selection, rendering and naming settings, read inside the containers (moments.py,
# render.py, clipper_names.py). main.py forwards them as a Modal secret because the
# containers have no .env file.
RANKING_KEYS = ("SPEAKER_MIN_HOLD_SECONDS", "FLASH_FRAMES_MAX", "DETECT_BATCH", "CLIP_LOUDNORM",
                "MIN_CLIPS", "CLIP_MIN_SECONDS", "CLIP_MAX_SECONDS", "CLIP_GRACE_SECONDS", "GEMINI_MODELS",
                "GEMINI_BUSY_RETRIES", "GEMINI_BUSY_WAIT_SECONDS", "GEMINI_THINKING_LEVEL",
                "GEMINI_TEMPERATURE", "GEMINI_TIMEOUT_SECONDS", "RANKING_TIME_LIMIT_SECONDS",
                "POST_TEXT_TIME_LIMIT_SECONDS", "SCORE_PROFILE", "CLIPPER_APP", "CLIPPER_SECRET")
# Keys where an empty value means something ("use the model's default"); it is sent as
# "default" because an unset key would mean the built-in default instead.
EMPTY_MEANS_DEFAULT = ("GEMINI_THINKING_LEVEL",)


def container_settings(env: dict | None = None) -> dict[str, str]:
    env = os.environ if env is None else env
    out = {key: env[key] for key in RANKING_KEYS if env.get(key)}
    for key in EMPTY_MEANS_DEFAULT:
        if key in env and not (env.get(key) or "").strip():
            out[key] = "default"
    return out


CONTAINER_SETTINGS = container_settings()
