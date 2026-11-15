"""
Names of the Modal objects this app uses, in one place.

    CLIPPER_APP      app name (default "ai-podcast-clipper"); the jobs volume is
                     "<app>-jobs" and the live progress Dict "<app>-progress"
    CLIPPER_SECRET   Modal secret with GEMINI_API_KEY (default "ai-podcast-clipper-secret",
                     whatever CLIPPER_APP is, so a test deployment can share the real key)

Set CLIPPER_APP in .env (or the shell) to deploy and run a test copy next to the real
app: `modal deploy main.py` and runner.py both read it here, and main.py forwards it to
the containers (settings.CONTAINER_SETTINGS), so the names are the same everywhere.
Bundled into the Modal image (main.py, add_local_python_source).
"""

from __future__ import annotations

import os

import settings

ENV_FILE = settings.ENV_FILE        # importing settings loads this .env first

DEFAULT_APP = "ai-podcast-clipper"
DEFAULT_SECRET = "ai-podcast-clipper-secret"


def names(env: dict | None = None) -> dict[str, str]:
    """{"app", "volume", "progress", "secret"} for these environment variables."""
    env = os.environ if env is None else env
    app = (env.get("CLIPPER_APP") or "").strip() or DEFAULT_APP
    return {"app": app, "volume": f"{app}-jobs", "progress": f"{app}-progress",
            "secret": (env.get("CLIPPER_SECRET") or "").strip() or DEFAULT_SECRET}


_NAMES = names()
APP_NAME = _NAMES["app"]
JOBS_VOLUME = _NAMES["volume"]
PROGRESS_DICT = _NAMES["progress"]
SECRET_NAME = _NAMES["secret"]
# The two deployed functions the runner calls (main.py): phase 1 and phase 2.
TRANSCRIBE_FUNCTION = "transcribe_and_rank"
RENDER_FUNCTION = "render_clips"
