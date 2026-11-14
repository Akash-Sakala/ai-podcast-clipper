"""
Local job runner for AI Podcast Clipper.

Runs on your laptop. The web app starts it for every job, but you can also use
it directly from the terminal:

    python runner.py --video "C:\\path\\to\\podcast.mp4"
    python runner.py --youtube "https://www.youtube.com/watch?v=..."
    python runner.py --job-dir "..\\data\\jobs\\<job id>"     (what the web app calls)
    python runner.py --job-dir "..\\data\\jobs\\<job id>" --rerank   (re-rank & re-render)
    python runner.py --thumbnails                              (covers and clip posters for older videos)
    python runner.py --prune-videos [--dry-run]                (delete videos kept by older versions)
    python runner.py --backfill-replay [--force]               (Most replayed graph + SponsorBlock for older videos)
    python runner.py --modal-bill                              (this month's Modal bill -> data/stats, for the stats page)
    python runner.py --check-rates                             (Modal's live prices as the cost estimate reads them)

Steps: (YouTube) read the Most replayed graph and SponsorBlock segments on this laptop
-> video into the Modal jobs volume -> run the deployed pipeline -> download clips
into <job dir>/clips -> update <job dir>/job.json -> delete the video everywhere.

YouTube videos download on this laptop into a temp folder outside OneDrive
(<temp>/ai-podcast-clipper/<job id>), are uploaded to the volume, and the temp copy is
deleted. A failed download keeps its partial file there, so Try again resumes it;
folders left behind for over two days are cleared at the next run. An uploaded MP4 is
sent from the job folder. Once the clips are saved, input.mp4 is deleted from the job
folder and the volume: only job.json, the transcript, the moments, the clips and the
thumbnail stay. Re-rank downloads a YouTube video again; an upload is added again.

In the cloud, a GPU container transcribes the video with Parakeet TDT 0.6B v2
(English), Gemini ranks the best moments, and every clip renders in its own GPU
container at the same time. The live progress (stage, share of the clips done)
and the estimated cost are mirrored into job.json for the web app.

Requires `modal deploy main.py` to have been run once.
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Callable

import costs
import downloader
import fx
import replay
import run_history
import settings  # loads ai-podcast-clipper-backend/.env

APP_NAME = "ai-podcast-clipper"
FUNCTION_NAME = "process_job"
JOBS_VOLUME = "ai-podcast-clipper-jobs"

PROGRESS_DICT = "ai-podcast-clipper-progress"   # live stage/clip counts written by the GPU
POLL_SECONDS = 3
DOWNLOADS_DIR = pathlib.Path(tempfile.gettempdir()) / "ai-podcast-clipper"   # outside OneDrive
DOWNLOAD_MAX_AGE_HOURS = 48
FINGERPRINT_CHUNK = 1024 * 1024                  # same as the web app's lib/fingerprint.ts

BACKEND_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BACKEND_DIR.parent / "data"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {message}", flush=True)


class Job:
    """Reads and writes <job dir>/job.json (shared format with the web app)."""

    def __init__(self, job_dir: pathlib.Path):
        self.dir = job_dir
        self.file = job_dir / "job.json"
        self.data = json.loads(self.file.read_text(encoding="utf-8"))

    @property
    def id(self) -> str:
        return self.data["id"]

    def update(self, **fields) -> None:
        self.data.update(fields)
        self.data["updatedAt"] = now_iso()
        self.save()

    def save(self) -> None:
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        for attempt in range(10):
            try:
                os.replace(tmp, self.file)
                return
            except PermissionError:  # Windows: the web app may be reading job.json
                time.sleep(0.2 * (attempt + 1))
        os.replace(tmp, self.file)

    @staticmethod
    def create(data_dir: pathlib.Path, source: str, title: str, youtube_url: str | None = None) -> "Job":
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        job_id = f"{stamp}-{secrets.token_hex(3)}"
        job_dir = data_dir / "jobs" / job_id
        job_dir.mkdir(parents=True)
        data = {
            "id": job_id,
            "source": source,
            "title": title,
            "youtubeUrl": youtube_url,
            "status": "queued",
            "message": "Waiting to start",
            "error": None,
            "clips": [],
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
        }
        (job_dir / "job.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
        return Job(job_dir)


def apply_meta(job: Job, meta: dict) -> None:
    job.update(title=meta.get("title") or job.data["title"], meta=meta)


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
    log(f"Most replayed: {'graph with ' + str(len(record['peaks'])) + ' hotspot(s)' if record['available'] else 'no graph'}"
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


def download_youtube(job: Job, attempts: int = 3) -> pathlib.Path:
    """Download the YouTube video into its temp folder; returns the path of input.mp4.
    A partial file from an earlier attempt or run is resumed."""
    import imageio_ffmpeg

    url = job.data["youtubeUrl"]
    folder = download_folder(job)
    job.update(status="downloading", message="Downloading from YouTube")
    resumed = sum(p.stat().st_size for p in folder.glob("input.*.part")) / 1024 / 1024 if folder.is_dir() else 0
    log(f"Downloading {url}" + (f" (resuming {resumed:.0f} MB)" if resumed >= 1 else ""))
    cookies = youtube_cookies()
    last = [0.0]

    def progress(fraction: float) -> None:
        if time.time() - last[0] >= 1.5:
            last[0] = time.time()
            set_progress(job, "downloading", fraction=round(fraction, 3))

    def retry(attempt: int, total: int, exc: Exception) -> None:
        log(f"Download attempt {attempt} failed ({exc}); resuming in {10 * attempt}s")
        job.update(message=f"Downloading from YouTube (retry {attempt}/{total - 1})")

    info = downloader.download(url, folder, attempts=attempts, ffmpeg_location=imageio_ffmpeg.get_ffmpeg_exe(),
                               cookies_browser=cookies["browser"], cookies_file=cookies["file"],
                               on_progress=progress, on_retry=retry, ipv4=settings.YOUTUBE_IPV4)
    apply_meta(job, downloader.video_meta(info))
    log(f"Downloaded: {job.data['title']} (YouTube language: {info.get('language') or 'unknown'})")
    graph_from_download(job, info)
    return folder / "input.mp4"


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


def put_video_on_modal(job: Job, volume, timings: dict, keep_video: bool = False) -> None:
    """Get the video into /<job id>/input.mp4 on the Modal volume.

    A video in the job folder (an upload) is uploaded from there. A YouTube video is
    downloaded into its temp folder, uploaded, and the temp folder is deleted (with
    keep_video, the video moves into the job folder instead)."""
    local = job.dir / "input.mp4"
    source = local
    transfer = "upload"
    if not local.exists() and job.data.get("source") == "youtube":
        transfer = "laptop-download"
        job.update(videoTransfer=transfer)
        set_progress(job, "downloading")
        start = time.time()
        source = download_youtube(job)
        timings["download"] = round(time.time() - start, 1)

    if not source.exists():
        raise FileNotFoundError("The uploaded video is no longer in the job folder: add the same MP4 again "
                                "to re-rank it")
    if transfer == "upload":
        job.update(videoTransfer=transfer)
    set_progress(job, "uploading")
    start = time.time()
    upload_input(job, volume, video=source)
    timings["upload"] = round(time.time() - start, 1)
    if source != local:
        if keep_video:
            try:
                shutil.move(str(source), str(local))
            except OSError as exc:
                log(f"Could not keep the video in the job folder: {exc}")
        shutil.rmtree(source.parent, ignore_errors=True)


def discard_local_video(job: Job) -> float:
    """Delete input.mp4 (and any partial download) from the job folder once the clips
    are saved. Returns the MB freed. Never raises."""
    freed = 0
    for path in job.dir.glob("input.*"):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            for attempt in range(5):
                try:
                    path.unlink()
                    break
                except PermissionError:  # Windows: the web app or a player may have it open
                    if attempt == 4:
                        raise
                    time.sleep(0.5 * (attempt + 1))
            freed += size
        except OSError as exc:
            log(f"Could not delete {path.name}: {exc}")
    return freed / 1024 / 1024


def fingerprint_ranges(size: int) -> list[tuple[int, int]]:
    if size <= 3 * FINGERPRINT_CHUNK:
        return [(0, size)]
    middle = size // 2 - FINGERPRINT_CHUNK // 2
    return [(0, FINGERPRINT_CHUNK), (middle, middle + FINGERPRINT_CHUNK), (size - FINGERPRINT_CHUNK, size)]


def file_fingerprint(path: pathlib.Path) -> str | None:
    """The web app's upload fingerprint (lib/fingerprint.ts): SHA-256 of "<size>:" plus
    1 MiB from the start, middle and end. Lets a re-added file be recognised after its
    copy in the job folder is gone."""
    try:
        size = path.stat().st_size
        digest = hashlib.sha256(f"{size}:".encode())
        with open(path, "rb") as f:
            for start, end in fingerprint_ranges(size):
                f.seek(start)
                digest.update(f.read(end - start))
        return digest.hexdigest()
    except OSError:
        return None


def hide_child_windows() -> None:
    """On Windows, start every child process (ffmpeg, yt-dlp helpers) without a
    console window. The web app runs this script with pythonw.exe, which has no
    console, so each console program it starts would otherwise open its own window."""
    if os.name != "nt":
        return
    create_no_window = 0x08000000
    new_console_flags = 0x00000010 | 0x00000008  # CREATE_NEW_CONSOLE, DETACHED_PROCESS
    original_init = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if not flags & new_console_flags:
            kwargs["creationflags"] = flags | create_no_window
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = init


def save_frame(video: pathlib.Path, target: pathlib.Path, at_seconds: float, video_filter: str) -> bool:
    """Write one JPEG frame of `video` to `target`. Never raises: a missing image
    only means the web app shows a plain placeholder."""
    import imageio_ffmpeg

    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
               "-ss", f"{at_seconds:.2f}", "-i", str(video), "-frames:v", "1",
               "-vf", video_filter, "-q:v", "4", str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
        return target.exists()
    except Exception as exc:  # noqa: BLE001
        log(f"Could not save {target.name}: {exc}")
        return False


def save_thumbnail(job: Job, size: str = "320:180") -> bool:
    """Save a 16:9 frame from about 10% into input.mp4 as thumbnail.jpg (the cover in
    the web app's video list). YouTube jobs get YouTube's own cover from the web app."""
    import imageio_ffmpeg

    video = job.dir / "input.mp4"
    if not video.exists():
        return False
    try:
        reader = imageio_ffmpeg.read_frames(str(video))
        duration = float(next(reader).get("duration") or 0)
        reader.close()
    except Exception:  # noqa: BLE001 - fall back to the first frame
        duration = 0
    width, height = size.split(":")
    return save_frame(video, job.dir / "thumbnail.jpg", duration * 0.1,
                      f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}")


def save_clip_poster(clip: pathlib.Path) -> bool:
    """clip_N.mp4 -> clip_N.jpg: the still the clip card shows before the video loads."""
    return save_frame(clip, clip.with_suffix(".jpg"), 0.5, "scale=540:-2")


def backfill_thumbnails(data_dir: pathlib.Path) -> int:
    """Save missing covers for uploaded videos and missing posters for all clips."""
    covers = posters = 0
    for job_dir in sorted((data_dir / "jobs").glob("*")):
        if not (job_dir / "job.json").exists():
            continue
        job = Job(job_dir)
        if (job.data.get("source") == "upload" and not (job_dir / "thumbnail.jpg").exists()
                and save_thumbnail(job)):
            covers += 1
        for clip in sorted((job_dir / "clips").glob("clip_*.mp4")):
            if not clip.with_suffix(".jpg").exists() and save_clip_poster(clip):
                posters += 1
    log(f"Saved {covers} video cover(s) and {posters} clip poster(s)")
    return 0


UPLOAD_PIECES_AT_ONCE = 4        # Modal's default is 128: on a home uplink every piece crawls and times out
UPLOAD_PIECE_TIMEOUT_S = 600     # a piece (8 MiB) that takes longer is retried; Modal sets no limit
UPLOAD_STALL_S = 120             # no bytes for this long: say so in runner.log and the progress strip
MODAL_OUTAGE_S = 900             # keep waiting for a running job through connection drops this long


class UploadProgress:
    """Counts the bytes Modal reports as sent (its upload progress callback: `advance=`
    bytes, negative when a failed piece is retried, `reset=True` to start a file over) and
    mirrors the share done into job.json every few seconds, plus a line in runner.log every
    10% and when the upload stalls."""

    def __init__(self, job: Job, size: int, every_s: float = 2.0, clock: Callable[[], float] = time.time):
        self.job, self.size, self.every_s, self.clock = job, max(size, 1), every_s, clock
        self.sent = 0
        self.started = self.last_bytes_at = clock()
        self.last_report = 0.0
        self.last_logged = 0
        self.stalled = False

    def __call__(self, *args, **kwargs):
        advance = kwargs.get("advance")
        if kwargs.get("reset"):
            self.sent = 0
        elif isinstance(advance, (int, float)) and advance:
            self.sent = max(0, self.sent + int(advance))
            if advance > 0:
                self.last_bytes_at = self.clock()
                if self.stalled:
                    self.stalled = False
                    log("Upload moving again")
                    self.report(force=True)
                    return None
        self.report()
        return None   # Modal asks for a task id; any value works

    @property
    def fraction(self) -> float:
        return min(0.99, self.sent / self.size)

    def report(self, force: bool = False) -> None:
        now = self.clock()
        if not force and now - self.last_report < self.every_s:
            return
        self.last_report = now
        speed = self.sent / max(now - self.started, 0.001)
        eta = round((self.size - self.sent) / speed) if speed > 0 and not self.stalled else None
        set_progress(self.job, "uploading", fraction=round(self.fraction, 3), etaSeconds=eta,
                     stalled=self.stalled or None)
        tenth = int(self.fraction * 10)
        if tenth > self.last_logged:
            self.last_logged = tenth
            log(f"Uploaded {self.sent / 1024 / 1024:.0f} of {self.size / 1024 / 1024:.0f} MB "
                f"({speed * 8 / 1e6:.1f} Mbps on average)")

    def check_stall(self) -> None:
        """Called every few seconds by a watcher thread: flag an upload with no bytes moving."""
        idle = self.clock() - self.last_bytes_at
        if not self.stalled and idle >= UPLOAD_STALL_S:
            self.stalled = True
            log(f"Upload: no data sent for {idle / 60:.0f} min; Modal retries the stuck pieces "
                f"(each gets up to {UPLOAD_PIECE_TIMEOUT_S // 60} min)")
            self.report(force=True)


def limit_upload_piece_time(seconds: float) -> bool:
    """Give Modal's file-transfer HTTP session a per-request time limit (it has none, so a
    connection that dies quietly hangs a piece forever; with a limit the piece is retried).
    Private Modal API: skipped, returning False, if this modal version doesn't fit."""
    try:
        from modal._utils import http_utils

        registry = http_utils.ClientSessionRegistry
        if getattr(registry, "_clipper_timeout", None) == seconds:
            return True
        make_session = http_utils._http_client_with_tls
        shutdown = http_utils.on_shutdown

        def get_session():
            if not registry._client_session_active:
                registry._client_session = make_session(timeout=seconds)
                registry._client_session_active = True
                shutdown(registry.close_session())
            return registry._client_session

        registry.get_session = staticmethod(get_session)
        registry._clipper_timeout = seconds
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"Could not set an upload time limit ({type(exc).__name__}: {exc})")
        return False


