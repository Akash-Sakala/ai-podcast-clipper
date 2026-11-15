"""
Getting a job through Modal in two phases (pipeline.py runs this between the replay
lookup and saving the clips):

  phase 1   the audio goes up first (YouTube: the audio stream alone; an upload: copied out
            of the MP4 on this laptop) and Modal transcribes and ranks it. Meanwhile the
            laptop downloads the YouTube video. A re-rank uploads the saved transcript
            instead (rank only).
  phase 2   for each chosen moment the laptop cuts the clip's footage out of the local video
            (stream copy, checked; cutter.py) and uploads only that; Modal renders it
            exactly as it would have rendered from the whole video (segments.py).

The whole video is uploaded as before (phase 1 then reads it) when the audio can't be had on
its own, and for phase 2 alone when the video can't be cut into footage (cutter.Unsupported:
only MP4/MOV with H.264 and one AAC track are cut). Legacy Whistle re-ranks always use it.

Local files live in <temp>/ai-podcast-clipper/<job id> (youtube.download_folder): the YouTube
video (kept until the run succeeds, so Try again needs no new download), audio/ and footage/
(removed after each run). Timings: "audio" (getting and uploading it), "download" (the
video, in parallel with phase 1), "downloadWait" (how long the run then waited for it),
"cut", "clipUpload"; "download" and "upload" as before on the whole-video path.
"""

import shutil
import subprocess
import threading
import time

import costs
import cutter
import segments
from clipper_names import RENDER_FUNCTION, TRANSCRIBE_FUNCTION
from cloud import BackgroundCall, finished, lookup_functions, spawn, wait_for
from jobstore import Job, log, set_progress
from transfer import download_file, put_video_on_modal, upload_files, upload_transcript
from youtube import download_folder, download_youtube, download_youtube_audio

AUDIO_FIRST = "audio-first"      # job.json "videoTransfer" of a two-phase run

MISSING_UPLOAD = "The uploaded video is no longer in the job folder: add the same MP4 again to re-rank it"


class StopVideoDownload(Exception):
    """Raised from the download's progress callback: phase 1 failed or found nothing to render."""


# --------------------------------------------------------------------------- #
# Progress while the cloud and the laptop work at the same time
# --------------------------------------------------------------------------- #

class Board:
    """job.json "progress" during phase 1: the cloud's stage, with the share of the video
    downloaded so far as "download"; "downloading" once only the download is left."""

    def __init__(self, job: Job, first_stage: str, downloading: bool):
        self.job = job
        self.cloud: dict = {"stage": first_stage}
        self.cloud_done = False
        self.downloading = downloading
        self.fraction: float | None = None
        self.lock = threading.Lock()

    def on_cloud(self, current: dict) -> None:
        with self.lock:
            self.cloud = dict(current)
            self.show()

    def on_download(self, fraction: float) -> None:
        with self.lock:
            self.fraction = fraction
            self.show()

    def end_download(self) -> None:
        with self.lock:
            self.downloading = False
            self.show()

    def end_cloud(self) -> None:
        with self.lock:
            self.cloud_done = True
            self.show()

    def show(self) -> None:
        if self.downloading and self.cloud_done:
            set_progress(self.job, "downloading", fraction=self.fraction)
            return
        if self.cloud_done:
            return
        fields = {k: v for k, v in self.cloud.items() if k not in ("stage", "ready")}
        if self.downloading:
            fields["download"] = self.fraction or 0.0
        set_progress(self.job, self.cloud.get("stage", "transcribing"), **fields)


# --------------------------------------------------------------------------- #
# Phase 1
# --------------------------------------------------------------------------- #

def local_upload(job: Job):
    """An upload's video in the job folder (FileNotFoundError when the file must be added again)."""
    video = job.dir / "input.mp4"
    if not video.exists():
        raise FileNotFoundError(MISSING_UPLOAD)
    return video


def audio_on_modal(job: Job, volume, timings: dict) -> tuple[str, str | None] | None:
    """Put the video's audio on the volume: (its name there, the YouTube audio format id),
    or None (with the reason in runner.log) when it can't be had on its own."""
    started = time.time()
    folder = download_folder(job) / "audio"
    job.update(status="uploading", message="Sending the audio to Modal", videoTransfer=AUDIO_FIRST)
    set_progress(job, "audio")
    upload = None if job.data.get("source") == "youtube" else local_upload(job)   # a missing video raises here
    try:
        if upload is None:
            source, format_id = download_youtube_audio(job)
        else:
            source, format_id = upload, None
        audio = cutter.extract_audio(source, folder)
    except Exception as exc:  # noqa: BLE001 - any failure: the whole video, as before
        log(f"Could not get the audio on its own ({type(exc).__name__}: {exc}); uploading the whole video instead")
        return None
    upload_files(job, volume, [(audio, f"/{job.id}/{audio.name}")], "audio", "the audio")
    timings["audio"] = round(time.time() - started, 1)
    return audio.name, format_id


