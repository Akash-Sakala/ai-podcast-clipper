"""Job folder basics shared by every runner module: paths, logging and job.json read/write.
Standard library only."""

import datetime as dt
import json
import os
import pathlib
import secrets
import threading
import time

BACKEND_DIR = pathlib.Path(__file__).resolve().parent

LEGACY_DATA_DIR = BACKEND_DIR.parent / "data"     # inside the repo (and so inside OneDrive on the laptop)


def app_data_root() -> pathlib.Path:
    """Where the app keeps its own files outside the repo: %LOCALAPPDATA%\\ai-podcast-clipper on
    Windows (outside OneDrive), else $XDG_DATA_HOME (or ~/.local/share)/ai-podcast-clipper."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(pathlib.Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local" / "share")
    return pathlib.Path(base) / "ai-podcast-clipper"


def _holds_data(folder: pathlib.Path) -> bool:
    return (folder / "jobs").is_dir() or (folder / "stats").is_dir() or (folder / "settings.json").is_file()


def default_data_dir() -> pathlib.Path:
    """The data folder when DATA_DIR is not set, chosen like the web app's lib/paths.ts:
    <app data root>/data, except while only the repo's data folder holds jobs (an older
    version's, not moved yet: scripts/move-data.ps1)."""
    local = app_data_root() / "data"
    if not _holds_data(local) and _holds_data(LEGACY_DATA_DIR):
        return LEGACY_DATA_DIR
    return local


DEFAULT_DATA_DIR = default_data_dir()

# job.json is written from more than one thread (the upload stall watcher and Modal's
# progress callback, the clip downloads): one writer at a time, and data changes under
# the same lock so a save never serialises a dict that is being changed.
_SAVE_LOCK = threading.RLock()

REPLACE_ATTEMPTS = 12            # Windows/OneDrive: job.json may be open in the web app for a moment


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {message}", flush=True)


def replace_file(source: pathlib.Path, target: pathlib.Path, attempts: int = 10, sleep=time.sleep) -> None:
    """os.replace, retried while Windows refuses it because a program (the web app streaming
    a clip, a video player, OneDrive) has the target open. Raises the last error."""
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            sleep(min(0.25 * (attempt + 1), 2.0))


def pid_alive(pid) -> bool:
    """Whether a process with this id is running. Never kills anything: on Windows,
    os.kill(pid, 0) would terminate the process, so the process is queried instead.
    When in doubt, True (callers use it to protect a job)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE            # pointer-sized on 64-bit Windows
            kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, pid)        # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return ctypes.get_last_error() == 5                   # access denied: it exists
            try:
                code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return True
                return code.value == 259                              # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Job:
    """Reads and writes <job dir>/job.json (shared format with the web app)."""

    def __init__(self, job_dir: pathlib.Path):
        self.dir = job_dir
        self.file = job_dir / "job.json"
        self.data = json.loads(self.file.read_text(encoding="utf-8"))
        self.remote_call = None      # the spawned Modal call while it runs (pipeline.py: the spawned phase call)

    @property
    def id(self) -> str:
        return self.data["id"]

    def update(self, **fields) -> None:
        with _SAVE_LOCK:
            self.data.update(fields)
            self.data["updatedAt"] = now_iso()
            self.save()

    def save(self) -> bool:
        """Write job.json atomically (unique temp file, then os.replace). On Windows the
        replace fails while another program has job.json open, so it is retried for a few
        seconds. Never raises: a write that still fails is logged, and the next save
        writes everything again (job.data is complete)."""
        with _SAVE_LOCK:
            tmp = self.file.with_name(f"job.json.{os.getpid()}.{threading.get_ident()}."
                                      f"{secrets.token_hex(4)}.tmp")
            try:
                tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
                for attempt in range(REPLACE_ATTEMPTS):
                    try:
                        os.replace(tmp, self.file)
                        return True
                    except PermissionError:
                        if attempt == REPLACE_ATTEMPTS - 1:
                            raise
                        time.sleep(min(0.1 * (attempt + 1), 1.0))
            except OSError as exc:
                log(f"Could not save job.json ({type(exc).__name__}: {exc}); it is written again "
                    "with the next update")
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                return False
        return False

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


def set_progress(job: Job, stage: str, **fields) -> None:
    """Structured progress for the UI: stage = audio | downloading | uploading | transcribing |
    ranking | clips | rendering | saving, plus optional done/total/fraction/etaSeconds (and
    "download", the share of the video downloaded while the cloud works, see phases.Board)."""
    with _SAVE_LOCK:
        previous = job.data.get("progress") or {}
        started = previous.get("stageStartedAt") if previous.get("stage") == stage else now_iso()
        job.update(progress={"stage": stage, "stageStartedAt": started, **fields})
