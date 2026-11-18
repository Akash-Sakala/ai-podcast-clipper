"""Shared test setup: no test may reach YouTube or SponsorBlock, kept videos
(videostore.py) go to a temporary folder, never the real home directory, and the
ranking settings are the code's defaults whatever the developer's .env says."""

import os
import pathlib
import sys
import urllib.request

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Ranking settings are read once, when moments.py is imported, and the developer's .env
# may hold other values (an older CLIP_MAX_SECONDS=60, say). The tests expect the code's
# defaults, so pin them before any test module imports moments.
os.environ.update({"CLIP_MIN_SECONDS": "30", "CLIP_MAX_SECONDS": "120", "CLIP_GRACE_SECONDS": "5",
                   "MIN_CLIPS": "3", "SCORE_PROFILE": "value"})

import replay  # noqa: E402
import youtube  # noqa: E402


@pytest.fixture(autouse=True)
def no_network_signals(monkeypatch):
    def offline(*args, **kwargs):
        raise RuntimeError("network is disabled in tests")

    monkeypatch.setattr(replay, "fetch_youtube", offline)
    monkeypatch.setattr(urllib.request, "urlopen", offline)   # SponsorBlock


@pytest.fixture(autouse=True)
def fresh_cookie_check(monkeypatch):
    """youtube.youtube_cookies() checks the cookies once per run; start each test unchecked."""
    monkeypatch.setattr(youtube, "_COOKIES", None)
    monkeypatch.delenv("YTDLP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)


@pytest.fixture(autouse=True)
def kept_videos_in_a_temp_folder(monkeypatch, tmp_path_factory):
    """videostore.videos_dir() -> a fresh temp folder; the default limits whatever .env says."""
    monkeypatch.setenv("VIDEO_CACHE_DIR", str(tmp_path_factory.mktemp("kept-videos")))
    monkeypatch.setenv("VIDEO_KEEP_DAYS", "7")
    monkeypatch.setenv("VIDEO_CACHE_MAX_GB", "10")