def upload_context(volume, progress: UploadProgress):
    """Modal's batch upload, built the way `batch_upload` builds it but with a progress
    callback and fewer pieces in flight (Volumes v2). None if this modal version doesn't fit."""
    try:
        import modal.volume as modal_volume
        from modal_proto import api_pb2

        volume.hydrate()
        version = getattr(volume._get_metadata(), "version", None)
        if version == api_pb2.VolumeFsVersion.VOLUME_FS_VERSION_V2:
            return modal_volume.VolumeUploadContextManager2(
                volume.object_id, volume.client, progress_cb=progress, force=True,
                put_concurrency=UPLOAD_PIECES_AT_ONCE)
        return modal_volume.AbstractVolumeUploadContextManager.resolve(
            version, volume.object_id, volume.client, progress_cb=progress, force=True)
    except Exception as exc:  # noqa: BLE001
        log(f"Upload progress unavailable ({type(exc).__name__}: {exc}); uploading without it")
        return None


def upload_with_progress(volume, local: pathlib.Path, remote: str, progress: UploadProgress) -> None:
    """Upload one file with live progress, a time limit per piece and a stall watcher.
    Falls back to the plain `batch_upload` when Modal's internals don't fit."""
    import threading

    limit_upload_piece_time(UPLOAD_PIECE_TIMEOUT_S)
    batch = upload_context(volume, progress)
    if batch is None:
        with volume.batch_upload(force=True) as plain:
            plain.put_file(str(local), remote)
        return
    done = threading.Event()

    def watch():
        while not done.wait(10):
            progress.check_stall()

    threading.Thread(target=watch, daemon=True).start()
    try:
        with batch:
            batch.put_file(str(local), remote)
    finally:
        done.set()


