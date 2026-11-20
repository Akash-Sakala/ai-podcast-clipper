"""
Getting a job through Modal in two phases (pipeline.py runs this between the replay
lookup and saving the clips):

  phase 1   the audio goes up first (YouTube: the audio stream alone; an upload: copied out
            of the MP4 on this laptop) and Modal transcribes and ranks it. Meanwhile the
            laptop downloads the YouTube video: the download starts as soon as the audio
            stream is on the laptop, so it also runs while the audio uploads
            (VideoDownload). A re-rank uploads the saved transcript instead (rank only).
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
"download" starts while the audio uploads, so it overlaps "audio" as well as phase 1.
"""

import shutil
import subprocess
import threading
import time

import costs
import cutter
import segments
import videostore
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

    def __init__(self, job: Job, first_stage: str, downloading: bool, fraction: float | None = None):
        self.job = job
        self.cloud: dict = {"stage": first_stage}
        self.cloud_done = False
        self.downloading = downloading
        self.fraction = fraction         # the download may be under way already (VideoDownload)
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
# The YouTube video, downloaded on its own thread
# --------------------------------------------------------------------------- #

class VideoDownload:
    """The YouTube video download on a background thread, so it can start while the audio
    still uploads and go on while the cloud transcribes and ranks.

    Its share done goes to whoever shows progress at the time: the audio upload's bar reads
    `fraction`; phase 1's Board is told through watch(). The download stops itself (the
    partial file stays, so Try again resumes it) when cancel() is called or the watched
    cloud call fails or finds nothing to render. wait() hands its outcome to the main thread."""

    def __init__(self, job: Job, audio_format: str | None, timings: dict):
        self.job, self.audio_format, self.timings = job, audio_format, timings
        self.fraction: float | None = None
        self.cloud: BackgroundCall | None = None
        self.board: Board | None = None
        self.video = None
        self.error: BaseException | None = None
        self.ready_at: float | None = None
        self.cancelled = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, name="video-download", daemon=True)

    def start(self) -> "VideoDownload":
        self.thread.start()
        return self

    def watch(self, cloud: BackgroundCall, board: Board) -> None:
        """Phase 1 has started: stop when it fails or has nothing to render, report to its board."""
        self.cloud, self.board = cloud, board
        if self.done.is_set():
            board.end_download()

    def _progress(self, fraction: float) -> None:
        cloud = self.cloud
        if self.cancelled.is_set() or (cloud is not None and (cloud.failed or cloud.nothing_to_render)):
            raise StopVideoDownload()
        self.fraction = fraction
        board = self.board
        if board is not None:
            board.on_download(fraction)

    def _run(self) -> None:
        started = time.time()
        try:
            self.video = download_youtube(self.job, on_progress=self._progress, audio_format=self.audio_format,
                                          update_status=False)
        except StopVideoDownload:
            log("Stopped the video download: it is not needed")
        except Exception as exc:  # noqa: BLE001 - raised by the run once phase 1 has finished
            log(f"The video download failed ({type(exc).__name__}: {exc}); waiting for the cloud's ranking first")
            self.error = exc
        except BaseException as exc:  # noqa: BLE001 - handed to wait(), like any other outcome
            self.error = exc
        finally:
            self.ready_at = time.time()
            self.timings["download"] = round(self.ready_at - started, 1)
            self.done.set()
            board = self.board
            if board is not None:
                board.end_download()

    def wait(self):
        """(path or None, the download's error or None, when it ended). A stop signal that
        ended the download is raised here, on the calling thread."""
        while not self.done.wait(0.5):    # short waits: a stop signal is handled on this thread
            pass
        if self.error is not None and not isinstance(self.error, Exception):
            raise self.error
        return self.video, self.error, self.ready_at

    def cancel(self, timeout: float = 5.0) -> None:
        """The run is ending without the video: stop at the next progress report. Never raises."""
        self.cancelled.set()
        self.done.wait(timeout)


# --------------------------------------------------------------------------- #
# Phase 1
# --------------------------------------------------------------------------- #

def local_upload(job: Job):
    """An upload's video: in the job folder (a failed run's copy, or the file added again), else
    the kept one (videostore.py). FileNotFoundError when the file must be added again."""
    video = videostore.local_video(job)
    if video is None:
        raise FileNotFoundError(MISSING_UPLOAD)
    return video


