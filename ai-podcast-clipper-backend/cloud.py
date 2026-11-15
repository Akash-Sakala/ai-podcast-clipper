"""Calls to the deployed Modal functions: look them up, spawn them, wait for their result
while mirroring their live progress, and cancel them when a run ends without it."""

import threading
import time
from collections.abc import Callable

from clipper_names import APP_NAME, PROGRESS_DICT, RENDER_FUNCTION, TRANSCRIBE_FUNCTION
from jobstore import Job, log, set_progress
from transfer import cancel_call_id

POLL_SECONDS = 3

MODAL_OUTAGE_S = 900             # keep waiting for a running call through connection drops this long

FUNCTIONS = (TRANSCRIBE_FUNCTION, RENDER_FUNCTION)


class DeployedAppTooOld(RuntimeError):
    pass


def lookup_functions(names=FUNCTIONS) -> dict:
    """{name: modal.Function} of the deployed app. Fails at once, before anything is uploaded,
    when the deployment is older than this runner."""
    import modal

    functions = {}
    for name in names:
        function = modal.Function.from_name(APP_NAME, name)
        hydrate = getattr(function, "hydrate", None)
        if callable(hydrate):
            try:
                hydrate()
            except modal.exception.NotFoundError as exc:
                raise DeployedAppTooOld(f"The deployed Modal app has no {name} function (it is older than this "
                                        "runner): run `modal deploy main.py` in ai-podcast-clipper-backend") from exc
        functions[name] = function
    return functions


def mirror_progress(job: Job) -> Callable[[dict], None]:
    """The default progress handler: the call's stage and numbers into job.json."""

    def mirror(current: dict) -> None:
        fields = {k: v for k, v in current.items() if k not in ("stage", "ready")}
        set_progress(job, current.get("stage", "processing"), **fields)

    return mirror


def wait_for(call, job: Job, on_ready=None, on_progress: Callable[[dict], None] | None = None):
    """The result of a spawned Modal call, mirroring its live progress meanwhile (on_progress,
    default: into job.json). on_ready(names) gets the clips render_clips reports as finished
    ("ready" in its progress), so they can be downloaded while the others still render."""
    import modal

    on_progress = on_progress or mirror_progress(job)
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
                # The call keeps running on Modal: keep asking for its result.
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
                on_progress(current)
                if on_ready and current.get("ready"):
                    try:
                        on_ready(list(current["ready"]))
                    except Exception as exc:  # noqa: BLE001 - finish() fetches them again
                        log(f"Could not start a clip download: {exc}")
    finally:
        try:
            progress.pop(job.id)
        except Exception:  # noqa: BLE001
            pass


def spawn(job: Job, function, *args, **kwargs):
    """Spawn a deployed function. While it runs, job.remote_call holds the call
    (cancel_remote_call stops it on failure) and job.json "modalCallId" its id (the next
    run's sweep cancels it if this runner is killed)."""
    call = function.spawn(*args, **kwargs)
    job.remote_call = call
    call_id = getattr(call, "object_id", None)
    if isinstance(call_id, str):
        job.update(modalCallId=call_id)
    return call


def finished(job: Job) -> None:
    """The spawned call returned: nothing to cancel any more."""
    job.remote_call = None
    if job.data.get("modalCallId"):
        job.update(modalCallId=None)


def cancel_remote_call(job: Job) -> None:
    """Stop the spawned call (and its GPU containers) when this run ends without its result:
    a failure, a stop signal, or giving up on a Modal outage. Called before the volume
    folder is removed, so nothing keeps running on deleted inputs. A no-op once the call
    has returned."""
    call = getattr(job, "remote_call", None)
    if call is None:
        return
    job.remote_call = None
    try:
        call.cancel(terminate_containers=True)
        log("Cancelled the job on Modal")
        if job.data.get("modalCallId"):
            job.update(modalCallId=None)
    except Exception as exc:  # noqa: BLE001 - the next run's sweep tries again (modalCallId)
        log(f"Could not cancel the job on Modal: {exc}")


def cancel_stale_call(job: Job) -> None:
    """A previous attempt of this job whose runner was killed may still be running on Modal."""
    call_id = job.data.get("modalCallId")
    if call_id:
        log("Cancelling the previous attempt still registered on Modal")
        cancel_call_id(call_id)
        job.update(modalCallId=None)


class BackgroundCall:
    """A spawned call waited for on its own thread, so the laptop can download the video
    meanwhile. get() returns its result (or raises its error) on the calling thread."""

    def __init__(self, job: Job, call, on_progress: Callable[[dict], None],
                 on_done: Callable[[], None] | None = None):
        self.job, self.call, self.on_progress, self.on_done = job, call, on_progress, on_done
        self.result = None
        self.error: BaseException | None = None
        self.finished_at: float | None = None
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._wait, name="modal-wait", daemon=True)

    def start(self) -> "BackgroundCall":
        self.thread.start()
        return self

    def _wait(self) -> None:
        try:
            self.result = wait_for(self.call, self.job, on_progress=self.on_progress)
        except BaseException as exc:  # noqa: BLE001 - handed to get()
            self.error = exc
        finally:
            self.finished_at = time.time()
            self.done.set()
            if self.on_done is not None and self.error is None:
                try:
                    self.on_done()       # on success only: the progress strip moves on while the laptop still works
                except Exception:  # noqa: BLE001 - never lose the call's outcome over a display hook
                    pass

    @property
    def failed(self) -> bool:
        return self.done.is_set() and self.error is not None

    @property
    def nothing_to_render(self) -> bool:
        return self.done.is_set() and self.error is None and not (self.result or {}).get("moments")

    def get(self):
        while not self.done.wait(0.5):    # short waits: a stop signal is handled on this thread
            pass
        if self.error is not None:
            raise self.error
        return self.result