def upload_input(job: Job, volume, update_status: bool = True, video: pathlib.Path | None = None) -> None:
    video = video or job.dir / "input.mp4"
    size = video.stat().st_size
    size_mb = size / 1024 / 1024
    if update_status:
        job.update(status="uploading", message=f"Uploading video to Modal ({size_mb:.0f} MB)")
    log(f"Uploading input.mp4 ({size_mb:.0f} MB) to volume {JOBS_VOLUME}")
    start = time.time()
    upload_with_progress(volume, video, f"/{job.id}/input.mp4", UploadProgress(job, size))
    took = time.time() - start
    log(f"Uploaded in {took:.0f}s ({size * 8 / 1e6 / max(took, 0.001):.1f} Mbps)")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def gemini_context(job: Job) -> dict:
    meta = job.data.get("meta") or {}
    context = {
        "title": meta.get("title") or job.data.get("title"),
        "channel": meta.get("channel"),
        "description": meta.get("description"),
    }
    signals = replay.ranking_context(job.data.get("replay"), settings.REPLAY_WEIGHT)
    if signals:
        context["replay"] = signals
    return context


def upload_transcript(job: Job, volume) -> None:
    with volume.batch_upload(force=True) as batch:
        batch.put_file(str(job.dir / "transcript.json"), f"/{job.id}/transcript.json")


