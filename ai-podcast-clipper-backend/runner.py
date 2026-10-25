"""
Local job runner for AI Podcast Clipper.

Runs on your laptop. The web app starts it for every job, but you can also use
it directly from the terminal:

    python runner.py --video "C:\\path\\to\\podcast.mp4"
    python runner.py --youtube "https://www.youtube.com/watch?v=..."
    python runner.py --job-dir "..\\data\\jobs\\<job id>"     (what the web app calls)
    python runner.py --job-dir "..\\data\\jobs\\<job id>" --rerank   (re-rank & re-render)
    python runner.py --thumbnails                              (covers and clip posters for older videos)
    python runner.py --backfill-stats                          (run history of older videos, for the stats page)
    python runner.py --prune-videos [--dry-run]                (delete videos kept by older versions)

Steps: video into the Modal jobs volume -> run the deployed pipeline -> download clips
into <job dir>/clips -> update <job dir>/job.json -> delete the video everywhere.

YouTube videos download straight into the volume (download_video on Modal), so they
never touch the laptop. If YouTube blocks Modal, the laptop downloads and uploads the
video instead (YOUTUBE_DOWNLOAD=laptop in .env always does). An uploaded MP4 is sent
from the job folder. Once the clips are saved, input.mp4 is deleted from the job folder
and the volume: only job.json, the transcript, the moments, the clips and the
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
import time
import traceback

import downloader
import run_history
import settings  # loads ai-podcast-clipper-backend/.env

APP_NAME = "ai-podcast-clipper"
FUNCTION_NAME = "process_job"
DOWNLOAD_FUNCTION = "download_video"
JOBS_VOLUME = "ai-podcast-clipper-jobs"

PROGRESS_DICT = "ai-podcast-clipper-progress"   # live stage/clip counts written by the GPU
POLL_SECONDS = 3
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


def download_youtube(job: Job, attempts: int = 3) -> None:
    """Laptop download into <job dir>/input.mp4 (the fallback when Modal cannot download)."""
    import imageio_ffmpeg

    url = job.data["youtubeUrl"]
    job.update(status="downloading", message="Downloading from YouTube")
    log(f"Downloading {url}")
    # Optional: use your browser's YouTube login when YouTube asks to
    # "confirm you're not a bot". Set YTDLP_COOKIES_FROM_BROWSER=firefox (or edge/chrome).
    browser = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip() or None
    if browser:
        log(f"Using cookies from {browser}")
    last = [0.0]

    def progress(fraction: float) -> None:
        if time.time() - last[0] >= 1.5:
            last[0] = time.time()
            set_progress(job, "downloading", where="laptop", fraction=round(fraction, 3))

    def retry(attempt: int, total: int, exc: Exception) -> None:
        log(f"Download attempt {attempt} failed ({exc}); retrying in {10 * attempt}s")
        job.update(message=f"Downloading from YouTube (retry {attempt}/{total - 1})")

    info = downloader.download(url, job.dir, attempts=attempts, ffmpeg_location=imageio_ffmpeg.get_ffmpeg_exe(),
                               cookies_browser=browser, on_progress=progress, on_retry=retry)
    apply_meta(job, downloader.video_meta(info))
    log(f"Downloaded: {job.data['title']} (YouTube language: {info.get('language') or 'unknown'})")


def cloud_download(job: Job) -> dict:
    """Download the YouTube video straight into the Modal volume (main.download_video).
    Never raises: {"ok": False, "error": ...} means the laptop should download it."""
    import modal

    try:
        call = modal.Function.from_name(APP_NAME, DOWNLOAD_FUNCTION).spawn(job.id, job.data["youtubeUrl"])
        return wait_for(call, job)
    except Exception as exc:  # noqa: BLE001 - e.g. deployed before download_video existed
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def put_video_on_modal(job: Job, volume, timings: dict, usages: list) -> None:
    """Get the video into /<job id>/input.mp4 on the Modal volume.

    A video in the job folder (an upload, or an older job) is uploaded. A YouTube
    video is downloaded on Modal; if that fails (YouTube blocks some cloud IPs), the
    laptop downloads it and uploads it, as before."""
    local = job.dir / "input.mp4"
    transfer = "upload"
    if not local.exists() and job.data.get("source") == "youtube":
        if settings.YOUTUBE_DOWNLOAD == "cloud":
            job.update(status="downloading", message="Downloading from YouTube to Modal",
                       videoTransfer="cloud-download")
            set_progress(job, "downloading", where="cloud")
            log(f"Downloading {job.data['youtubeUrl']} on Modal")
            start = time.time()
            result = cloud_download(job)
            timings["cloud_download"] = round(time.time() - start, 1)
            if result.get("usage"):
                usages.append(result["usage"])
            if result.get("ok"):
                apply_meta(job, result.get("meta") or {})
                log(f"Downloaded on Modal in {timings['cloud_download']:.0f}s ({result.get('sizeMB', '?')} MB): "
                    f"{job.data['title']}")
                return
            log(f"Modal could not download the video ({result.get('error') or 'unknown error'}); "
                "downloading on this laptop instead")
            job.update(downloadFallback="blocked" if result.get("blocked") else "failed")
        transfer = "laptop-download"
        job.update(videoTransfer=transfer)
        set_progress(job, "downloading", where="laptop")
        start = time.time()
        download_youtube(job)
        timings["download"] = round(time.time() - start, 1)

    if not local.exists():
        raise FileNotFoundError("The uploaded video is no longer in the job folder: add the same MP4 again "
                                "to re-rank it")
    if transfer == "upload":
        job.update(videoTransfer=transfer)
    set_progress(job, "uploading")
    start = time.time()
    upload_input(job, volume)
    timings["upload"] = round(time.time() - start, 1)


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


def upload_input(job: Job, volume, update_status: bool = True) -> None:
    size_mb = (job.dir / "input.mp4").stat().st_size / 1024 / 1024
    if update_status:
        job.update(status="uploading", message=f"Uploading video to Modal ({size_mb:.0f} MB)")
    log(f"Uploading input.mp4 ({size_mb:.0f} MB) to volume {JOBS_VOLUME}")
    with volume.batch_upload(force=True) as batch:
        batch.put_file(str(job.dir / "input.mp4"), f"/{job.id}/input.mp4")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def gemini_context(job: Job) -> dict:
    meta = job.data.get("meta") or {}
    return {
        "title": meta.get("title") or job.data.get("title"),
        "channel": meta.get("channel"),
        "description": meta.get("description"),
    }


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
    try:
        while True:
            try:
                return call.get(timeout=POLL_SECONDS)
            except (TimeoutError, modal.exception.TimeoutError):
                pass
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


def run_gpu(job: Job, max_clips: int, transcript: str, timings: dict, extra_usages: list | None = None) -> dict:
    """Run the deployed process_job and mirror its live progress into job.json.
    extra_usages: the download container, so the job's cost includes it."""
    import modal

    job.update(status="processing", message="Processing on the GPU")
    set_progress(job, "transcribing" if transcript == "parakeet" else "ranking")
    log(f"Calling {FUNCTION_NAME} (transcript={transcript})")
    start = time.time()
    function = modal.Function.from_name(APP_NAME, FUNCTION_NAME)
    args = (job.id, max_clips, gemini_context(job), transcript)
    call = function.spawn(*args, extra_usages=extra_usages) if extra_usages else function.spawn(*args)
    result = wait_for(call, job)

    timings.update({f"gpu_{k}": v for k, v in (result.get("timings") or {}).items()})
    timings["gpu_total"] = round(time.time() - start, 1)
    cost = result.get("cost") or {}
    log(f"GPU processing finished in {timings['gpu_total']:.0f}s on {result.get('gpu', '?')}"
        + (f", about ${cost['usd']:.3f}" if cost.get("usd") is not None else ""))
    return result


