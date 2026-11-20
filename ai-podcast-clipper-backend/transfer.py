"""Everything that touches the Modal jobs volume: uploads (with Modal's private upload API),
the transcript upload, downloading the clips (each as soon as it is rendered), removing
the job's remote folder, and the sweep that removes what a killed runner left behind."""

import json
import pathlib
import re
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import settings
import videostore
from clipper_names import JOBS_VOLUME
from jobstore import Job, log, now_iso, pid_alive, replace_file, set_progress
from media import save_clip_poster
from youtube import download_youtube

UPLOAD_PIECES_AT_ONCE = 4        # Modal's default is 128: on a home uplink every piece crawls and times out

UPLOAD_PIECE_TIMEOUT_S = 600     # a piece (8 MiB) that takes longer is retried; Modal sets no limit

UPLOAD_STALL_S = 120             # no bytes for this long: say so in runner.log and the progress strip

# The private upload path (limit_upload_piece_time, upload_context) was checked against
# these modal versions (requirements-local.txt pins the same range). Outside it, the
# plain batch_upload is used if anything doesn't fit.
MODAL_TESTED = ("1.6.4", "1.7")

DOWNLOAD_ATTEMPTS = 4            # per file; waits 2, 4, 8 s between attempts

CLIP_NAME = re.compile(r"clip_\d+\.mp4")

# Volume folders the sweep may remove: a job id (data/jobs/<id>) or a benchmark run.
REMOTE_FOLDER = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}|bench-\d+")

SWEEP_MIN_AGE_HOURS = 24         # folders of unknown or kept jobs are removed only after this long

TERMINAL_STATUSES = ("done", "failed")


def modal_version() -> str:
    try:
        import modal

        return str(getattr(modal, "__version__", "?"))
    except Exception:  # noqa: BLE001
        return "?"


def _private_api_note() -> str:
    return (f"modal {modal_version()}; this code was checked against modal >={MODAL_TESTED[0]},"
            f"<{MODAL_TESTED[1]}")


class UploadProgress:
    """Counts the bytes Modal reports as sent (its upload progress callback: `advance=`
    bytes, negative when a failed piece is retried, `reset=True` to start a file over) and
    mirrors the share done into job.json every few seconds, plus a line in runner.log every
    10% and when the upload stalls."""

    def __init__(self, job: Job, size: int, every_s: float = 2.0, clock: Callable[[], float] = time.time,
                 stage: str = "uploading", extra: Callable[[], dict] | None = None):
        self.job, self.size, self.every_s, self.clock, self.stage = job, max(size, 1), every_s, clock, stage
        self.extra = extra               # more progress fields, read at every report (phases: "download")
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
        set_progress(self.job, self.stage, fraction=round(self.fraction, 3), etaSeconds=eta,
                     stalled=self.stalled or None, **self.extra_fields())
        tenth = int(self.fraction * 10)
        if tenth > self.last_logged:
            self.last_logged = tenth
            log(f"Uploaded {self.sent / 1024 / 1024:.0f} of {self.size / 1024 / 1024:.0f} MB "
                f"({speed * 8 / 1e6:.1f} Mbps on average)")

    def extra_fields(self) -> dict:
        try:
            return dict(self.extra()) if self.extra is not None else {}
        except Exception:  # noqa: BLE001 - progress is best effort
            return {}

    def check_stall(self) -> None:
        """Called every few seconds by a watcher thread: flag an upload with no bytes moving."""
        idle = self.clock() - self.last_bytes_at
        if not self.stalled and idle >= UPLOAD_STALL_S:
            self.stalled = True
            log(f"Upload: no data sent for {idle / 60:.0f} min; Modal retries the stuck pieces "
                f"(each gets up to {UPLOAD_PIECE_TIMEOUT_S // 60} min)")
            self.report(force=True)


