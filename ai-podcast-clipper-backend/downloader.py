"""
YouTube download with yt-dlp, on this laptop (runner.py downloads into a temp folder
outside OneDrive, then uploads the video to Modal).

Built for flaky connections: a failed attempt keeps its partial file and the next
attempt resumes it, pieces are retried with a growing pause, and IPv4 is used by
default (repeated timeouts to one YouTube server are often a broken IPv6 route).

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


def backoff(attempt: int) -> float:
    """Seconds yt-dlp waits before retrying a piece: 2, 4, 8, ... up to 30."""
    return float(min(30, 2 ** max(1, attempt)))


def cookie_options(cookies_browser: str | None = None, cookies_file: str | None = None) -> dict:
    """yt-dlp options for YouTube cookies: a cookies.txt file wins over a browser."""
    if cookies_file:
        return {"cookiefile": cookies_file}
    if cookies_browser:
        return {"cookiesfrombrowser": (cookies_browser,)}
    return {}


def check_cookies(cookies_browser: str | None = None, cookies_file: str | None = None) -> str | None:
    """None if yt-dlp can read these cookies, else the reason it can't. Chrome and Edge
    on Windows encrypt their cookies so that other programs can't read them (Chrome 127+,
    "Failed to decrypt with DPAPI"); Firefox and a cookies.txt file work."""
    options = cookie_options(cookies_browser, cookies_file)
    if not options:
        return None
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, **options}) as ydl:
            jar = ydl.cookiejar
            if not any("youtube.com" in (cookie.domain or "") for cookie in jar):
                return "no YouTube cookies in them (sign in to YouTube first)"
        return None
    except Exception as exc:  # noqa: BLE001 - any failure means "run without cookies"
        return f"{type(exc).__name__}: {str(exc)[:200]}"


def ydl_options(out_dir: pathlib.Path, *, ffmpeg_location: str | None = None,
                cookies_browser: str | None = None, hook: Callable | None = None,
                ipv4: bool = True, cookies_file: str | None = None) -> dict:
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
        "retry_sleep_functions": {"http": backoff, "fragment": backoff},
        "socket_timeout": 30,
        # Resume the .part file a failed attempt left behind.
        "continuedl": True,
    }
    if ipv4:
        options["source_address"] = "0.0.0.0"
    if ffmpeg_location:
        options["ffmpeg_location"] = ffmpeg_location
    options.update(cookie_options(cookies_browser, cookies_file))
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
             sleep: Callable[[float], None] = time.sleep, ipv4: bool = True,
             cookies_file: str | None = None) -> dict:
    """Download `url` to <out_dir>/input.mp4 and return yt-dlp's info dict. A failed
    attempt's partial file stays, so the next attempt (or the next run) resumes it."""
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    info = None
    for attempt in range(1, attempts + 1):
        options = ydl_options(out_dir, ffmpeg_location=ffmpeg_location, cookies_browser=cookies_browser,
                              hook=Progress(on_progress), ipv4=ipv4, cookies_file=cookies_file)
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as exc:
            if attempt == attempts or is_blocked(str(exc)):
                raise  # a block will not clear in a few seconds
            # A new attempt gets fresh stream URLs and resumes the partial file.
            if on_retry:
                on_retry(attempt, attempts, exc)
            sleep(10 * attempt)

    if not (out_dir / "input.mp4").exists():
        raise RuntimeError("YouTube download finished but input.mp4 was not created")
    return info or {}
