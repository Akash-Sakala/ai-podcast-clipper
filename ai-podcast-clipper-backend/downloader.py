"""
YouTube download with yt-dlp, shared by the Modal download function
(main.download_video, which saves straight into the jobs volume) and the laptop
fallback in runner.py (used when YouTube blocks Modal, or YOUTUBE_DOWNLOAD=laptop).

Standard library only at import time; yt-dlp is imported when a download starts.
"""

from __future__ import annotations

import pathlib
import time
from typing import Callable

# Prefer H.264 (avc1) video up to 1080p - the clipping pipeline (OpenCV, LR-ASD)
# handles it best - plus M4A audio, merged into input.mp4.
FORMAT = ("bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/"
          "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/"
          "b[ext=mp4][height<=1080]/bv*[height<=1080]+ba/b")

# Phrases in yt-dlp errors that mean YouTube refused this computer or IP address,
# rather than the video being unavailable.
BLOCKED_HINTS = ("not a bot", "sign in to confirm", "http error 429", "too many requests")


def ydl_options(out_dir: pathlib.Path, *, ffmpeg_location: str | None = None,
                cookies_browser: str | None = None, hook: Callable | None = None) -> dict:
    options = {
        "format": FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "input.%(ext)s"),
        # YouTube needs a JavaScript runtime: Deno in the Modal image, Node on the laptop.
        "js_runtimes": {"deno": {}, "node": {}},
        "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        "noplaylist": True,
        "noprogress": True,
        # Download in 10 MB pieces and retry failed pieces - YouTube often answers
        # large single requests with "HTTP Error 403: Forbidden".
        "http_chunk_size": 10 * 1024 * 1024,
        "retries": 10,
        "fragment_retries": 10,
    }
    if ffmpeg_location:
        options["ffmpeg_location"] = ffmpeg_location
    if cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)
    if hook:
        options["progress_hooks"] = [hook]
    return options


class Progress:
    """Share of the download done, over every stream yt-dlp fetches (video, then audio)."""

    def __init__(self, report: Callable[[float], None] | None = None):
        self.report = report
        self.files: dict[str, tuple[float, float]] = {}

    def __call__(self, status: dict) -> None:
        name = str(status.get("filename") or status.get("tmpfilename") or "?")
        total = float(status.get("total_bytes") or status.get("total_bytes_estimate") or 0)
        done = float(status.get("downloaded_bytes") or 0)
        if status.get("status") == "finished":
            done = total = max(total, done, 1.0)
        if total <= 0:
            return
        self.files[name] = (min(done, total), total)
        if self.report:
            self.report(self.fraction)

    @property
    def fraction(self) -> float:
        done = sum(d for d, _ in self.files.values())
        total = sum(t for _, t in self.files.values())
        # The audio stream is unknown until the video finishes: cap so the bar never
        # reaches 100% before the merge.
        return min(0.99, done / total) if total else 0.0


def video_meta(info: dict | None) -> dict:
    """The video facts kept in job.json (and given to Gemini as context)."""
    info = info or {}
    return {
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "description": (info.get("description") or "")[:2000],
        "tags": (info.get("tags") or [])[:30],
        "language": info.get("language"),
        "duration": info.get("duration"),
    }


def is_blocked(message: str | None) -> bool:
    text = (message or "").lower()
    return any(hint in text for hint in BLOCKED_HINTS)


def download(url: str, out_dir: pathlib.Path, *, attempts: int = 3, ffmpeg_location: str | None = None,
             cookies_browser: str | None = None, on_progress: Callable[[float], None] | None = None,
             on_retry: Callable[[int, int, Exception], None] | None = None,
             sleep: Callable[[float], None] = time.sleep) -> dict:
    """Download `url` to <out_dir>/input.mp4 and return yt-dlp's info dict."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    info = None
    for attempt in range(1, attempts + 1):
        options = ydl_options(out_dir, ffmpeg_location=ffmpeg_location, cookies_browser=cookies_browser,
                              hook=Progress(on_progress))
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as exc:
            if attempt == attempts or is_blocked(str(exc)):
                raise  # a block will not clear in a few seconds
            # Stream URLs are signed per request - start over with fresh ones.
            for partial in out_dir.glob("input.*"):
                partial.unlink(missing_ok=True)
            if on_retry:
                on_retry(attempt, attempts, exc)
            sleep(10 * attempt)

    if not (out_dir / "input.mp4").exists():
        raise RuntimeError("YouTube download finished but input.mp4 was not created")
    return info or {}