class FileProgress:
    """One file's share of an UploadProgress that spans several files uploaded one after
    another: Modal's `reset=True` (start this file over) takes back only this file's bytes."""

    def __init__(self, total: UploadProgress):
        self.total, self.sent = total, 0

    def __call__(self, *args, **kwargs):
        advance = kwargs.get("advance")
        if kwargs.get("reset"):
            if self.sent:
                self.total(advance=-self.sent)
            self.sent = 0
        elif isinstance(advance, (int, float)) and advance:
            self.sent = max(0, self.sent + int(advance))
            self.total(advance=advance)
        return None

    def check_stall(self) -> None:
        self.total.check_stall()


def limit_upload_piece_time(seconds: float) -> bool:
    """Give Modal's file-transfer HTTP session a per-request time limit (it has none, so a
    connection that dies quietly hangs a piece forever; with a limit the piece is retried).

    Modal builds that session in ClientSessionRegistry.get_session() with
    `_http_client_with_tls(timeout=None)`; this wraps that one factory so the session gets
    `seconds` instead, and leaves get_session (and its shutdown hook) to Modal.
    Private Modal API: skipped, returning False, if this modal version doesn't fit."""
    try:
        from modal._utils import http_utils

        factory = http_utils._http_client_with_tls
        if getattr(factory, "_clipper_timeout", None) == seconds:
            return True
        original = getattr(factory, "_clipper_original", factory)
        registry = http_utils.ClientSessionRegistry
        if not callable(getattr(registry, "get_session", None)) or \
                not hasattr(registry, "_client_session_active"):
            raise AttributeError("ClientSessionRegistry changed")

        def with_time_limit(timeout=None, *args, **kwargs):
            return original(seconds if timeout is None else timeout, *args, **kwargs)

        with_time_limit._clipper_timeout = seconds
        with_time_limit._clipper_original = original
        http_utils._http_client_with_tls = with_time_limit
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"Could not set an upload time limit ({type(exc).__name__}: {exc}; {_private_api_note()})")
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
        log(f"Upload progress unavailable ({type(exc).__name__}: {exc}; {_private_api_note()}); "
            "uploading without it")
        return None


def upload_with_progress(volume, local: pathlib.Path, remote: str, progress) -> None:
    """Upload one file with live progress, a time limit per piece and a stall watcher.
    Falls back to the plain `batch_upload` when Modal's internals don't fit."""
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


def upload_files(job: Job, volume, files: list[tuple[pathlib.Path, str]], stage: str, what: str,
                 extra: Callable[[], dict] | None = None) -> None:
    """Upload files (local path, remote path) one after another with one progress bar over
    all of them, shown as `stage` in job.json. extra() adds fields to every progress report."""
    total = sum(local.stat().st_size for local, _ in files)
    progress = UploadProgress(job, total, stage=stage, extra=extra)
    set_progress(job, stage, fraction=0.0, **progress.extra_fields())
    log(f"Uploading {what} ({total / 1024 / 1024:.1f} MB) to volume {JOBS_VOLUME}")
    start = time.time()
    for local, remote in files:
        upload_with_progress(volume, local, remote, FileProgress(progress))
    took = time.time() - start
    log(f"Uploaded {what} in {took:.0f}s ({total * 8 / 1e6 / max(took, 0.001):.1f} Mbps)")


def put_video_on_modal(job: Job, volume, timings: dict, keep_video: bool = False) -> None:
    """Get the video into /<job id>/input.mp4 on the Modal volume.

    A video in the job folder (an upload) is uploaded from there, else an upload's kept video
    (videostore.py). A YouTube video is downloaded into its temp folder (or taken from the
    kept videos), uploaded, and then moves into the kept videos (with keep_video, into the
    job folder; if keeping is off, it is deleted with the temp folder). A kept video is
    never moved or deleted here."""
    local = job.dir / "input.mp4"
    source = local
    transfer = "upload"
    if not local.exists() and job.data.get("source") != "youtube":
        source = videostore.use(job) or local
    elif not local.exists() and job.data.get("source") == "youtube":
        transfer = "laptop-download"
        job.update(videoTransfer=transfer)
        set_progress(job, "downloading")
        start = time.time()
        source = download_youtube(job)
        # Added to what a download started during the audio upload already took (phases.py).
        timings["download"] = round(float(timings.get("download") or 0) + time.time() - start, 1)

    if not source.exists():
        raise FileNotFoundError("The uploaded video is no longer in the job folder: add the same MP4 again "
                                "to re-rank it")
    if transfer == "upload":
        job.update(videoTransfer=transfer)
    set_progress(job, "uploading")
    start = time.time()
    upload_input(job, volume, video=source)
    timings["upload"] = round(time.time() - start, 1)
    if source != local and not videostore.holds(source):
        if keep_video:
            try:
                shutil.move(str(source), str(local))
            except OSError as exc:
                log(f"Could not keep the video in the job folder: {exc}")
        else:
            videostore.keep(job, source)
        shutil.rmtree(source.parent, ignore_errors=True)