def job_cost(result: dict) -> dict | None:
    """The estimated Modal cost of this run, as stored in job.json."""
    cost = result.get("cost")
    if not cost or cost.get("usd") is None:
        return None
    return {"usd": cost["usd"], "gpuSeconds": cost.get("gpuSeconds"), "gpu": result.get("gpu"),
            "containers": cost.get("containers")}


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


def rerank_job(job: Job, volume, max_clips: int, timings: dict) -> tuple[dict, list[str]]:
    """Re-rank & re-render: reuse the saved transcript, skip transcription. The video is
    downloaded again (YouTube) or taken from the job folder (an upload added again)."""
    if not (job.dir / "transcript.json").exists():
        raise FileNotFoundError("transcript.json is missing from the job folder, so this job cannot be re-ranked")
    # Jobs from the removed Whistle engine get their subtitles re-transcribed with Parakeet.
    transcriber = (job.data.get("engine") or {}).get("transcriber") or "parakeet"
    mode = "uploaded" if transcriber == "whistle" else "reuse"

    usages: list = []
    put_video_on_modal(job, volume, timings, usages)
    upload_transcript(job, volume)
    previous = job.dir / "moments.json"
    if previous.exists():
        shutil.copy(previous, job.dir / "moments.previous.json")

    result = run_gpu(job, max_clips, mode, timings, usages)
    new_dir = job.dir / "clips_new"
    shutil.rmtree(new_dir, ignore_errors=True)
    clips = download_outputs(job, volume, result, new_dir)
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
        })
    return details


