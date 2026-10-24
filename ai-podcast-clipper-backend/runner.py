"""
Local job runner for AI Podcast Clipper.

Runs on your laptop. The web app starts it for every job, but you can also use
it directly from the terminal:

    python runner.py --video "C:\\path\\to\\podcast.mp4"
    python runner.py --youtube "https://www.youtube.com/watch?v=..."
    python runner.py --job-dir "..\\data\\jobs\\<job id>"     (what the web app calls)
    python runner.py --job-dir "..\\data\\jobs\\<job id>" --rerank   (re-rank & re-render)

Steps: (YouTube download) -> upload to the Modal jobs volume -> run the deployed
pipeline -> download clips into <job dir>/clips -> update <job dir>/job.json.

One GPU container (A10) transcribes the video with Parakeet TDT 0.6B v2
(English), ranks the best moments with Gemini and renders the clips. Its live
progress (stage, clips done) is mirrored into job.json for the web app.

Requires `modal deploy main.py` to have been run once.
"""

import argparse
import datetime as dt
import io
import json
import os
import pathlib
import re
import secrets
import shutil
import sys
import time
import traceback

import settings  # noqa: F401 - loads ai-podcast-clipper-backend/.env

APP_NAME = "ai-podcast-clipper"
CLASS_NAME = "AiPodcastClipper"
JOBS_VOLUME = "ai-podcast-clipper-jobs"

PROGRESS_DICT = "ai-podcast-clipper-progress"   # live stage/clip counts written by the GPU
POLL_SECONDS = 3

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