def upload_transcript(job: Job, volume) -> None:
    with volume.batch_upload(force=True) as batch:
        batch.put_file(str(job.dir / "transcript.json"), f"/{job.id}/transcript.json")


def download_file(volume, remote: str, target: pathlib.Path, attempts: int = DOWNLOAD_ATTEMPTS,
                  sleep: Callable[[float], None] | None = None) -> None:
    """Copy one file from the volume to `target`: written to <target>.part, then renamed,
    so a broken download never leaves a truncated file under the real name. Retried with
    backoff; a file that is not on the volume (FileNotFoundError) is not retried."""
    sleep = sleep or time.sleep
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    for attempt in range(1, attempts + 1):
        try:
            with open(part, "wb") as f:
                volume.read_file_into_fileobj(remote, f)
            replace_file(part, target)
            return
        except FileNotFoundError:
            part.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 - network drops, Modal hiccups, a locked file
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt == attempts:
                raise
            wait = 2 ** attempt
            log(f"Download of {remote} failed ({type(exc).__name__}: {exc}); retrying in {wait}s")
            sleep(wait)


class ClipSaver:
    """Downloads clips from /<job id>/clips/ into `target_dir`. Each clip is fetched as soon
    as render_clips reports it ready (fetch, called while the others still render), on a
    small thread pool; finish() waits for all of them and fetches any not fetched yet."""

    def __init__(self, job: Job, volume, target_dir: pathlib.Path, workers: int | None = None,
                 sleep: Callable[[float], None] | None = None,
                 on_saved: Callable[[list[str]], None] | None = None):
        self.job, self.volume, self.dir, self.sleep = job, volume, target_dir, sleep
        self.workers = workers or settings.CLIP_DOWNLOADS_AT_ONCE
        self.futures: dict = {}
        self.pool: ThreadPoolExecutor | None = None
        self.on_saved = on_saved
        self.moments: list | None = None
        self._lock = threading.Lock()
        self._done: dict[str, dict] = {}     # name -> {bytes, seconds, start, end}
        self._saved: set[str] = set()

    def _save(self, name: str) -> str:
        target = self.dir / name
        began = time.time()
        download_file(self.volume, f"/{self.job.id}/clips/{name}", target, sleep=self.sleep)
        ended = time.time()
        try:
            size = target.stat().st_size
        except OSError:
            size = 0
        seconds = ended - began
        save_clip_poster(target)
        with self._lock:
            self._done[name] = {"bytes": size, "seconds": seconds, "start": began, "end": ended}
        mbps = size * 8 / 1e6 / seconds if seconds > 0 else 0.0
        log(f"Saved {name} ({size / 1024 / 1024:.1f} MB in {seconds:.1f}s, {mbps:.1f} Mbps)")
        with self._lock:
            self._saved.add(name)
        self._notify()
        return name

    def _notify(self) -> None:
        if self.on_saved is None:
            return
        with self._lock:
            names = sorted(self._saved)
        if not names:
            return
        try:
            self.on_saved(names)
        except Exception as exc:  # noqa: BLE001 - progress publishing must never fail a download
            log(f"Could not publish the saved clips ({type(exc).__name__}: {exc})")

    def describe(self, moments: list | None) -> None:
        """The ranked moments (clip_N is moments[N]); republishes what is already saved."""
        self.moments = moments
        self._notify()

    def stats(self) -> dict | None:
        """Download summary: clips, MB, wall-clock seconds from the first download's start to the
        last one's end, Mbps over that wall clock, and how many ran at once."""
        with self._lock:
            done = list(self._done.values())
        if not done:
            return None
        seconds = max(d["end"] for d in done) - min(d["start"] for d in done)
        total = sum(d["bytes"] for d in done)
        mbps = total * 8 / 1e6 / seconds if seconds > 0 else 0.0
        return {"clips": len(done), "megabytes": round(total / 1024 / 1024, 1), "seconds": round(seconds, 1),
                "mbps": round(mbps, 1), "atOnce": self.workers}

    def fetch(self, names) -> None:
        for name in names or []:
            if name in self.futures or not CLIP_NAME.fullmatch(str(name)):
                continue
            if self.pool is None:
                self.dir.mkdir(parents=True, exist_ok=True)
                self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix="clip-download")
            self.futures[name] = self.pool.submit(self._save, name)

    def finish(self, names: list[str]) -> list[str]:
        """Every clip in `names` saved, in that order. A clip whose early download failed
        is tried once more; then the first error is raised."""
        try:
            self.fetch(names)
            errors = []
            for name in names:
                try:
                    self.futures[name].result()
                except Exception:  # noqa: BLE001 - try once more below
                    del self.futures[name]
                    self.fetch([name])
                    try:
                        self.futures[name].result()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(exc)
            if errors:
                raise errors[0]
            return list(names)
        finally:
            self.close()

    def close(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)
            self.pool = None


