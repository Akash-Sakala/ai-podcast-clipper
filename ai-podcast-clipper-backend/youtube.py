"""YouTube on this laptop: cookies, the Most replayed lookup, the download into a temp folder."""

import os
import pathlib
import shutil
import tempfile
import time

import downloader
import replay
import settings
import videostore
from jobstore import Job, apply_meta, log, set_progress

DOWNLOADS_DIR = pathlib.Path(tempfile.gettempdir()) / "ai-podcast-clipper"   # outside OneDrive

DOWNLOAD_MAX_AGE_HOURS = 48

_COOKIES: dict | None = None


def youtube_cookies() -> dict:
    """{"browser", "file"}: the YouTube login yt-dlp should use, if it can read it.
    Optional, for when YouTube asks to "confirm you're not a bot". Set in the frontend
    (or backend) .env: YTDLP_COOKIES_FILE=<path to a cookies.txt> (works with Chrome, via
    an export extension) or YTDLP_COOKIES_FROM_BROWSER=firefox. Checked once per run;
    cookies that can't be read are skipped with a note in runner.log, never a failure."""
    global _COOKIES
    if _COOKIES is not None:
        return _COOKIES
    _COOKIES = {"browser": None, "file": None}
    file = os.environ.get("YTDLP_COOKIES_FILE", "").strip().strip('"') or None
    browser = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip().strip('"') or None
    if file:
        path = pathlib.Path(file).expanduser()
        if not path.is_file():
            log(f"YTDLP_COOKIES_FILE: {path} not found; trying without it")
        else:
            problem = downloader.check_cookies(cookies_file=str(path))
            if problem:
                log(f"Could not use the cookies in {path.name} ({problem}); trying without them")
            else:
                log(f"Using YouTube cookies from {path.name}")
                _COOKIES["file"] = str(path)
                return _COOKIES
    if browser:
        problem = downloader.check_cookies(cookies_browser=browser)
        if problem:
            hint = (" Chrome and Edge on Windows encrypt their cookies so other programs can't read them:"
                    " export a cookies.txt and set YTDLP_COOKIES_FILE, or use firefox."
                    if browser.lower().split(":")[0] in ("chrome", "edge", "brave", "chromium", "opera", "vivaldi")
                    else "")
            log(f"Could not read {browser}'s cookies ({problem}); continuing without them.{hint}")
        else:
            log(f"Using YouTube cookies from {browser}")
            _COOKIES["browser"] = browser
    return _COOKIES


def fetch_youtube_info(url: str, browser: str | None = None) -> dict:
    """The metadata-only call for the Most replayed graph, with the cookie file and IPv4 setting."""
    return replay.fetch_youtube(url, browser, cookies_file=youtube_cookies()["file"], ipv4=settings.YOUTUBE_IPV4)


def fetch_replay(job: Job, timings: dict | None = None) -> None:
    """YouTube jobs: read the Most replayed graph (metadata only, no download) and the
    SponsorBlock segments on this laptop, into job.json "replay". Runs before every run,
    re-rank included, because the graph changes as a video gets more views. Never raises."""
    if (job.data.get("source") != "youtube" or not job.data.get("youtubeUrl")
            or not (settings.MOST_REPLAYED or settings.SPONSORBLOCK)):
        return
    job.update(message="Reading YouTube's Most replayed graph")
    start = time.time()

    def no_graph(url, browser):
        return {}

    record, info = replay.fetch_signals(job.data["youtubeUrl"], cookies_browser=youtube_cookies()["browser"],
                                        sponsorblock=settings.SPONSORBLOCK,
                                        fetch_info=fetch_youtube_info if settings.MOST_REPLAYED else no_graph,
                                        log=log)
    if info and not job.data.get("meta"):
        apply_meta(job, downloader.video_meta(info))   # the title shows up right away
    job.update(replay=record)
    took = round(time.time() - start, 1)
    if timings is not None:
        timings["replay"] = took
    sponsor = record.get("sponsor") or {}
    graph = f"graph with {len(record['peaks'])} hotspot(s)" if record["available"] else "no graph"
    log(f"Most replayed: {graph}"
        f"; SponsorBlock: {len(sponsor.get('segments') or [])} segment(s)"
        + (", a highlight" if sponsor.get("highlight") is not None else "") + f" ({took:.1f}s)")


def download_folder(job: Job) -> pathlib.Path:
    return DOWNLOADS_DIR / job.id