def save_transcript(job: Job, volume) -> bool:
    """Phase 1 transcribed: keep transcript.json in the job folder right away, so Try again
    and Re-rank reuse it even if a later step fails (no second transcription). Also after a
    failed phase 1 whose transcription had finished. Never raises."""
    try:
        download_file(volume, f"/{job.id}/transcript.json", job.dir / "transcript.json")
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:  # noqa: BLE001 - only a later Try again would be slower
        log(f"Could not keep the transcript in the job folder: {exc}")
        return False


def phase_one_done(job: Job, volume, first: dict, mode: str, timings: dict, started: float) -> None:
    if mode == "parakeet":
        save_transcript(job, volume)
        engine = job.data.get("engine") or {}
        job.update(engine={**engine, "parakeet": first.get("transcription") or None})
    timings.update({f"gpu_{k}": v for k, v in (first.get("timings") or {}).items()})
    timings["gpu_total"] = round(time.time() - started, 1)
    log(f"Phase 1 done: {len(first.get('moments') or [])} moment(s) in {timings['gpu_total']:.0f}s")


# --------------------------------------------------------------------------- #
# Phase 2
# --------------------------------------------------------------------------- #

def footage_on_modal(job: Job, volume, video, moments: list, timings: dict) -> list | None:
    """Cut and upload each clip's footage; returns [{"index", "targetUs"}] for render_clips,
    or None after uploading the whole video instead (a video cutter.py does not handle)."""
    folder = download_folder(job) / "footage"
    set_progress(job, "clips")
    job.update(status="uploading", message="Preparing the clips")
    started = time.time()
    try:
        try:
            pieces = cutter.make_footage(video, moments, folder)
        except (cutter.Unsupported, OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
            timings["cut"] = round(time.time() - started, 1)
            log(f"Uploading the whole video instead of clip footage: {exc}")
            started = time.time()
            upload_files(job, volume, [(video, f"/{job.id}/input.mp4")], "clips", "the whole video")
            timings["upload"] = round(time.time() - started, 1)
            return None
        timings["cut"] = round(time.time() - started, 1)
        share = sum(p["bytes"] for p in pieces) / max(1, video.stat().st_size)
        log(f"Cut {len(pieces)} clip(s) in {timings['cut']:.1f}s: {share:.0%} of the video")
        started = time.time()
        upload_files(job, volume, [(p["path"], segments.footage_remote(job.id, p["index"])) for p in pieces],
                     "clips", "the clip footage")
        timings["clipUpload"] = round(time.time() - started, 1)
        return [{"index": p["index"], "targetUs": p["targetUs"]} for p in pieces]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def render_on_modal(job: Job, functions: dict, first: dict, footage: list | None, timings: dict, saver) -> dict:
    """Phase 2 on Modal: render every moment, downloading each clip as it is ready."""
    job.update(status="processing", message="Rendering on the GPU")
    set_progress(job, "rendering")
    started = time.time()
    call = spawn(job, functions[RENDER_FUNCTION], job.id, first["moments"], footage, first.get("clipWords"))
    second = wait_for(call, job, on_ready=saver.fetch if saver else None)
    finished(job)
    timings.update({f"gpu_{k}": v for k, v in (second.get("timings") or {}).items()})
    timings["gpu_total"] = round(float(timings.get("gpu_total") or 0) + time.time() - started, 1)
    return second


def merge_results(first: dict, second: dict | None) -> dict:
    """Both calls as one result in the shape the runner saves and prices (pipeline.py)."""
    second = second or {}
    seconds, orchestrator = costs.combine_orchestrators([
        (first.get("orchestratorSeconds"), first.get("orchestrator")),
        (second.get("orchestratorSeconds"), second.get("orchestrator"))])
    usages = [*(first.get("usages") or []), *(second.get("usages") or [])]
    cost = costs.estimate(usages, orchestrator_seconds=seconds, orchestrator=orchestrator)
    return {"clips": second.get("clips") or [], "moments": first.get("moments") or [],
            "errors": second.get("errors") or [], "subtitles": first.get("subtitles") or "parakeet",
            "timings": {**(first.get("timings") or {}), **(second.get("timings") or {})},
            "transcription": first.get("transcription") or {}, "gpu": second.get("gpu") or first.get("gpu"),
            "ranking": first.get("ranking") or {}, "cost": cost, "clipTimings": second.get("clipTimings") or [],
            "usages": usages, "orchestratorSeconds": seconds, "orchestrator": orchestrator}


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #

def first_stage(mode: str) -> str:
    return "transcribing" if mode == "parakeet" else "ranking"


def whole_video_run(job: Job, volume, functions: dict, max_clips: int, mode: str, context: dict, timings: dict,
                    saver, keep_video: bool) -> dict:
    """The whole video goes up first (as before the two phases), then both calls in turn."""
    put_video_on_modal(job, volume, timings, keep_video)
    if mode != "parakeet":
        upload_transcript(job, volume)
    job.update(status="processing", message="Processing on the GPU")
    set_progress(job, first_stage(mode))
    started = time.time()
    call = spawn(job, functions[TRANSCRIBE_FUNCTION], job.id, max_clips, context, mode, "input.mp4")
    first = wait_for(call, job)
    finished(job)
    phase_one_done(job, volume, first, mode, timings, started)
    if not first.get("moments"):
        return merge_results(first, None)
    return merge_results(first, render_on_modal(job, functions, first, None, timings, saver))


def get_video(job: Job, cloud: BackgroundCall, board: Board, audio_format: str | None, timings: dict):
    """The local video while phase 1 runs: an upload's own file, or the YouTube download
    (stopped early when phase 1 fails or finds nothing to render). Returns (path or None,
    the download's error or None, when the video was ready)."""
    if job.data.get("source") != "youtube":
        return local_upload(job), None, time.time()

    def progress(fraction: float) -> None:
        if cloud.failed or cloud.nothing_to_render:
            raise StopVideoDownload()
        board.on_download(fraction)

    started = time.time()
    try:
        video = download_youtube(job, on_progress=progress, audio_format=audio_format, update_status=False)
        return video, None, time.time()
    except StopVideoDownload:
        log("Stopped the video download: it is not needed")
        return None, None, time.time()
    except Exception as exc:  # noqa: BLE001 - raised once phase 1 has finished
        log(f"The video download failed ({type(exc).__name__}: {exc}); waiting for the cloud's ranking first")
        return None, exc, time.time()
    finally:
        timings["download"] = round(time.time() - started, 1)
        board.end_download()


def two_phase_run(job: Job, volume, functions: dict, max_clips: int, mode: str, context: dict, timings: dict,
                  saver, media: str | None, audio_format: str | None) -> dict:
    """Phase 1 in the cloud while the laptop gets the video, then phase 2."""
    if mode != "parakeet":
        upload_transcript(job, volume)
    job.update(status="processing", message="Finding the best moments" if mode != "parakeet" else "Transcribing",
               videoTransfer=AUDIO_FIRST)
    youtube = job.data.get("source") == "youtube"
    board = Board(job, first_stage(mode), downloading=youtube)
    board.show()
    started = time.time()
    call = spawn(job, functions[TRANSCRIBE_FUNCTION], job.id, max_clips, context, mode, media or "audio.m4a")
    cloud = BackgroundCall(job, call, board.on_cloud, on_done=board.end_cloud).start()
    video, video_error, video_ready = get_video(job, cloud, board, audio_format, timings)
    try:
        first = cloud.get()
    except Exception:
        if mode == "parakeet" and save_transcript(job, volume):
            log("Kept the transcript: Try again only ranks and renders")
        raise
    finally:
        board.end_cloud()
    finished(job)
    phase_one_done(job, volume, first, mode, timings, started)
    if video_error is not None:
        set_progress(job, "downloading")
        raise video_error
    if not first.get("moments"):
        return merge_results(first, None)
    if video is None:
        raise RuntimeError("The video download stopped before the clips could be cut")
    timings["downloadWait"] = round(max(0.0, video_ready - (cloud.finished_at or video_ready)), 1)
    footage = footage_on_modal(job, volume, video, first["moments"], timings)
    return merge_results(first, render_on_modal(job, functions, first, footage, timings, saver))


def process_on_modal(job: Job, max_clips: int, mode: str, timings: dict, saver=None, *, volume, context: dict,
                     keep_video: bool = False) -> dict:
    """Everything between the replay lookup and saving: media to Modal, phase 1, phase 2.
    mode: "parakeet" (transcribe), "reuse" (re-rank with transcript.json), "uploaded"
    (legacy Whistle re-rank). Returns the merged result (merge_results)."""
    functions = lookup_functions()
    if job.data.get("source") != "youtube":
        local_upload(job)
    if mode == "uploaded":
        return whole_video_run(job, volume, functions, max_clips, mode, context, timings, saver, keep_video)
    media, audio_format = None, None
    if mode == "parakeet":
        got = audio_on_modal(job, volume, timings)
        if got is None:
            return whole_video_run(job, volume, functions, max_clips, mode, context, timings, saver, keep_video)
        media, audio_format = got
    try:
        return two_phase_run(job, volume, functions, max_clips, mode, context, timings, saver, media, audio_format)
    finally:
        shutil.rmtree(download_folder(job) / "audio", ignore_errors=True)
        shutil.rmtree(download_folder(job) / "audio-download", ignore_errors=True)


def forget_download(job: Job, keep_video: bool) -> None:
    """After a run that succeeded: the YouTube video leaves the temp folder (into the job
    folder with keep_video). Never raises."""
    folder = download_folder(job)
    video = folder / "input.mp4"
    if keep_video and video.exists():
        try:
            shutil.move(str(video), str(job.dir / "input.mp4"))
        except OSError as exc:
            log(f"Could not keep the video in the job folder: {exc}")
    shutil.rmtree(folder, ignore_errors=True)


__all__ = ["process_on_modal", "forget_download", "merge_results", "StopVideoDownload", "AUDIO_FIRST"]