def set_progress(job: Job, stage: str, **fields) -> None:
    """Structured progress for the UI: stage = downloading | uploading | transcribing |
    ranking | rendering | saving, plus optional done/total/etaSeconds."""
    previous = job.data.get("progress") or {}
    started = previous.get("stageStartedAt") if previous.get("stage") == stage else now_iso()
    job.update(progress={"stage": stage, "stageStartedAt": started, **fields})


# --------------------------------------------------------------------------- #
# Cloud step
# --------------------------------------------------------------------------- #

def wait_for(call, job: Job):
    """The result of a spawned Modal call, mirroring its live progress into job.json meanwhile."""
    import modal

    progress = modal.Dict.from_name(PROGRESS_DICT, create_if_missing=True)
    last = None
    outage_since = None
    try:
        while True:
            try:
                result = call.get(timeout=POLL_SECONDS)
                if outage_since is not None:
                    log("Connection to Modal is back")
                return result
            except (TimeoutError, modal.exception.TimeoutError):
                if outage_since is not None:
                    log("Connection to Modal is back")
                    outage_since = None
            except (modal.exception.ConnectionError, OSError) as exc:
                # The job keeps running on Modal: keep asking for its result.
                now = time.time()
                if outage_since is None:
                    outage_since = now
                    log(f"Lost the connection to Modal ({type(exc).__name__}: {exc}); the job keeps "
                        f"running there, retrying for up to {MODAL_OUTAGE_S // 60} min")
                elif now - outage_since > MODAL_OUTAGE_S:
                    raise
                time.sleep(POLL_SECONDS)
                continue
            try:
                current = progress.get(job.id)
            except Exception:  # noqa: BLE001 - progress is best effort
                current = None
            if current and current != last:
                last = current
                fields = {k: v for k, v in current.items() if k != "stage"}
                set_progress(job, current.get("stage", "processing"), **fields)
    finally:
        try:
            progress.pop(job.id)
        except Exception:  # noqa: BLE001
            pass


def run_gpu(job: Job, max_clips: int, transcript: str, timings: dict) -> dict:
    """Run the deployed process_job and mirror its live progress into job.json."""
    import modal

    job.update(status="processing", message="Processing on the GPU")
    set_progress(job, "transcribing" if transcript == "parakeet" else "ranking")
    log(f"Calling {FUNCTION_NAME} (transcript={transcript})")
    start = time.time()
    function = modal.Function.from_name(APP_NAME, FUNCTION_NAME)
    args = (job.id, max_clips, gemini_context(job), transcript)
    call = function.spawn(*args)
    result = wait_for(call, job)

    timings.update({f"gpu_{k}": v for k, v in (result.get("timings") or {}).items()})
    timings["gpu_total"] = round(time.time() - start, 1)
    cost = result.get("cost") or {}
    log(f"GPU processing finished in {timings['gpu_total']:.0f}s on {result.get('gpu', '?')}"
        + (f", about ${cost['usd']:.3f}" if cost.get("usd") is not None else ""))
    return result


RATES_CACHE = pathlib.Path("stats") / "rates.json"
BILL_CACHE = pathlib.Path("stats") / "modal-bill.json"


def modal_cli_json(*args: str, timeout_s: float = 60.0):
    """Run `python -m modal <args> --json` and parse its output (None on any failure)."""
    try:
        done = subprocess.run([sys.executable, "-m", "modal", *args, "--json"], capture_output=True, text=True,
                              timeout=timeout_s, cwd=str(BACKEND_DIR), env={**os.environ, "PYTHONUTF8": "1"},
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"modal {' '.join(args)} failed: {exc}")
        return None
    if done.returncode != 0:
        log(f"modal {' '.join(args)} failed: {(done.stderr or done.stdout).strip()[-300:]}")
        return None
    text = done.stdout.strip()
    try:
        return json.loads(text[text.index("{"):]) if "{" in text else None
    except ValueError:
        return None


def live_rates(data_dir: pathlib.Path, max_age_hours: float = 24.0, fetch=None) -> dict:
    """Modal's current prices (`modal billing rates`), read at most once a day and saved in
    <data>/stats/rates.json. Falls back to the built-in table in costs.py."""
    cache = data_dir / RATES_CACHE
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        age = time.time() - float(saved.get("fetchedAt") or 0)
        if age < max_age_hours * 3600 and saved.get("rates"):
            return saved["rates"]
    except (OSError, ValueError):
        saved = None
    raw = (fetch or (lambda: modal_cli_json("billing", "rates")))()
    parsed = costs.parse_modal_rates(raw) if isinstance(raw, dict) else None
    if parsed is None:
        if raw is not None:
            log("Modal's rate names were not recognised; using the built-in prices from costs.py")
        return (saved or {}).get("rates") or costs.builtin_rates()
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"fetchedAt": time.time(), "rates": parsed, "raw": raw}), encoding="utf-8")
    except OSError:
        pass
    return parsed


