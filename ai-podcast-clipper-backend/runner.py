"""
Local job runner for AI Podcast Clipper.

Runs on your laptop. The web app starts it for every job, but you can also use
it directly from the terminal:

    python runner.py --video "C:\\path\\to\\podcast.mp4"
    python runner.py --youtube "https://www.youtube.com/watch?v=..."
    python runner.py --job-dir "..\\data\\jobs\\<job id>"     (what the web app calls)

Steps: (YouTube download) -> upload to the Modal jobs volume -> run the deployed
GPU class -> download clips into <job dir>/clips -> update <job dir>/job.json.
Requires `modal deploy main.py` to have been run once.
"""

import argparse
import datetime as dt
import io
import json
import os
import pathlib
import secrets
import shutil
import sys
import time
import traceback

APP_NAME = "ai-podcast-clipper"
CLASS_NAME = "AiPodcastClipper"
JOBS_VOLUME = "ai-podcast-clipper-jobs"

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

    job.update(title=(info or {}).get("title") or job.data["title"])
    log(f"Downloaded: {job.data['title']}")


def upload_input(job: Job, volume) -> None:
    size_mb = (job.dir / "input.mp4").stat().st_size / 1024 / 1024
    job.update(status="uploading", message=f"Uploading video to Modal ({size_mb:.0f} MB)")
    log(f"Uploading input.mp4 ({size_mb:.0f} MB) to volume {JOBS_VOLUME}")
    with volume.batch_upload(force=True) as batch:
        batch.put_file(str(job.dir / "input.mp4"), f"/{job.id}/input.mp4")


def run_on_gpu(job: Job, max_clips: int) -> dict:
    import modal

    job.update(status="processing",
               message="Transcribing, finding moments and rendering clips on the GPU (check modal.com for live logs)")
    log("Calling the deployed GPU class (this can take several minutes)")
    clipper_cls = modal.Cls.from_name(APP_NAME, CLASS_NAME)
    start = time.time()
    result = clipper_cls().process_job.remote(job.id, max_clips)
    log(f"GPU processing finished in {time.time() - start:.0f}s: {result}")
    return result


def download_outputs(job: Job, volume, result: dict) -> list[str]:
    job.update(status="saving", message="Downloading clips")
    clips_dir = job.dir / "clips"
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


def cleanup_remote(job: Job, volume) -> None:
    try:
        volume.remove_file(f"/{job.id}", recursive=True)
    except Exception as exc:  # noqa: BLE001
        log(f"Could not clean up remote files: {exc}")


def run_job(job_dir: pathlib.Path, max_clips: int, keep_remote: bool) -> int:
    import modal

    job = Job(job_dir)
    job.update(pid=os.getpid(), error=None)
    volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)

    try:
        if job.data.get("source") == "youtube":
            download_youtube(job)
        upload_input(job, volume)
        result = run_on_gpu(job, max_clips)
        clips = download_outputs(job, volume, result)

        errors = result.get("errors") or []
        if clips:
            message = f"{len(clips)} clip(s) ready"
            if errors:
                message += f" ({len(errors)} failed - see the log)"
        else:
            message = "No clips were produced"
            if errors:
                message += f" ({len(errors)} failed - see the log)"
            elif not result.get("moments"):
                message += " - Gemini found no suitable moments"
        job.update(status="done", message=message, clips=clips, clipErrors=errors)
        log(message)
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        job.update(status="failed", message="Failed", error=f"{type(exc).__name__}: {exc}")
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
    args = parser.parse_args()

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