def audio_on_modal(job: Job, volume, timings: dict, start_video=None) -> tuple[str, str | None] | None:
    """Put the video's audio on the volume: (its name there, the YouTube audio format id),
    or None (with the reason in runner.log) when it can't be had on its own.
    start_video(format id) is called once a YouTube video's audio stream is on the laptop
    and returns the VideoDownload it started: the video then downloads while the audio is
    prepared and uploaded, and the upload's progress carries its share as "download"."""
    started = time.time()
    folder = download_folder(job) / "audio"
    job.update(status="uploading", message="Sending the audio to Modal", videoTransfer=AUDIO_FIRST)
    set_progress(job, "audio")
    upload = None if job.data.get("source") == "youtube" else local_upload(job)   # a missing video raises here
    try:
        video = None
        if upload is None:
            source, format_id = download_youtube_audio(job)
            if start_video is not None:
                video = start_video(format_id)
        else:
            source, format_id = upload, None
        audio = cutter.extract_audio(source, folder)
    except Exception as exc:  # noqa: BLE001 - any failure: the whole video, as before
        log(f"Could not get the audio on its own ({type(exc).__name__}: {exc}); uploading the whole video instead")
        return None
    meanwhile = (lambda: {"download": video.fraction or 0.0}) if video is not None else None
    upload_files(job, volume, [(audio, f"/{job.id}/{audio.name}")], "audio", "the audio", extra=meanwhile)
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
    if saver is not None and hasattr(saver, "describe"):
        saver.describe(first["moments"])
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


def get_video(job: Job, cloud: BackgroundCall, board: Board, audio_format: str | None, timings: dict,
              download: VideoDownload | None = None):
    """The local video while phase 1 runs: an upload's own file, or the YouTube download
    (already under way when it started during the audio upload; stopped early when phase 1
    fails or finds nothing to render). Returns (path or None, the download's error or None,
    when the video was ready)."""
    if job.data.get("source") != "youtube":
        return local_upload(job), None, time.time()
    if download is None:
        download = VideoDownload(job, audio_format, timings)
        download.watch(cloud, board)
        download.start()
    else:
        download.watch(cloud, board)
    return download.wait()


def two_phase_run(job: Job, volume, functions: dict, max_clips: int, mode: str, context: dict, timings: dict,
                  saver, media: str | None, audio_format: str | None,
                  download: VideoDownload | None = None) -> dict:
    """Phase 1 in the cloud while the laptop gets the video (`download`: already started
    during the audio upload), then phase 2."""
    if mode != "parakeet":
        upload_transcript(job, volume)
    job.update(status="processing", message="Finding the best moments" if mode != "parakeet" else "Transcribing",
               videoTransfer=AUDIO_FIRST)
    youtube = job.data.get("source") == "youtube"
    board = Board(job, first_stage(mode), downloading=youtube,
                  fraction=download.fraction if download is not None else None)
    board.show()
    started = time.time()
    call = spawn(job, functions[TRANSCRIBE_FUNCTION], job.id, max_clips, context, mode, media or "audio.m4a")
    cloud = BackgroundCall(job, call, board.on_cloud, on_done=board.end_cloud).start()
    video, video_error, video_ready = get_video(job, cloud, board, audio_format, timings, download)
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
    early: list[VideoDownload] = []      # the video download started during the audio upload, if any

    def start_video(format_id: str | None) -> VideoDownload:
        early.append(VideoDownload(job, format_id, timings).start())
        return early[0]

    try:
        if mode == "parakeet":
            got = audio_on_modal(job, volume, timings, start_video)
            if got is None:
                if early:   # the same file is uploaded whole next: let its download finish first
                    set_progress(job, "downloading", fraction=early[0].fraction)
                    early[0].wait()
                return whole_video_run(job, volume, functions, max_clips, mode, context, timings, saver, keep_video)
            media, audio_format = got
        return two_phase_run(job, volume, functions, max_clips, mode, context, timings, saver, media, audio_format,
                             early[0] if early else None)
    finally:
        if early and not early[0].done.is_set():   # the run ended without the video (an error, a stop)
            early[0].cancel()
        shutil.rmtree(download_folder(job) / "audio", ignore_errors=True)
        shutil.rmtree(download_folder(job) / "audio-download", ignore_errors=True)


def forget_download(job: Job, keep_video: bool) -> None:
    """After a run that succeeded: the YouTube video leaves the temp folder, into the kept
    videos (videostore.py; deleted when keeping is off) or, with keep_video, into the job
    folder. A video taken from the kept videos stays where it is. Never raises."""
    folder = download_folder(job)
    video = folder / "input.mp4"
    if video.exists():
        if keep_video:
            try:
                shutil.move(str(video), str(job.dir / "input.mp4"))
            except OSError as exc:
                log(f"Could not keep the video in the job folder: {exc}")
        else:
            videostore.keep(job, video)
    shutil.rmtree(folder, ignore_errors=True)


__all__ = ["process_on_modal", "forget_download", "merge_results", "StopVideoDownload", "AUDIO_FIRST"]