def refresh_modal_bill(data_dir: pathlib.Path, fetch=None) -> dict | None:
    """This month's bill from `modal billing summary`, saved in <data>/stats/modal-bill.json
    for the stats page. Amounts are USD strings in Modal's output; stored as numbers."""
    raw = (fetch or (lambda: modal_cli_json("billing", "summary")))()
    if not isinstance(raw, dict) or "metered_cost" not in raw:
        return None

    def number(value) -> float:
        try:
            return round(float(value), 4)
        except (TypeError, ValueError):
            return 0.0

    now = dt.datetime.now(dt.timezone.utc)
    bill = {"fetchedAt": now.isoformat().replace("+00:00", "Z"), "month": now.strftime("%Y-%m"),
            "meteredUsd": number(raw.get("metered_cost")), "billedUsd": number(raw.get("billed_cost")),
            "breakdown": {k: number(v) for k, v in (raw.get("metered_cost_breakdown") or {}).items()},
            "adjustments": {k: number(v) for k, v in (raw.get("adjustments") or {}).items()}}
    try:
        (data_dir / BILL_CACHE).parent.mkdir(parents=True, exist_ok=True)
        (data_dir / BILL_CACHE).write_text(json.dumps(bill, indent=2), encoding="utf-8")
    except OSError:
        pass
    return bill


def job_cost(result: dict, data_dir: pathlib.Path | None = None) -> dict | None:
    """The estimated Modal cost of this run, as stored in job.json and the stats ledger:
    the containers' running time priced with Modal's live rates, split by stage."""
    cost = result.get("cost")
    if result.get("usages") and data_dir is not None:
        try:
            cost = costs.estimate(result["usages"], float(result.get("orchestratorSeconds") or 0),
                                  live_rates(data_dir))
        except Exception as exc:  # noqa: BLE001 - keep the container's own estimate
            log(f"Could not price the run with Modal's live rates: {exc}")
    if not cost or cost.get("usd") is None:
        return None
    return {"usd": cost["usd"], "gpuSeconds": cost.get("gpuSeconds"), "gpu": result.get("gpu"),
            "containers": cost.get("containers"), "stages": cost.get("stages"),
            "rates": cost.get("rates", "built-in")}


def download_outputs(job: Job, volume, result: dict, clips_dir: pathlib.Path | None = None) -> list[str]:
    job.update(status="saving", message="Saving clips")
    set_progress(job, "saving")
    clips_dir = clips_dir or job.dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    saved = []
    for name in result.get("clips", []):
        target = clips_dir / name
        with open(target, "wb") as f:
            volume.read_file_into_fileobj(f"/{job.id}/clips/{name}", f)
        saved.append(name)
        save_clip_poster(target)
        log(f"Saved {target}")

    for extra in ("transcript.json", "moments.json"):
        buffer = io.BytesIO()
        try:
            volume.read_file_into_fileobj(f"/{job.id}/{extra}", buffer)
        except Exception:  # noqa: BLE001 - optional files
            continue
        (job.dir / extra).write_bytes(buffer.getvalue())

    return saved


def swap_in_new_clips(job: Job, new_dir: pathlib.Path) -> None:
    """Replace <job>/clips with the re-rendered clips only once they are all downloaded."""
    clips_dir = job.dir / "clips"
    old_dir = job.dir / "clips_previous"
    shutil.rmtree(old_dir, ignore_errors=True)
    if clips_dir.exists():
        clips_dir.rename(old_dir)
    new_dir.rename(clips_dir)
    shutil.rmtree(old_dir, ignore_errors=True)


def rerank_job(job: Job, volume, max_clips: int, timings: dict, keep_video: bool = False) -> tuple[dict, list[str]]:
    """Re-rank & re-render: reuse the saved transcript, skip transcription. The video is
    downloaded again (YouTube) or taken from the job folder (an upload added again)."""
    if not (job.dir / "transcript.json").exists():
        raise FileNotFoundError("transcript.json is missing from the job folder, so this job cannot be re-ranked")
    # Jobs from the removed Whistle engine get their subtitles re-transcribed with Parakeet.
    transcriber = (job.data.get("engine") or {}).get("transcriber") or "parakeet"
    mode = "uploaded" if transcriber == "whistle" else "reuse"

    fetch_replay(job, timings)
    put_video_on_modal(job, volume, timings, keep_video)
    upload_transcript(job, volume)
    previous = job.dir / "moments.json"
    if previous.exists():
        shutil.copy(previous, job.dir / "moments.previous.json")

    result = run_gpu(job, max_clips, mode, timings)
    new_dir = job.dir / "clips_new"
    shutil.rmtree(new_dir, ignore_errors=True)
    saving = time.time()
    clips = download_outputs(job, volume, result, new_dir)
    timings["save"] = round(time.time() - saving, 1)
    if clips:
        swap_in_new_clips(job, new_dir)
    else:  # keep the old clips if the re-run produced none
        shutil.rmtree(new_dir, ignore_errors=True)
        clips = job.data.get("clips") or []
        result = {**result, "moments": None}
    return result, clips


def ranking_message(clips: list[str], errors: list[str], ranking: dict) -> str:
    """Job summary that says why clips are missing or came from the fallback."""
    if clips:
        message = f"{len(clips)} clip(s) ready, best first"
    else:
        message = "No clips were produced"
    notes = []
    if ranking.get("fallback"):
        notes.append(f"{ranking['fallback']} picked from the transcript because Gemini returned "
                     f"{ranking.get('valid', 0)} usable moment(s)")
    if ranking.get("errors"):
        notes.append(f"Gemini error: {ranking['errors'][-1][:160]}")
    if errors:
        notes.append(f"{len(errors)} clip(s) failed to render - see the log")
    if not clips and not notes:
        notes.append("the transcript was empty" if ranking.get("reason") == "empty transcript"
                     else "Gemini found no usable moments")
    return message + (" (" + "; ".join(notes) + ")" if notes else "")


