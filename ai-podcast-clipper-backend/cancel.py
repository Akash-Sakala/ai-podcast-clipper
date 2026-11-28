"""Cancel a running video from the app (runner.py --job-dir <dir> --cancel): stop its runner
on this laptop, cancel what it started on Modal, remove its files from the volume and leave
job.json the way a stopped run leaves it, marked as cancelled. Laptop only (no redeploy).

The runner is stopped outright (Windows: taskkill /T /F, which also ends its FFmpeg), because
the app starts it with pythonw.exe, which has no console to send Ctrl+Break to. Whatever it
would have cleaned up on the way out is done here instead. Try again then resumes like after
any stopped run: a partial YouTube download, the saved transcript and any clips kept on Modal
are reused."""

import os
import pathlib
import shutil
import signal
import subprocess
import time
from collections.abc import Callable

import transfer
from clipper_names import JOBS_VOLUME
from clipsets import NEW_DIR
from jobstore import Job, log, now_iso, pid_alive
from media import no_window
from transfer import cleanup_remote, discard_extras, leftover_call_ids

FINAL_STATUSES = ("done", "failed")
STOP_WAIT_S = 15
PID_WAIT_S = 5                 # a runner started a moment ago has not written its pid yet


def _windows_program(pid: int) -> str | None:
    """Full path of the program a Windows process runs, or None when it can't be read."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                        ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001 - unknown means "not ours": nothing is stopped
        return None


def _command_line(pid: int) -> str:
    try:
        return pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        pass
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def is_runner(pid) -> bool:
    """Whether pid still is a runner, so a pid Windows has given to another program since is
    never stopped. Windows: the program is python.exe or pythonw.exe (its command line can't be
    read without admin tools); elsewhere: the command line names runner.py."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name == "nt":
        program = _windows_program(pid)
        return bool(program) and os.path.basename(program).lower().startswith("python")
    return "runner.py" in _command_line(pid)


def stop_process_tree(pid: int, wait_s: float = STOP_WAIT_S, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Stop the runner and everything it started. True when it is gone."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30,
                           **no_window())
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"Could not stop the runner ({type(exc).__name__}: {exc})")
    else:
        # The app starts the runner in a session of its own: stop the whole group, and only
        # the runner itself when it shares a group (started from a terminal).
        try:
            group = os.getpgid(pid)
            if group == pid:
                os.killpg(group, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + wait_s
    while pid_alive(pid):
        if time.monotonic() >= deadline:
            if os.name != "nt":
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                sleep(0.5)
            return not pid_alive(pid)
        sleep(0.25)
    return True


def _open_volume():
    import modal

    return modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)


def _remove_temp_files(job: Job) -> None:
    """job.json.<pid>.<thread>.<token>.tmp files a write left when the runner was stopped mid-save."""
    for tmp in job.dir.glob("job.json.*.tmp"):
        try:
            tmp.unlink()
        except OSError:
            pass


def cancel_job(job_dir: pathlib.Path, open_volume: Callable = _open_volume,
               stop: Callable[[int], bool] = stop_process_tree, runner_check: Callable[[int], bool] = is_runner,
               alive: Callable[[int], bool] = pid_alive, sleep: Callable[[float], None] = time.sleep) -> int:
    """Cancel the job in job_dir. 0 when it is cancelled (or was not running), 1 when its
    runner could not be stopped (nothing else is changed then)."""
    job = Job(job_dir)
    # Just started: the runner writes its pid within a moment of starting.
    for _ in range(int(PID_WAIT_S / 0.25)):
        if job.data.get("pid") or job.data.get("status") in FINAL_STATUSES:
            break
        sleep(0.25)
        job = Job(job_dir)
    pid = job.data.get("pid")
    running = bool(pid) and alive(pid) and runner_check(pid)
    if job.data.get("status") in FINAL_STATUSES and not running:
        log("Nothing to cancel: this video is not running")
        return 0
    stage = (job.data.get("progress") or {}).get("stage")
    if running:
        log(f"Cancelling: stopping the runner (pid {pid})")
        if not stop(int(pid)):
            log("The runner did not stop, so nothing was cancelled")
            return 1
    elif pid and alive(pid):
        log(f"Process {pid} is no longer this video's runner: it is left alone")

    job = Job(job_dir)                    # what the runner wrote last
    stage = (job.data.get("progress") or {}).get("stage") or stage or job.data.get("failedStage") or "starting"
    calls = leftover_call_ids(job.data)
    cancelled = sum(1 for call_id in calls if transfer.cancel_call_id(call_id))
    if calls:
        log(f"Cancelled {cancelled} of {len(calls)} Modal call(s)")
    # A call that could not be cancelled (offline, say) stays in job.json: the next run's
    # sweep cancels it then.
    keep_ids = cancelled < len(calls)
    try:
        cleanup_remote(job, open_volume(), keep_clips=bool(job.data.get("pendingSave")))
    except Exception as exc:  # noqa: BLE001 - the next run's sweep removes the folder
        log(f"Could not clean up the Modal volume now ({type(exc).__name__}: {exc}); the next run does it")
    discard_extras(job)
    if job.data.get("mode") == "rerank":
        shutil.rmtree(job.dir / NEW_DIR, ignore_errors=True)
    _remove_temp_files(job)
    keep_clips = bool(job.data.get("clips"))
    job.update(status="done" if keep_clips else "failed",
               message="Cancelled - previous clips kept" if keep_clips else "Cancelled",
               error=None if keep_clips else "Cancelled", cancelledAt=now_iso(), failedStage=stage,
               progress=None, pid=None, liveClips=None, liveClipsSet=None, plannedClips=None,
               **({} if keep_ids else {"modalCallId": None, "modalChildCallIds": None}))
    log("Cancelled" + (" - previous clips kept" if keep_clips else ""))
    return 0