def download_outputs(job: Job, volume, result: dict, clips_dir: pathlib.Path | None = None,
                     saver: ClipSaver | None = None) -> list[str]:
    """Save the run's clips (those not downloaded during rendering) into clips_dir, and its
    transcript.json / moments.json as <name>.new in the job folder: commit_extras() puts
    those in place once the clips are (so moments.json always matches clip_N)."""
    job.update(status="saving", message="Saving clips")
    set_progress(job, "saving")
    saver = saver or ClipSaver(job, volume, clips_dir or job.dir / "clips")
    saved = saver.finish([name for name in result.get("clips") or [] if CLIP_NAME.fullmatch(str(name))])
    for extra in ("transcript.json", "moments.json"):
        try:
            download_file(volume, f"/{job.id}/{extra}", job.dir / f"{extra}.new", sleep=saver.sleep)
        except FileNotFoundError:   # optional files
            continue
    return saved


def commit_extras(job: Job, only: tuple[str, ...] = ("transcript.json", "moments.json")) -> None:
    """transcript.json.new / moments.json.new -> their real names (`only` those named)."""
    for extra in only:
        staged = job.dir / f"{extra}.new"
        if staged.exists():
            replace_file(staged, job.dir / extra)


def discard_extras(job: Job) -> None:
    for extra in ("transcript.json", "moments.json"):
        try:
            (job.dir / f"{extra}.new").unlink(missing_ok=True)
        except OSError:
            pass


# Everything of a job on the volume except its clips: the media the runner uploaded.
REMOTE_MEDIA = ("input.mp4", "audio.m4a", "audio.flac", "footage")


def cleanup_remote(job: Job, volume, keep_clips: bool = False) -> None:
    """Remove /<job id> from the volume. keep_clips: remove only the video, the audio and
    the clip footage, so a later run can still download the rendered clips (the sweep
    removes them after a day)."""
    paths = [f"/{job.id}/{name}" for name in REMOTE_MEDIA] if keep_clips else [f"/{job.id}"]
    for path in paths:
        try:
            volume.remove_file(path, recursive=True)
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001
            log(f"Could not clean up remote files ({path}): {exc}")


def cancel_call_id(call_id: str) -> bool:
    """Cancel a Modal call by id (transcribe_and_rank or render_clips, left running by a
    runner that was killed)."""
    try:
        import modal

        modal.FunctionCall.from_id(call_id).cancel(terminate_containers=True)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"Could not cancel Modal call {call_id}: {exc}")
        return False