def clip_details(clips: list[str], clip_moments: list[dict]) -> list[dict]:
    """clip_N.mp4 is rendered from clip_moments[N] (sorted best first)."""
    details = []
    for name in clips:
        index = int(re.search(r"\d+", name).group(0))
        moment = clip_moments[index] if index < len(clip_moments) else {}
        details.append({
            "name": name,
            "rank": moment.get("rank", index + 1),
            "score": moment.get("score"),
            "title": moment.get("title"),
            "hook": moment.get("hook"),
            "reason": moment.get("reason"),
            "start": moment.get("start"),
            "end": moment.get("end"),
            "scores": moment.get("scores"),
            "source": moment.get("source", "gemini"),
            "gemini": moment.get("gemini"),
            "audience": moment.get("audience"),
            "peak": moment.get("peak"),
        })
    return details


def cleanup_remote(job: Job, volume) -> None:
    try:
        volume.remove_file(f"/{job.id}", recursive=True)
    except Exception as exc:  # noqa: BLE001
        log(f"Could not clean up remote files: {exc}")


def prompt_version() -> str | None:
    """Short hash of the ranking prompts, so the stats can later be split by prompt."""
    digest = hashlib.sha256()
    try:
        for name in ("viral_moments.md", "viral_moments_rerank.md"):
            digest.update((BACKEND_DIR / "prompts" / name).read_bytes().replace(b"\r\n", b"\n"))
    except OSError:
        return None
    return digest.hexdigest()[:8]


def clip_sources(job: Job, details: list[dict] | None, ranking: dict | None) -> dict | None:
    """Most replayed and SponsorBlock numbers of a run, for the stats page."""
    record = job.data.get("replay")
    if not record and not (ranking or {}).get("replay"):
        return None
    details = details or []
    audience = [d["audience"] for d in details if isinstance(d.get("audience"), (int, float))]
    sponsor = (record or {}).get("sponsor") or {}
    skipped: dict[str, float] = {}
    for segment in sponsor.get("segments") or []:
        seconds = max(0.0, float(segment.get("end") or 0) - float(segment.get("start") or 0))
        skipped[segment.get("category") or "other"] = round(skipped.get(segment.get("category") or "other", 0)
                                                             + seconds, 1)
    report = (ranking or {}).get("replay") or {}
    return {"graph": bool((record or {}).get("available")), "hotspots": len((record or {}).get("peaks") or []),
            "clips": len(details), "clipsOnHotspot": sum(1 for d in details if d.get("peak")),
            "audienceAvg": round(sum(audience) / len(audience), 1) if audience else None,
            "sponsorBlock": sponsor.get("segments") is not None if record else False,
            "skippedSeconds": skipped, "droppedForSponsor": int(report.get("droppedForSponsor") or 0)}


def record_run(job: Job, *, mode: str, status: str, started: float, timings: dict,
               cost: dict | None = None, clips: list[str] | None = None, details: list[dict] | None = None,
               ranking: dict | None = None, failed_stage: str | None = None) -> list[dict] | None:
    """Record a run that just ended: returns job.json "runs" with it added, and appends it
    to the stats ledger (<data>/stats/runs.jsonl, which outlives the job folder).
    Never raises: stats are best effort."""
    data_dir = job.dir.parent.parent
    try:
        entry = run_history.runner_entry(job.data, mode=mode, status=status, started=started,
                                         timings=timings, cost=cost,
                                         clips=None if clips is None else len(clips))
        if failed_stage:
            entry["failedStage"] = failed_stage
        entry["maxClips"] = settings.MAX_CLIPS
        entry["promptVersion"] = prompt_version()
        entry["usdInr"] = fx.usd_inr(data_dir, log=log)
        sources = clip_sources(job, details, ranking) if status != "failed" else None
        if sources:
            entry["clipSources"] = sources
    except Exception as exc:  # noqa: BLE001
        log(f"Could not record the run for the stats page: {exc}")
        return job.data.get("runs")
    try:
        run_history.append_ledger(data_dir, run_history.ledger_record(job.data, entry))
    except Exception as exc:  # noqa: BLE001
        log(f"Could not add the run to the stats ledger: {exc}")
    return [*(job.data.get("runs") or []), entry]


def run_rerank(job_dir: pathlib.Path, max_clips: int, keep_remote: bool, keep_video: bool = False) -> int:
    import modal

    job = Job(job_dir)
    job.update(pid=os.getpid(), error=None, status="queued", progress=None, videoTransfer=None,
               message="Re-rank & re-render: getting the video ready")
    volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)
    timings: dict = {"rerank": True}
    job_start = time.time()
    try:
        clear_old_downloads()
        result, clips = rerank_job(job, volume, max_clips, timings, keep_video)
        timings["total"] = round(time.time() - job_start, 1)
        errors = result.get("errors") or []
        ranking = result.get("ranking") or {}
        message = "Re-ranked: " + ranking_message(clips, errors, ranking)
        if result.get("moments") is None:
            message += " - kept the previous clips"
            details = job.data.get("clipDetails") or []
        else:
            details = clip_details(clips, result.get("moments") or [])
        cost = job_cost(result, job.dir.parent.parent)
        runs = record_run(job, mode="rerank", status="done" if result.get("clips") else "no-clips",
                          started=job_start, timings=timings, cost=cost, clips=result.get("clips") or [],
                          details=details if result.get("moments") is not None else [], ranking=ranking)
        job.update(status="done", message=message, clips=clips, clipErrors=errors, timings=timings,
                   ranking=ranking, clipDetails=details, reranks=int(job.data.get("reranks") or 0) + 1,
                   rerankedAt=now_iso(), progress=None, cost=cost, runs=runs)
        log(f"{message}. Timings: {timings}")
        if not keep_video:
            forget_video(job)
        refresh_modal_bill(job.dir.parent.parent)   # for the stats page; best effort
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        timings["total"] = round(time.time() - job_start, 1)
        runs = record_run(job, mode="rerank", status="failed", started=job_start, timings=timings,
                          failed_stage=(job.data.get("progress") or {}).get("stage") or "processing")
        job.update(status="done" if job.data.get("clips") else "failed",
                   message="Re-rank failed - previous clips kept" if job.data.get("clips") else "Failed",
                   error=f"{type(exc).__name__}: {exc}", progress=None, runs=runs,
                   failedStage=(job.data.get("progress") or {}).get("stage") or "processing")
        return 1
    finally:
        if not keep_remote:
            cleanup_remote(job, volume)