def cleanup_remote(job: Job, volume) -> None:
    try:
        volume.remove_file(f"/{job.id}", recursive=True)
    except Exception as exc:  # noqa: BLE001
        log(f"Could not clean up remote files: {exc}")


def run_entries(job: Job, *, mode: str, status: str, started: float, timings: dict,
                cost: dict | None = None, clips: list[str] | None = None) -> list[dict] | None:
    """job.json "runs" with this run added (the stats page reads it). Never raises."""
    try:
        log_file = job.dir / "runner.log"
        log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
        runs = run_history.history_before(job.data, log_text, started)
        runs.append(run_history.runner_entry(job.data, mode=mode, status=status, started=started,
                                             timings=timings, cost=cost,
                                             clips=None if clips is None else len(clips)))
        return runs
    except Exception as exc:  # noqa: BLE001 - stats are best effort
        log(f"Could not record the run for the stats page: {exc}")
        return job.data.get("runs")


def backfill_stats(data_dir: pathlib.Path, force: bool = False) -> int:
    """Rebuild "runs" from runner.log for videos processed before the run history existed."""
    filled = 0
    for job_dir in sorted((data_dir / "jobs").glob("*")):
        log_file = job_dir / "runner.log"
        if not (job_dir / "job.json").exists() or not log_file.exists():
            continue
        job = Job(job_dir)
        if isinstance(job.data.get("runs"), list) and not force:
            continue
        runs = run_history.parse_log(log_file.read_text(encoding="utf-8", errors="replace"), job.data)
        job.data["runs"] = runs
        job.save()  # keeps updatedAt: the video itself did not change
        filled += 1
        log(f"{job.id}: {len(runs)} run(s)")
    log(f"Rebuilt the run history of {filled} video(s)")
    return 0


