"""Shared test setup: no test may reach YouTube or SponsorBlock."""

import pathlib
import sys
import urllib.request

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import replay  # noqa: E402


@pytest.fixture(autouse=True)
def no_network_signals(monkeypatch):
    def offline(*args, **kwargs):
        raise RuntimeError("network is disabled in tests")

    monkeypatch.setattr(replay, "fetch_youtube", offline)
    monkeypatch.setattr(urllib.request, "urlopen", offline)   # SponsorBlock