def download_youtube(job: Job, attempts: int = 3) -> None:
    import imageio_ffmpeg
    import yt_dlp

    url = job.data["youtubeUrl"]
    job.update(status="downloading", message="Downloading from YouTube")
    log(f"Downloading {url}")

    options = {
        # Prefer H.264 (avc1) video up to 1080p - the clipping pipeline (OpenCV, LR-ASD)
        # handles it best - plus M4A audio, merged into input.mp4.
        "format": ("bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/"
                   "bv*[ext=mp4][height<=1080]+ba[ext=m4a]/"
                   "b[ext=mp4][height<=1080]/bv*[height<=1080]+ba/b"),
        "merge_output_format": "mp4",
        "outtmpl": str(job.dir / "input.%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        # YouTube needs a JavaScript runtime; Node is already installed for the web app.
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
    # Optional: use your browser's YouTube login when YouTube asks to
    # "confirm you're not a bot". Set YTDLP_COOKIES_FROM_BROWSER=firefox (or edge/chrome).
    browser = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if browser:
        options["cookiesfrombrowser"] = (browser,)
        log(f"Using cookies from {browser}")

    info = None
    for attempt in range(1, attempts + 1):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            break
        except yt_dlp.utils.DownloadError as exc:
            if attempt == attempts:
                raise
            # Stream URLs are signed per request - start over with fresh ones.
            log(f"Download attempt {attempt} failed ({exc}); retrying in {10 * attempt}s")
            for partial in job.dir.glob("input.*"):
                partial.unlink(missing_ok=True)
            job.update(message=f"Downloading from YouTube (retry {attempt}/{attempts - 1})")
            time.sleep(10 * attempt)

    if not (job.dir / "input.mp4").exists():
        raise RuntimeError("YouTube download finished but input.mp4 was not created")

    info = info or {}
    job.update(title=info.get("title") or job.data["title"], meta={
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "description": (info.get("description") or "")[:2000],
        "tags": (info.get("tags") or [])[:30],
        "language": info.get("language"),
        "duration": info.get("duration"),
    })
    log(f"Downloaded: {job.data['title']} (YouTube language: {info.get('language') or 'unknown'})")


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

def run_gpu(job: Job, max_clips: int, transcript: str, timings: dict) -> dict:
    """Run AiPodcastClipper.process_job and mirror its live progress into job.json."""
    import modal

    job.update(status="processing", message="Processing on the GPU")
    set_progress(job, "transcribing" if transcript == "parakeet" else "ranking")
    log(f"Calling {CLASS_NAME}.process_job (transcript={transcript})")
    start = time.time()
    call = modal.Cls.from_name(APP_NAME, CLASS_NAME)().process_job.spawn(
        job.id, max_clips, gemini_context(job), transcript)
    progress = modal.Dict.from_name(PROGRESS_DICT, create_if_missing=True)

    last = None
    while True:
        try:
            result = call.get(timeout=POLL_SECONDS)
            break
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
    try:
        progress.pop(job.id)
    except Exception:  # noqa: BLE001
        pass

    timings.update({f"gpu_{k}": v for k, v in (result.get("timings") or {}).items()})
    timings["gpu_total"] = round(time.time() - start, 1)
    log(f"GPU processing finished in {timings['gpu_total']:.0f}s on {result.get('gpu', '?')}")
    return result


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
    """Re-rank & re-render: reuse the saved video and transcript, skip download and transcription."""
    for name in ("input.mp4", "transcript.json"):
        if not (job.dir / name).exists():
            raise FileNotFoundError(f"{name} is missing from the job folder, so this job cannot be re-ranked")
    # Jobs from the removed Whistle engine get their subtitles re-transcribed with Parakeet.
    transcriber = (job.data.get("engine") or {}).get("transcriber") or "parakeet"
    mode = "uploaded" if transcriber == "whistle" else "reuse"

    start = time.time()
    set_progress(job, "uploading")
    upload_input(job, volume)
    upload_transcript(job, volume)
    timings["upload"] = round(time.time() - start, 1)
    previous = job.dir / "moments.json"
    if previous.exists():
        shutil.copy(previous, job.dir / "moments.previous.json")

    result = run_gpu(job, max_clips, mode, timings)
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


def run_rerank(job_dir: pathlib.Path, max_clips: int, keep_remote: bool) -> int:
    import modal

    job = Job(job_dir)
    job.update(pid=os.getpid(), error=None, status="uploading", progress=None,
               message="Re-rank & re-render: uploading the saved video and transcript")
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
        job.update(status="done", message=message, clips=clips, clipErrors=errors, timings=timings,
                   ranking=ranking, clipDetails=details, reranks=int(job.data.get("reranks") or 0) + 1,
                   rerankedAt=now_iso(), progress=None)
        log(f"{message}. Timings: {timings}")
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        job.update(status="done" if job.data.get("clips") else "failed",
                   message="Re-rank failed - previous clips kept" if job.data.get("clips") else "Failed",
                   error=f"{type(exc).__name__}: {exc}", progress=None,
                   failedStage=(job.data.get("progress") or {}).get("stage") or "processing")
        return 1
    finally:
        if not keep_remote:
            cleanup_remote(job, volume)


def run_job(job_dir: pathlib.Path, max_clips: int, keep_remote: bool) -> int:
    import modal

    job = Job(job_dir)
    job.update(pid=os.getpid(), error=None, progress=None)
    volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)
    timings: dict = {}
    job_start = time.time()
    failed_stage = "starting"

    try:
        if job.data.get("source") == "youtube" and not (job.dir / "input.mp4").exists():
            failed_stage = "downloading"
            set_progress(job, "downloading")
            start = time.time()
            download_youtube(job)
            timings["download"] = round(time.time() - start, 1)

        job.update(engine={"transcriber": "parakeet", "language": "en", "subtitles": "parakeet"})
        failed_stage = "uploading"
        set_progress(job, "uploading")
        start = time.time()
        upload_input(job, volume)
        timings["upload"] = round(time.time() - start, 1)

        failed_stage = "processing"
        result = run_gpu(job, max_clips, "parakeet", timings)
        job.update(engine={**job.data["engine"], "subtitles": result.get("subtitles", "parakeet"),
                           "gpu": result.get("gpu"), "parakeet": result.get("transcription") or None})
        failed_stage = "saving"
        clips = download_outputs(job, volume, result)
        timings["total"] = round(time.time() - job_start, 1)

        errors = result.get("errors") or []
        ranking = result.get("ranking") or {}
        message = ranking_message(clips, errors, ranking)
        job.update(status="done", message=message, clips=clips, clipErrors=errors, timings=timings,
                   ranking=ranking, progress=None, clipDetails=clip_details(clips, result.get("moments") or []))
        log(f"{message}. Timings: {timings}")
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        timings["total"] = round(time.time() - job_start, 1)
        job.update(status="failed", message="Failed", error=f"{type(exc).__name__}: {exc}", timings=timings,
                   failedStage=(job.data.get("progress") or {}).get("stage") or failed_stage, progress=None)
        return 1
    finally:
        if not keep_remote:
            cleanup_remote(job, volume)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Podcast Clipper local runner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job-dir", help="Existing job folder created by the web app")
    source.add_argument("--video", help="Path to a local .mp4 file")
    source.add_argument("--youtube", help="YouTube video URL")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)),
                        help="Where job folders are stored (default: ../data)")
    parser.add_argument("--max-clips", type=int, default=5)
    parser.add_argument("--keep-remote", action="store_true",
                        help="Keep the job files in the Modal volume after downloading")
    parser.add_argument("--rerank", action="store_true",
                        help="With --job-dir: re-rank & re-render a finished job from its saved transcript")
    args = parser.parse_args()
    if args.rerank:
        if not args.job_dir:
            parser.error("--rerank needs --job-dir")
        return run_rerank(pathlib.Path(args.job_dir).resolve(), args.max_clips, args.keep_remote)

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
        else:
            job = Job.create(data_dir, "youtube", args.youtube, youtube_url=args.youtube)
        job_dir = job.dir
        log(f"Created job {job.id} in {job_dir}")

    return run_job(job_dir, args.max_clips, args.keep_remote)


if __name__ == "__main__":
    sys.exit(main())