def run_rerank(job_dir: pathlib.Path, max_clips: int, keep_remote: bool, keep_video: bool = False) -> int:
    import modal

    job = Job(job_dir)
    job.update(pid=os.getpid(), error=None, status="queued", progress=None, videoTransfer=None,
               downloadFallback=None, message="Re-rank & re-render: getting the video ready")
    volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)
    timings: dict = {"rerank": True}
    job_start = time.time()
    try:
        result, clips = rerank_job(job, volume, max_clips, timings)
        timings["total"] = round(time.time() - job_start, 1)
        errors = result.get("errors") or []
        ranking = result.get("ranking") or {}
        message = "Re-ranked: " + ranking_message(clips, errors, ranking)
        if result.get("moments") is None:
            message += " - kept the previous clips"
            details = job.data.get("clipDetails") or []
        else:
            details = clip_details(clips, result.get("moments") or [])
        cost = job_cost(result)
        runs = run_entries(job, mode="rerank", status="done" if result.get("clips") else "no-clips",
                           started=job_start, timings=timings, cost=cost, clips=result.get("clips") or [])
        job.update(status="done", message=message, clips=clips, clipErrors=errors, timings=timings,
                   ranking=ranking, clipDetails=details, reranks=int(job.data.get("reranks") or 0) + 1,
                   rerankedAt=now_iso(), progress=None, cost=cost, runs=runs)
        log(f"{message}. Timings: {timings}")
        if not keep_video:
            forget_video(job)
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        timings["total"] = round(time.time() - job_start, 1)
        runs = run_entries(job, mode="rerank", status="failed", started=job_start, timings=timings)
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
    job.update(pid=os.getpid(), error=None, progress=None, videoTransfer=None, downloadFallback=None)
    volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)
    timings: dict = {}
    usages: list = []
    job_start = time.time()
    failed_stage = "starting"

    try:
        if job.data.get("source") == "upload" and not (job.dir / "thumbnail.jpg").exists():
            save_thumbnail(job)

        job.update(engine={"transcriber": "parakeet", "language": "en", "subtitles": "parakeet"})
        failed_stage = "uploading" if (job.dir / "input.mp4").exists() else "downloading"
        put_video_on_modal(job, volume, timings, usages)

        failed_stage = "processing"
        result = run_gpu(job, max_clips, "parakeet", timings, usages)
        job.update(engine={**job.data["engine"], "subtitles": result.get("subtitles", "parakeet"),
                           "gpu": result.get("gpu"), "parakeet": result.get("transcription") or None})
        failed_stage = "saving"
        clips = download_outputs(job, volume, result)
        timings["total"] = round(time.time() - job_start, 1)

        errors = result.get("errors") or []
        ranking = result.get("ranking") or {}
        message = ranking_message(clips, errors, ranking)
        cost = job_cost(result)
        runs = run_entries(job, mode="full", status="done" if clips else "no-clips", started=job_start,
                           timings=timings, cost=cost, clips=clips)
        job.update(status="done", message=message, clips=clips, clipErrors=errors, timings=timings,
                   ranking=ranking, progress=None, clipDetails=clip_details(clips, result.get("moments") or []),
                   cost=cost, runs=runs)
        log(f"{message}. Timings: {timings}")
        if not keep_video:
            forget_video(job)
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        timings["total"] = round(time.time() - job_start, 1)
        runs = run_entries(job, mode="full", status="failed", started=job_start, timings=timings)
        job.update(status="failed", message="Failed", error=f"{type(exc).__name__}: {exc}", timings=timings,
                   failedStage=(job.data.get("progress") or {}).get("stage") or failed_stage, progress=None,
                   runs=runs)
        return 1
    finally:
        if not keep_remote:
            cleanup_remote(job, volume)


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
    source.add_argument("--backfill-stats", action="store_true",
                        help="Rebuild the run history of older videos in --data-dir from their runner.log, then exit")
    source.add_argument("--prune-videos", action="store_true",
                        help="Delete the input.mp4 of finished jobs in --data-dir (kept by older versions), then exit")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)),
                        help="Where job folders are stored (default: ../data)")
    parser.add_argument("--max-clips", type=int, default=5)
    parser.add_argument("--keep-remote", action="store_true",
                        help="Keep the job files in the Modal volume after downloading")
    parser.add_argument("--keep-video", action="store_true",
                        help="Keep input.mp4 in the job folder after the run (e.g. for benchmark_gpu.py)")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --prune-videos: only list what would be deleted")
    parser.add_argument("--force", action="store_true",
                        help="With --backfill-stats: also rebuild videos that already have a run history")
    parser.add_argument("--rerank", action="store_true",
                        help="With --job-dir: re-rank & re-render a finished job from its saved transcript")
    args = parser.parse_args()
    hide_child_windows()
    if args.thumbnails:
        return backfill_thumbnails(pathlib.Path(args.data_dir).resolve())
    if args.backfill_stats:
        return backfill_stats(pathlib.Path(args.data_dir).resolve(), args.force)
    if args.prune_videos:
        return prune_videos(pathlib.Path(args.data_dir).resolve(), args.dry_run)
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