def clear_old_downloads(now: float | None = None) -> None:
    """Delete download folders older than DOWNLOAD_MAX_AGE_HOURS (failed runs that were
    never tried again). Never raises."""
    if not DOWNLOADS_DIR.is_dir():
        return
    cutoff = (now or time.time()) - DOWNLOAD_MAX_AGE_HOURS * 3600
    for folder in DOWNLOADS_DIR.iterdir():
        try:
            if folder.is_dir() and folder.stat().st_mtime < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def download_youtube(job: Job, attempts: int = 3, on_progress=None, audio_format: str | None = None,
                     update_status: bool = True) -> pathlib.Path:
    """Download the YouTube video into its temp folder; returns the path of input.mp4.
    A partial file from an earlier attempt or run is resumed (and a complete one is used
    as it is). on_progress(fraction) replaces the "downloading" progress (it may raise to
    stop the download; the partial file stays). audio_format: see downloader.pinned_format.
    A video kept from an earlier run (videostore.py) is returned as it is: no download.
    Callers must never delete or move that file (videostore.holds)."""
    kept = videostore.use(job)
    if kept is not None:
        log(f"Using the kept video ({kept.stat().st_size / 1024 ** 3:.1f} GB): no download")
        if on_progress is not None:
            on_progress(1.0)          # may raise on purpose (a stop signal): it propagates
        return kept

    import imageio_ffmpeg

    url = job.data["youtubeUrl"]
    folder = download_folder(job)
    if update_status:
        job.update(status="downloading", message="Downloading from YouTube")
    resumed = sum(p.stat().st_size for p in folder.glob("input.*.part")) / 1024 / 1024 if folder.is_dir() else 0
    log(f"Downloading {url}" + (f" (resuming {resumed:.0f} MB)" if resumed >= 1 else ""))
    cookies = youtube_cookies()
    last = [0.0]

    def progress(fraction: float) -> None:
        if time.time() - last[0] >= 1.5:
            last[0] = time.time()
            if on_progress is not None:
                on_progress(round(fraction, 3))
            else:
                set_progress(job, "downloading", fraction=round(fraction, 3))

    def retry(attempt: int, total: int, exc: Exception) -> None:
        log(f"Download attempt {attempt} failed ({exc}); resuming in {10 * attempt}s")
        job.update(message=f"Downloading from YouTube (retry {attempt}/{total - 1})")

    info = downloader.download(url, folder, attempts=attempts, ffmpeg_location=imageio_ffmpeg.get_ffmpeg_exe(),
                               cookies_browser=cookies["browser"], cookies_file=cookies["file"],
                               on_progress=progress, on_retry=retry, ipv4=settings.YOUTUBE_IPV4,
                               audio_format=audio_format)
    apply_meta(job, downloader.video_meta(info))
    log(f"Downloaded: {job.data['title']} (YouTube language: {info.get('language') or 'unknown'}, "
        f"formats {info.get('format_id') or '?'})")
    merged = downloader.audio_part(info)
    if audio_format and merged is not None and str(merged.get("format_id")) != audio_format:
        log(f"Note: the video came with audio {merged.get('format_id')}, the transcript with {audio_format}")
    graph_from_download(job, info)
    return folder / "input.mp4"


def download_youtube_audio(job: Job, on_progress=None) -> tuple[pathlib.Path, str]:
    """Download only the audio the video download will merge in (see downloader.download_audio)
    into <temp folder>/audio-download; returns (file, format id). The title shows up at once."""
    import imageio_ffmpeg

    folder = download_folder(job) / "audio-download"
    shutil.rmtree(folder, ignore_errors=True)
    cookies = youtube_cookies()
    log(f"Downloading the audio of {job.data['youtubeUrl']}")
    path, format_id, info = downloader.download_audio(
        job.data["youtubeUrl"], folder, ffmpeg_location=imageio_ffmpeg.get_ffmpeg_exe(),
        cookies_browser=cookies["browser"], cookies_file=cookies["file"], ipv4=settings.YOUTUBE_IPV4,
        on_progress=on_progress)
    apply_meta(job, downloader.video_meta(info))
    log(f"Downloaded the audio (format {format_id}, {path.stat().st_size / 1024 / 1024:.1f} MB)")
    return path, format_id


def graph_from_download(job: Job, info: dict | None) -> None:
    """If the Most replayed lookup was refused (YouTube's "confirm you're not a bot" can
    hit that call and not the download), take the graph from the download instead."""
    record = job.data.get("replay")
    if not settings.MOST_REPLAYED or not record or record.get("available"):
        return
    updated = replay.with_graph(record, info)
    if updated is not record:
        job.update(replay=updated)
        log(f"Most replayed: graph with {len(updated['peaks'])} hotspot(s), read from the download")
