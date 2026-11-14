"""Shared test setup: no test may reach YouTube or SponsorBlock."""

import pathlib
import sys
import urllib.request

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import replay  # noqa: E402
import runner  # noqa: E402


@pytest.fixture(autouse=True)
def no_network_signals(monkeypatch):
    def offline(*args, **kwargs):
        raise RuntimeError("network is disabled in tests")

    monkeypatch.setattr(replay, "fetch_youtube", offline)
    monkeypatch.setattr(urllib.request, "urlopen", offline)   # SponsorBlock


@pytest.fixture(autouse=True)
def fresh_cookie_check(monkeypatch):
    """runner.youtube_cookies() checks the cookies once per run; start each test unchecked."""
    monkeypatch.setattr(runner, "_COOKIES", None)
    monkeypatch.delenv("YTDLP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)