def local_state(job_dir: pathlib.Path) -> tuple[str, dict]:
    """How the sweep treats a volume folder, from the local job.json:
    active   a runner is on it, or about to start (non-final status with a live pid or no pid yet),
             or job.json can't be read: never touched
    keep     clips kept for Try again (pendingSave) or --keep-remote: removed after SWEEP_MIN_AGE_HOURS
    unknown  no such job in this data folder (deleted, or another data folder): same
    idle     finished or failed, or its runner is gone: nothing can use the folder, removed now"""
    try:
        data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "unknown", {}
    except (OSError, ValueError):
        return "active", {}
    if not isinstance(data, dict):
        return "active", {}
    status = data.get("status")
    if status not in TERMINAL_STATUSES:
        if not data.get("pid") or pid_alive(data["pid"]):
            return "active", data
    if data.get("pendingSave") or data.get("keepRemote"):
        return "keep", data
    return "idle", data


def _newest_mtime(volume, name: str, entry) -> float:
    newest = float(getattr(entry, "mtime", 0) or 0)
    try:
        for item in volume.listdir(f"/{name}", recursive=True):
            newest = max(newest, float(getattr(item, "mtime", 0) or 0))
    except Exception:  # noqa: BLE001 - keep the folder's own time
        pass
    return newest


def sweep_remote(volume, data_dir: pathlib.Path, current_job: str | None = None, now: float | None = None,
                 min_age_hours: float = SWEEP_MIN_AGE_HOURS) -> list[str]:
    """Remove volume folders no run will use again, so no video stays on Modal when a runner
    was killed before its own clean-up (Task Manager, shutdown, a crash). Runs at the start
    of every run; never raises. Rules (see local_state): the current job and active jobs are
    never touched; idle jobs are removed now (their leftover Modal call, if any, is
    cancelled first); kept and unknown folders only once nothing in them changed for
    min_age_hours. Folder names that are not job ids are left alone. Returns the removed names."""
    now = time.time() if now is None else now
    try:
        entries = volume.listdir("/")
    except Exception as exc:  # noqa: BLE001
        log(f"Could not list the Modal volume for clean-up: {exc}")
        return []
    removed = []
    for entry in entries:
        name = str(getattr(entry, "path", "")).strip("/")
        if not REMOTE_FOLDER.fullmatch(name) or name == current_job:
            continue
        if int(getattr(entry, "type", 2) or 2) != 2:          # FileEntryType.DIRECTORY
            continue
        job_dir = data_dir / "jobs" / name
        state, data = local_state(job_dir)
        if state == "active":
            continue
        if state in ("keep", "unknown"):
            newest = _newest_mtime(volume, name, entry)
            if newest <= 0 or now - newest < min_age_hours * 3600:
                continue
        state, data = local_state(job_dir)                   # again, right before deleting
        if state == "active":
            continue
        if data.get("modalCallId"):
            cancel_call_id(data["modalCallId"])
        try:
            volume.remove_file(f"/{name}", recursive=True)
            removed.append(name)
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            log(f"Could not remove /{name} from the volume: {exc}")
            continue
        if data and (data.get("pendingSave") or data.get("modalCallId")):
            try:
                job = Job(job_dir)
                job.update(pendingSave=None, modalCallId=None)
            except Exception:  # noqa: BLE001
                pass
    if removed:
        log(f"Removed {len(removed)} leftover folder(s) from the Modal volume: {', '.join(removed)}")
    return removed


def pending_record(mode: str, result: dict, timings: dict, elapsed: float) -> dict:
    """job.json "pendingSave": a run whose clips rendered but could not be saved. Its
    clips stay on the volume, and the next run of the same mode downloads them instead
    of running the GPU pipeline again."""
    return {"mode": mode, "savedAt": now_iso(), "elapsed": round(elapsed, 1), "timings": dict(timings),
            "result": {k: v for k, v in result.items() if k != "data"}}