def forget_video(job: Job) -> None:
    """After a successful run: an upload keeps its fingerprint (to be recognised when it is
    added again), then input.mp4 leaves the job folder."""
    if job.data.get("source") == "upload" and not job.data.get("fingerprint"):
        fingerprint = file_fingerprint(job.dir / "input.mp4")
        if fingerprint:
            job.update(fingerprint=fingerprint)
    freed = discard_local_video(job)
    if freed:
        log(f"Deleted the video from the job folder ({freed:.0f} MB)")


def run_job(job_dir: pathlib.Path, max_clips: int, keep_remote: bool, keep_video: bool = False) -> int:
    import modal

    job = Job(job_dir)
    job.update(pid=os.getpid(), error=None, progress=None, videoTransfer=None)
    volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)
    timings: dict = {}
    job_start = time.time()
    failed_stage = "starting"

    try:
        if job.data.get("source") == "upload" and not (job.dir / "thumbnail.jpg").exists():
            save_thumbnail(job)

        job.update(engine={"transcriber": "parakeet", "language": "en", "subtitles": "parakeet"})
        failed_stage = "uploading" if (job.dir / "input.mp4").exists() else "downloading"
        clear_old_downloads()
        fetch_replay(job, timings)
        put_video_on_modal(job, volume, timings, keep_video)

        failed_stage = "processing"
        result = run_gpu(job, max_clips, "parakeet", timings)
        job.update(engine={**job.data["engine"], "subtitles": result.get("subtitles", "parakeet"),
                           "gpu": result.get("gpu"), "parakeet": result.get("transcription") or None})
        failed_stage = "saving"
        saving = time.time()
        clips = download_outputs(job, volume, result)
        timings["save"] = round(time.time() - saving, 1)
        timings["total"] = round(time.time() - job_start, 1)

        errors = result.get("errors") or []
        ranking = result.get("ranking") or {}
        message = ranking_message(clips, errors, ranking)
        cost = job_cost(result, job.dir.parent.parent)
        details = clip_details(clips, result.get("moments") or [])
        runs = record_run(job, mode="full", status="done" if clips else "no-clips", started=job_start,
                          timings=timings, cost=cost, clips=clips, details=details, ranking=ranking)
        job.update(status="done", message=message, clips=clips, clipErrors=errors, timings=timings,
                   ranking=ranking, progress=None, clipDetails=details, cost=cost, runs=runs)
        log(f"{message}. Timings: {timings}")
        if not keep_video:
            forget_video(job)
        refresh_modal_bill(job.dir.parent.parent)   # for the stats page; best effort
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        timings["total"] = round(time.time() - job_start, 1)
        runs = record_run(job, mode="full", status="failed", started=job_start, timings=timings,
                          failed_stage=(job.data.get("progress") or {}).get("stage") or failed_stage)
        job.update(status="failed", message="Failed", error=f"{type(exc).__name__}: {exc}", timings=timings,
                   failedStage=(job.data.get("progress") or {}).get("stage") or failed_stage, progress=None,
                   runs=runs)
        return 1
    finally:
        if not keep_remote:
            cleanup_remote(job, volume)


def backfill_replay(data_dir: pathlib.Path, force: bool = False, pause_s: float = 3.0,
                    fetch=replay.fetch_signals, sleep=time.sleep) -> int:
    """Read the Most replayed graph and SponsorBlock segments for older YouTube videos,
    so their next re-rank uses them, and report how the current clips line up with the
    replay hotspots (clips chosen by Gemini alone, before this feature)."""
    totals = {"videos": 0, "withGraph": 0, "clips": 0, "clipsOnPeak": 0, "topPeakCovered": 0}
    for job_dir in sorted((data_dir / "jobs").glob("*")):
        if not (job_dir / "job.json").exists():
            continue
        job = Job(job_dir)
        if job.data.get("source") != "youtube" or not job.data.get("youtubeUrl"):
            continue
        if job.data.get("replay") and not force:
            record = job.data["replay"]
        else:
            if totals["videos"]:
                sleep(pause_s)                          # be gentle with YouTube
            record, _ = fetch(job.data["youtubeUrl"], cookies_browser=youtube_cookies()["browser"],
                              sponsorblock=settings.SPONSORBLOCK, fetch_info=fetch_youtube_info, log=log)
            job.data["replay"] = record
            job.save()  # keeps updatedAt: the video itself did not change
        totals["videos"] += 1
        sponsor = record.get("sponsor") or {}
        skips = len(sponsor.get("segments") or [])
        if not record.get("available"):
            reason = "; ".join(record.get("errors") or []) or "YouTube shows no graph for it"
            log(f"{job.id}: no Most replayed graph ({reason[:120]}); SponsorBlock {skips} segment(s)")
            continue
        hits = replay.hit_report(record.get("peaks") or [], job.data.get("clipDetails") or [])
        totals["withGraph"] += 1
        totals["clips"] += hits["clips"]
        totals["clipsOnPeak"] += hits["clipsOnPeak"]
        totals["topPeakCovered"] += int(hits["topPeakCovered"])
        log(f"{job.id}: {len(record.get('peaks') or [])} hotspot(s); {hits['clipsOnPeak']} of {hits['clips']} "
            f"current clip(s) on a hotspot; top hotspot {'covered' if hits['topPeakCovered'] else 'missed'}; "
            f"SponsorBlock {skips} segment(s){', a highlight' if sponsor.get('highlight') is not None else ''}")
    log(f"{totals['withGraph']} of {totals['videos']} YouTube video(s) have a Most replayed graph. "
        f"Before this feature, {totals['clipsOnPeak']} of {totals['clips']} of their clips sat on a hotspot "
        f"and {totals['topPeakCovered']} of {totals['withGraph']} covered the most replayed one. "
        "Re-rank a video to use the graph.")
    return 0


def prune_videos(data_dir: pathlib.Path, dry_run: bool = False) -> int:
    """Delete the videos older versions kept in finished job folders. Failed and running
    jobs keep theirs (Try again reuses it). Uploads keep a fingerprint and a cover first."""
    freed, count = 0.0, 0
    for job_dir in sorted((data_dir / "jobs").glob("*")):
        videos = [p for p in job_dir.glob("input.*") if p.is_file()]
        if not videos or not (job_dir / "job.json").exists():
            continue
        job = Job(job_dir)
        size = sum(p.stat().st_size for p in videos) / 1024 / 1024
        if job.data.get("status") != "done":
            log(f"{job.id}: kept ({job.data.get('status')}, {size:.0f} MB)")
            continue
        if dry_run:
            log(f"{job.id}: would delete {size:.0f} MB")
            freed, count = freed + size, count + 1
            continue
        if job.data.get("source") == "upload":
            if not job.data.get("fingerprint"):
                fingerprint = file_fingerprint(job_dir / "input.mp4")
                if fingerprint:
                    job.data["fingerprint"] = fingerprint
                    job.save()  # keeps updatedAt: the video itself did not change
            if not (job_dir / "thumbnail.jpg").exists():
                save_thumbnail(job)
        freed_here = discard_local_video(job)
        log(f"{job.id}: deleted {freed_here:.0f} MB")
        freed, count = freed + freed_here, count + 1
    verb = "Would free" if dry_run else "Freed"
    log(f"{verb} {freed / 1024:.1f} GB from {count} video(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Podcast Clipper local runner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job-dir", help="Existing job folder created by the web app")
    source.add_argument("--video", help="Path to a local .mp4 file")
    source.add_argument("--youtube", help="YouTube video URL")
    source.add_argument("--thumbnails", action="store_true",
                        help="Save missing video covers and clip posters in --data-dir, then exit")
    source.add_argument("--prune-videos", action="store_true",
                        help="Delete the input.mp4 of finished jobs in --data-dir (kept by older versions), then exit")
    source.add_argument("--modal-bill", action="store_true",
                        help="Save this month's Modal bill (modal billing summary) for the stats page, then exit")
    source.add_argument("--check-rates", action="store_true",
                        help="Print Modal's live prices as the cost estimate reads them, then exit")
    source.add_argument("--backfill-replay", action="store_true",
                        help="Read the Most replayed graph and SponsorBlock segments for older YouTube videos "
                             "in --data-dir, report how their clips line up with it, then exit")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)),
                        help="Where job folders are stored (default: ../data)")
    parser.add_argument("--max-clips", type=int, default=None,
                        help="Clips per video (default: the app's Settings, else 5)")
    parser.add_argument("--keep-remote", action="store_true",
                        help="Keep the job files in the Modal volume after downloading")
    parser.add_argument("--keep-video", action="store_true",
                        help="Keep input.mp4 in the job folder after the run (e.g. for benchmark_gpu.py)")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --prune-videos: only list what would be deleted")
    parser.add_argument("--force", action="store_true",
                        help="With --backfill-replay: also redo videos that already have it")
    parser.add_argument("--rerank", action="store_true",
                        help="With --job-dir: re-rank & re-render a finished job from its saved transcript")
    args = parser.parse_args()
    hide_child_windows()
    data_dir = (pathlib.Path(args.job_dir).resolve().parent.parent if args.job_dir
                else pathlib.Path(args.data_dir).resolve())
    settings.apply_user_settings(data_dir)
    if args.max_clips is None:
        args.max_clips = settings.MAX_CLIPS
    if args.modal_bill:
        bill = refresh_modal_bill(data_dir)
        print(json.dumps(bill, indent=2) if bill else "Could not read the Modal bill (see the message above)")
        return 0 if bill else 1
    if args.check_rates:
        rates = live_rates(data_dir, max_age_hours=0)
        print(json.dumps({"source": rates.get("source"), "A10 per hour": round(rates["gpu"]["A10"] * 3600, 4),
                          "CPU core per hour": round(rates["cpu"] * 3600, 4),
                          "memory GiB per hour": round(rates["memory"] * 3600, 4)}, indent=2))
        return 0
    if args.thumbnails:
        return backfill_thumbnails(pathlib.Path(args.data_dir).resolve())
    if args.prune_videos:
        return prune_videos(pathlib.Path(args.data_dir).resolve(), args.dry_run)
    if args.backfill_replay:
        return backfill_replay(pathlib.Path(args.data_dir).resolve(), args.force)
    if args.rerank:
        if not args.job_dir:
            parser.error("--rerank needs --job-dir")
        return run_rerank(pathlib.Path(args.job_dir).resolve(), args.max_clips, args.keep_remote, args.keep_video)

    if args.job_dir:
        job_dir = pathlib.Path(args.job_dir).resolve()
    else:
        data_dir = pathlib.Path(args.data_dir).resolve()
        if args.video:
            video = pathlib.Path(args.video).resolve()
            if not video.exists():
                parser.error(f"{video} does not exist")
            job = Job.create(data_dir, "upload", video.name)
            log(f"Copying {video} into {job.dir}")
            shutil.copy(video, job.dir / "input.mp4")
            job.update(fingerprint=file_fingerprint(job.dir / "input.mp4"))
        else:
            job = Job.create(data_dir, "youtube", args.youtube, youtube_url=args.youtube)
        job_dir = job.dir
        log(f"Created job {job.id} in {job_dir}")

    return run_job(job_dir, args.max_clips, args.keep_remote, args.keep_video)


if __name__ == "__main__":
    sys.exit(main())
