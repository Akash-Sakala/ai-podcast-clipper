"""Offline tests for the run's failure paths: cancelling the Modal call, the volume sweep,
Settings switches reaching Gemini, saving clips (.part, retries, keeping them on Modal
for Try again), the re-rank swap, job.json writes, the Modal names, the cost inputs and
the run diagnostics.

    python -m pytest tests -q
"""

import inspect
import json
import os
import pathlib
import signal
import subprocess
import sys
import threading
import types

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import clipper_names  # noqa: E402
import cloud  # noqa: E402
import costs  # noqa: E402
import jobstore  # noqa: E402
import moments  # noqa: E402
import pipeline  # noqa: E402
import replay  # noqa: E402
import run_history  # noqa: E402
import settings  # noqa: E402
import transfer  # noqa: E402

JOB_ID = "20261114-100000-abcdef"


def make_job(tmp_path, job_id=JOB_ID, **fields):
    job_dir = tmp_path / "jobs" / job_id
    job_dir.mkdir(parents=True)
    data = {"id": job_id, "source": "upload", "title": "talk.mp4", "youtubeUrl": None, "status": "queued",
            "clips": [], **fields}
    (job_dir / "job.json").write_text(json.dumps(data), encoding="utf-8")
    return job_dir


def read(job_dir):
    return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))


class FakeVolume:
    """The jobs volume: files as {path: bytes}; reads can be made to fail."""

    def __init__(self, files=None, entries=None):
        self.files = dict(files or {})
        self.entries = entries or []
        self.removed = []
        self.reads = []
        self.fail = {}                       # path -> number of failures left (or -1: always)

    def read_file_into_fileobj(self, path, f):
        self.reads.append(path)
        left = self.fail.get(path, 0)
        if left:
            self.fail[path] = left - 1 if left > 0 else -1
            f.write(b"partial")
            raise ConnectionError("dropped")
        if path not in self.files:
            raise FileNotFoundError(path)
        f.write(self.files[path])

    def remove_file(self, path, recursive=False):
        self.removed.append(path)

    def listdir(self, path, recursive=False):
        if path == "/":
            return self.entries
        return [e for e in self.entries if getattr(e, "path", "").startswith(path.strip("/") + "/")]


def entry(name, mtime, kind=2):
    return types.SimpleNamespace(path=name, mtime=mtime, type=kind)


def install_fake_modal(monkeypatch, volume, calls=None):
    fake = types.SimpleNamespace(
        Volume=types.SimpleNamespace(from_name=lambda *a, **k: volume),
        FunctionCall=types.SimpleNamespace(from_id=lambda call_id: types.SimpleNamespace(
            cancel=lambda terminate_containers=False: (calls if calls is not None else []).append(
                ("cancel-id", call_id, terminate_containers)))))
    monkeypatch.setitem(sys.modules, "modal", fake)
    return fake


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(pipeline, "refresh_modal_bill", lambda *a, **k: None)
    monkeypatch.setattr(transfer, "save_clip_poster", lambda clip: True)
    monkeypatch.setattr(transfer, "upload_input", lambda *a, **k: None)
    monkeypatch.setattr(transfer.time, "sleep", lambda s: None)


# --------------------------------------------------------------------------- #
# 1. The Modal call is cancelled before the volume folder goes; the sweep
# --------------------------------------------------------------------------- #

class FakeCall:
    object_id = "fc-123"

    def __init__(self, order):
        self.order = order

    def cancel(self, terminate_containers=False):
        self.order.append(("cancel", terminate_containers))


def gpu_that(order, outcome):
    """Stand-in for a phase run: registers the call like the real one, then raises `outcome`."""

    def run(job, max_clips, mode, timings, saver=None, **kwargs):
        job.remote_call = FakeCall(order)
        job.update(modalCallId="fc-123")
        raise outcome

    return run


@pytest.mark.parametrize("outcome", [RuntimeError("Modal outage"), KeyboardInterrupt(), SystemExit(143)])
def test_a_failed_or_stopped_run_cancels_the_modal_call_before_removing_its_folder(tmp_path, monkeypatch, outcome):
    job_dir = make_job(tmp_path)
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")
    order = []
    volume = FakeVolume()
    volume.remove_file = lambda path, recursive=False: order.append(("remove", path))
    install_fake_modal(monkeypatch, volume)
    monkeypatch.setattr(pipeline, "process_on_modal", gpu_that(order, outcome))
    if isinstance(outcome, Exception):
        assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    else:
        with pytest.raises(type(outcome)):
            pipeline.run_job(job_dir, 5, keep_remote=False)
    assert order == [("cancel", True), ("remove", f"/{JOB_ID}")]
    data = read(job_dir)
    assert data["status"] == "failed" and data.get("modalCallId") is None
    if not isinstance(outcome, Exception):
        assert data["message"] == "Stopped"


def test_a_call_that_could_not_be_cancelled_is_cancelled_by_the_next_run(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, modalCallId="fc-old", status="failed")
    calls = []
    install_fake_modal(monkeypatch, FakeVolume(), calls)
    cloud.cancel_stale_call(jobstore.Job(job_dir))
    assert calls == [("cancel-id", "fc-old", True)] and read(job_dir).get("modalCallId") is None


def test_modal_call_cancel_has_the_terminate_containers_option():
    modal = pytest.importorskip("modal")
    assert "terminate_containers" in inspect.signature(modal.FunctionCall.cancel).parameters
    assert callable(getattr(modal.FunctionCall, "from_id", None))


def test_sweep_removes_only_folders_no_run_can_use(tmp_path, monkeypatch):
    now = 1_800_000_000.0
    day = 24 * 3600
    jobs = {
        "20261101-000000-aaaaa1": dict(status="processing", pid=os.getpid()),       # running here: keep
        "20261101-000000-aaaaa2": dict(status="queued"),                             # about to start: keep
        "20261101-000000-aaaaa3": dict(status="done"),                               # finished: remove now
        "20261101-000000-aaaaa4": dict(status="processing", pid=99999999, modalCallId="fc-9"),  # killed runner
        "20261101-000000-aaaaa5": dict(status="failed", pendingSave={"mode": "full"}),  # kept clips, recent
        "20261101-000000-aaaaa6": dict(status="failed", pendingSave={"mode": "full"}),  # kept clips, old
    }
    for job_id, fields in jobs.items():
        make_job(tmp_path, job_id, **fields)
    entries = [entry(name, now - 3600) for name in jobs]
    entries[5] = entry("20261101-000000-aaaaa6", now - 2 * day)
    entries += [entry("20261101-000000-bbbbb1", now - 2 * day),      # unknown job, old: remove
                entry("20261101-000000-bbbbb2", now - 3600),         # unknown job, recent: keep
                entry(JOB_ID, now - 9 * day),                        # the current job: never
                entry("something-else", now - 9 * day),              # not ours: never
                entry("20261101-000000-ccccc1", now - 9 * day, kind=1)]  # a file: never
    volume = FakeVolume(entries=entries)
    calls = []
    install_fake_modal(monkeypatch, volume, calls)
    removed = transfer.sweep_remote(volume, tmp_path, current_job=JOB_ID, now=now)
    assert sorted(removed) == ["20261101-000000-aaaaa3", "20261101-000000-aaaaa4", "20261101-000000-aaaaa6",
                               "20261101-000000-bbbbb1"]
    assert calls == [("cancel-id", "fc-9", True)]
    assert read(tmp_path / "jobs" / "20261101-000000-aaaaa6").get("pendingSave") is None
    assert read(tmp_path / "jobs" / "20261101-000000-aaaaa5")["pendingSave"] == {"mode": "full"}


def test_sweep_uses_the_newest_file_in_a_folder_and_never_raises(tmp_path):
    now = 1_800_000_000.0
    name = "20261101-000000-ddddd1"
    volume = FakeVolume(entries=[entry(name, now - 3 * 86400), entry(f"{name}/clips/clip_0.mp4", now - 60, 1)])
    assert transfer.sweep_remote(volume, tmp_path, now=now) == []          # a file inside changed a minute ago
    broken = FakeVolume()
    broken.listdir = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network"))
    assert transfer.sweep_remote(broken, tmp_path, now=now) == []


def test_pid_alive_never_kills_and_knows_this_process():
    assert jobstore.pid_alive(os.getpid()) is True
    assert jobstore.pid_alive(99999999) is False
    assert jobstore.pid_alive(None) is False and jobstore.pid_alive("x") is False


@pytest.mark.parametrize("handle, error, exit_code, alive", [
    (4242, 0, 259, True),        # STILL_ACTIVE
    (4242, 0, 0, False),         # exited
    (0, 5, None, True),          # OpenProcess: access denied, so the process exists
    (0, 87, None, False),        # OpenProcess: invalid parameter, no such process
])
def test_pid_alive_on_windows_declares_the_prototypes_and_reads_the_exit_code(
        monkeypatch, handle, error, exit_code, alive):
    import ctypes
    from ctypes import wintypes
    from unittest import mock

    class WindowsOs:
        name = "nt"

        def __getattr__(self, attr):
            return getattr(os, attr)

    k32 = mock.MagicMock()
    k32.OpenProcess.return_value = handle

    def exit_code_of(h, out):
        out.value = exit_code
        return 1

    k32.GetExitCodeProcess.side_effect = exit_code_of
    monkeypatch.setattr(jobstore, "os", WindowsOs())
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: k32, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(ctypes, "byref", lambda obj: obj)
    assert jobstore.pid_alive(1234) is alive
    assert k32.OpenProcess.restype is wintypes.HANDLE                       # 64-bit handles are not truncated
    assert k32.OpenProcess.argtypes == [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    assert k32.CloseHandle.argtypes == [wintypes.HANDLE] and k32.GetExitCodeProcess.argtypes[0] is wintypes.HANDLE
    assert k32.CloseHandle.called is bool(handle)                           # a handle that was opened is closed


def test_stop_signals_become_system_exit(monkeypatch):
    import runner

    installed = {}
    monkeypatch.setattr(runner.signal, "signal", lambda sig, handler: installed.setdefault(sig, handler))
    names = runner.stop_on_signals()
    assert "SIGTERM" in names
    with pytest.raises(SystemExit):
        installed[signal.SIGTERM](signal.SIGTERM, None)


# --------------------------------------------------------------------------- #
# 2. Settings switches decide what Gemini gets
# --------------------------------------------------------------------------- #

RECORD = {"available": True, "heatmap": [[0, 30, 0.5]], "peaks": [{"start": 100, "end": 130, "rank": 1}],
          "chapters": [{"start": 0, "title": "Intro"}],
          "sponsor": {"segments": [{"start": 10, "end": 40, "category": "sponsor"}], "highlight": 300.0}}


@pytest.mark.parametrize("most_replayed, sponsorblock", [(True, True), (True, False), (False, True), (False, False)])
def test_switched_off_sources_never_reach_gemini(tmp_path, monkeypatch, most_replayed, sponsorblock):
    job = jobstore.Job(make_job(tmp_path, source="youtube", replay=RECORD))
    monkeypatch.setattr(settings, "MOST_REPLAYED", most_replayed)
    monkeypatch.setattr(settings, "SPONSORBLOCK", sponsorblock)
    signals = pipeline.gemini_context(job).get("replay")
    if not (most_replayed or sponsorblock):
        bare = jobstore.Job(make_job(tmp_path, "20261114-100000-000000", source="youtube"))
        assert pipeline.gemini_context(job) == pipeline.gemini_context(bare)   # same as no graph at all
        return
    assert bool(signals["heatmap"]) is most_replayed and bool(signals["peaks"]) is most_replayed
    assert bool(signals["chapters"]) is most_replayed
    assert bool(signals["skips"]) is sponsorblock and (signals["highlight"] is not None) is sponsorblock


def test_ranking_context_defaults_are_unchanged():
    assert replay.ranking_context(RECORD, 0.4) == replay.ranking_context(RECORD, 0.4, most_replayed=True,
                                                                        sponsorblock=True)


# --------------------------------------------------------------------------- #
# 3. Saving clips: .part files, retries, clips kept on Modal for Try again
# --------------------------------------------------------------------------- #

def test_a_download_goes_through_a_part_file_and_is_retried(tmp_path):
    volume = FakeVolume({"/j/clips/clip_0.mp4": b"clip"})
    volume.fail["/j/clips/clip_0.mp4"] = 2
    waits = []
    target = tmp_path / "clips" / "clip_0.mp4"
    transfer.download_file(volume, "/j/clips/clip_0.mp4", target, sleep=waits.append)
    assert target.read_bytes() == b"clip" and waits == [2, 4]
    assert not list(target.parent.glob("*.part"))
    with pytest.raises(FileNotFoundError):
        transfer.download_file(volume, "/j/clips/missing.mp4", tmp_path / "m.mp4", sleep=waits.append)
    assert waits == [2, 4] and not (tmp_path / "m.mp4").exists()


def gpu_result(clips=("clip_0.mp4", "clip_1.mp4")):
    return {"clips": list(clips), "moments": [{"start": 10.0 * i, "end": 10.0 * i + 40, "rank": i + 1}
                                              for i in range(len(clips))],
            "errors": [], "gpu": "A10", "ranking": {"valid": len(clips)}, "timings": {"render": 50.0},
            "cost": {"usd": 0.1, "gpuSeconds": 200, "containers": 3},
            "clipTimings": [{"clip": c, "seconds": 40.0, "enterSeconds": 9.0, "steps": {"cut": 1.0}}
                            for c in clips],
            "usages": [{"container": "t", "gpu": "NVIDIA A10", "seconds": 30, "enterSeconds": 12.5,
                        "stage": "transcribe"},
                       {"container": "r", "gpu": "NVIDIA A10", "seconds": 40, "enterSeconds": 9.0, "stage": "render"},
                       {"container": "r", "gpu": "NVIDIA A10", "seconds": 40, "enterSeconds": 0.0, "stage": "render"}],
            "orchestratorSeconds": 100.0, "orchestrator": {"cpu": 0.2, "memoryGiB": 0.3}}


def remote_files(clips=("clip_0.mp4", "clip_1.mp4")):
    files = {f"/{JOB_ID}/clips/{c}": f"new {c}".encode() for c in clips}
    files[f"/{JOB_ID}/moments.json"] = b"[1]"
    files[f"/{JOB_ID}/transcript.json"] = b"[2]"
    return files


def test_a_save_failure_keeps_the_clips_on_modal_and_try_again_downloads_them(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")
    volume = FakeVolume(remote_files())
    volume.fail[f"/{JOB_ID}/clips/clip_1.mp4"] = -1            # the network keeps failing
    install_fake_modal(monkeypatch, volume)
    gpu_runs = []
    monkeypatch.setattr(pipeline, "process_on_modal", lambda *a, **k: gpu_runs.append(1) or gpu_result())
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    data = read(job_dir)
    assert data["status"] == "failed" and data["failedStage"] == "saving"
    assert data["pendingSave"]["mode"] == "full" and data["pendingSave"]["result"]["clips"] == ["clip_0.mp4",
                                                                                                 "clip_1.mp4"]
    assert volume.removed == [f"/{JOB_ID}/{name}" for name in transfer.REMOTE_MEDIA]   # the clips stay
    assert (job_dir / "input.mp4").exists()                    # Try again is allowed for an upload
    assert not (job_dir / "moments.json").exists()

    volume.fail.clear()                                        # Try again
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert gpu_runs == [1]                                     # no second GPU run
    assert data["status"] == "done" and data["clips"] == ["clip_0.mp4", "clip_1.mp4"]
    assert data.get("pendingSave") is None and data["resumedFrom"]
    assert (job_dir / "clips" / "clip_1.mp4").read_bytes() == b"new clip_1.mp4"
    assert (job_dir / "moments.json").read_bytes() == b"[1]"
    assert data["cost"]["usd"] > 0 and data["runs"][-1]["status"] == "done"
    assert volume.removed[-1] == f"/{JOB_ID}" and not (job_dir / "input.mp4").exists()


def test_try_again_runs_the_gpu_when_the_kept_clips_are_gone(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, status="failed", pendingSave=transfer.pending_record("full", gpu_result(), {}, 5))
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")
    volume = FakeVolume({})                                    # swept meanwhile
    install_fake_modal(monkeypatch, volume)
    gpu_runs = []

    def gpu(*a, **k):
        gpu_runs.append(1)
        volume.files.update(remote_files())
        return gpu_result()

    monkeypatch.setattr(pipeline, "process_on_modal", gpu)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert gpu_runs == [1] and read(job_dir)["status"] == "done" and "resumedFrom" not in read(job_dir)


def test_clips_reported_ready_are_downloaded_while_others_render(tmp_path, monkeypatch):
    job = jobstore.Job(make_job(tmp_path))
    volume = FakeVolume(remote_files())
    saver = transfer.ClipSaver(job, volume, job.dir / "clips")
    states = [{"stage": "rendering", "done": 1, "total": 2, "ready": ["clip_0.mp4"]},
              {"stage": "rendering", "done": 2, "total": 2, "ready": ["clip_0.mp4", "clip_1.mp4"]}]

    class Call:
        polls = 0

        def get(self, timeout=None):
            Call.polls += 1
            if Call.polls <= len(states):
                raise TimeoutError()
            return gpu_result()

    fake = types.SimpleNamespace(
        Dict=types.SimpleNamespace(from_name=lambda *a, **k: types.SimpleNamespace(
            get=lambda key: states[min(Call.polls, len(states)) - 1], pop=lambda key: None)),
        exception=types.SimpleNamespace(TimeoutError=type("T", (Exception,), {}),
                                        ConnectionError=type("C", (Exception,), {})))
    monkeypatch.setitem(sys.modules, "modal", fake)
    monkeypatch.setattr(cloud.time, "sleep", lambda s: None)
    result = cloud.wait_for(Call(), job, on_ready=saver.fetch)
    assert set(saver.futures) == {"clip_0.mp4", "clip_1.mp4"}       # started before the call returned
    assert "ready" not in read(job.dir)["progress"]                  # job.json progress keeps its keys
    assert transfer.download_outputs(job, volume, result, saver=saver) == ["clip_0.mp4", "clip_1.mp4"]
    assert volume.reads.count(f"/{JOB_ID}/clips/clip_0.mp4") == 1    # not downloaded twice
    assert (job.dir / "moments.json.new").exists() and not (job.dir / "moments.json").exists()
    transfer.commit_extras(job)
    assert (job.dir / "moments.json").read_bytes() == b"[1]"


# --------------------------------------------------------------------------- #
# 4. Re-rank swap on Windows
# --------------------------------------------------------------------------- #

def rerank_job_dir(tmp_path, old=("clip_0", "clip_1", "clip_2")):
    job_dir = make_job(tmp_path, source="upload", status="done", clips=[f"{c}.mp4" for c in old],
                       engine={"transcriber": "parakeet"})
    (job_dir / "clips").mkdir()
    for c in old:
        (job_dir / "clips" / f"{c}.mp4").write_bytes(b"old " + c.encode())
        (job_dir / "clips" / f"{c}.jpg").write_bytes(b"old poster")
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "transcript.json").write_text("[]")
    (job_dir / "moments.json").write_text('["old"]')
    return job_dir


def test_rerank_swaps_file_by_file_and_drops_old_extra_clips(tmp_path, monkeypatch):
    job_dir = rerank_job_dir(tmp_path)
    install_fake_modal(monkeypatch, FakeVolume(remote_files()))
    monkeypatch.setattr(pipeline, "process_on_modal", lambda *a, **k: gpu_result())
    monkeypatch.setattr(pipeline, "fetch_replay", lambda *a, **k: None)
    renamed_dirs = []
    real_replace = os.replace

    def replace(src, dst):
        if pathlib.Path(src).is_dir():
            renamed_dirs.append(src)
        return real_replace(src, dst)

    monkeypatch.setattr(jobstore.os, "replace", replace)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert renamed_dirs == []                                          # never renames the clips folder
    assert sorted(p.name for p in (job_dir / "clips").iterdir()) == ["clip_0.mp4", "clip_1.mp4"]
    assert (job_dir / "clips" / "clip_0.mp4").read_bytes() == b"new clip_0.mp4"
    assert (job_dir / "moments.json").read_text() == "[1]"
    assert not (job_dir / "clips_new").exists() and not (job_dir / "clips_previous").exists()


def test_a_locked_clip_keeps_every_old_clip_and_the_old_moments(tmp_path, monkeypatch):
    job_dir = rerank_job_dir(tmp_path)
    volume = FakeVolume(remote_files())
    install_fake_modal(monkeypatch, volume)
    monkeypatch.setattr(pipeline, "process_on_modal", lambda *a, **k: gpu_result())
    monkeypatch.setattr(pipeline, "fetch_replay", lambda *a, **k: None)
    monkeypatch.setattr(jobstore.time, "sleep", lambda s: None)
    real_replace = os.replace

    def replace(src, dst):
        if pathlib.Path(src).name == "clip_1.mp4" and pathlib.Path(src).parent.name == "clips":
            raise PermissionError("in use by the web app")             # clip_1 is being streamed
        return real_replace(src, dst)

    monkeypatch.setattr(jobstore.os, "replace", replace)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 1
    data = read(job_dir)
    assert data["status"] == "done" and data["message"] == "Re-rank failed - previous clips kept"
    for c in ("clip_0", "clip_1", "clip_2"):
        assert (job_dir / "clips" / f"{c}.mp4").read_bytes() == b"old " + c.encode()
    assert (job_dir / "moments.json").read_text() == '["old"]' and not (job_dir / "moments.json.new").exists()
    assert data["pendingSave"]["mode"] == "rerank"                     # the next re-rank downloads them
    assert volume.removed == [f"/{JOB_ID}/{name}" for name in transfer.REMOTE_MEDIA]

    monkeypatch.setattr(jobstore.os, "replace", real_replace)
    monkeypatch.setattr(pipeline, "process_on_modal", lambda *a, **k: pytest.fail("rendered again"))
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert (job_dir / "clips" / "clip_1.mp4").read_bytes() == b"new clip_1.mp4"
    assert read(job_dir).get("pendingSave") is None


def test_replace_file_retries_a_locked_target(tmp_path, monkeypatch):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("new")
    dst.write_text("old")
    attempts = []
    real_replace = os.replace

    def flaky(a, b):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError("locked")
        return real_replace(a, b)

    monkeypatch.setattr(jobstore.os, "replace", flaky)
    jobstore.replace_file(src, dst, sleep=lambda s: None)
    assert dst.read_text() == "new" and len(attempts) == 3


# --------------------------------------------------------------------------- #
# 5. job.json writes from several threads
# --------------------------------------------------------------------------- #

def test_concurrent_job_updates_never_collide(tmp_path):
    job = jobstore.Job(make_job(tmp_path))
    errors = []

    def writer(n):
        try:
            for i in range(60):
                if n % 2:
                    jobstore.set_progress(job, "uploading", fraction=i / 60)
                else:
                    job.update(**{f"field{n}": i})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    data = read(job.dir)
    assert data["field0"] == data["field2"] == data["field4"] == 59
    assert not list(job.dir.glob("*.tmp"))


def test_a_job_json_that_stays_locked_is_logged_not_raised(tmp_path, monkeypatch):
    job = jobstore.Job(make_job(tmp_path))
    lines = []
    monkeypatch.setattr(jobstore, "log", lines.append)
    monkeypatch.setattr(jobstore.time, "sleep", lambda s: None)
    monkeypatch.setattr(jobstore.os, "replace", lambda a, b: (_ for _ in ()).throw(PermissionError("locked")))
    job.data["x"] = 1
    assert job.save() is False
    assert "Could not save job.json" in lines[0] and not list(job.dir.glob("*.tmp"))


def test_temp_names_are_unique_per_process_and_thread(tmp_path, monkeypatch):
    job = jobstore.Job(make_job(tmp_path))
    names = []
    real_replace = os.replace
    monkeypatch.setattr(jobstore.os, "replace", lambda a, b: (names.append(pathlib.Path(a).name), real_replace(a, b)))
    job.save()
    job.save()
    assert len(set(names)) == 2 and all(n.startswith(f"job.json.{os.getpid()}.") for n in names)


# --------------------------------------------------------------------------- #
# 9. Settings forwarded to the containers, Modal names
# --------------------------------------------------------------------------- #

def test_thinking_level_means_the_same_on_the_laptop_and_in_the_container():
    for laptop_env in ({}, {"GEMINI_THINKING_LEVEL": ""}, {"GEMINI_THINKING_LEVEL": "low"},
                       {"GEMINI_THINKING_LEVEL": "HIGH"}):
        container_env = settings.container_settings(laptop_env)
        assert moments.thinking_level(container_env) == moments.thinking_level(laptop_env)
    assert moments.thinking_level({}) == "low"
    assert moments.thinking_level({"GEMINI_THINKING_LEVEL": ""}) is None
    assert settings.container_settings({"GEMINI_THINKING_LEVEL": ""})["GEMINI_THINKING_LEVEL"] == "default"


def test_detect_batch_and_app_name_reach_the_containers():
    sent = settings.container_settings({"DETECT_BATCH": "32", "CLIPPER_APP": "clipper-test", "MIN_CLIPS": ""})
    assert sent == {"DETECT_BATCH": "32", "CLIPPER_APP": "clipper-test"}


def test_modal_names_derive_from_one_setting():
    assert clipper_names.names({}) == {"app": "ai-podcast-clipper", "volume": "ai-podcast-clipper-jobs",
                                       "progress": "ai-podcast-clipper-progress",
                                       "secret": "ai-podcast-clipper-secret"}
    test = clipper_names.names({"CLIPPER_APP": "clipper-test"})
    assert test == {"app": "clipper-test", "volume": "clipper-test-jobs", "progress": "clipper-test-progress",
                    "secret": "ai-podcast-clipper-secret"}
    assert clipper_names.names({"CLIPPER_SECRET": "other"})["secret"] == "other"
    laptop = {"CLIPPER_APP": "clipper-test", "CLIPPER_SECRET": "s2"}
    assert clipper_names.names(settings.container_settings(laptop)) == clipper_names.names(laptop)


def test_main_uses_the_names_and_ships_the_module_to_the_containers():
    pytest.importorskip("modal")
    code = ("import main, settings, sys; print(main.app.name, main.JOBS_VOLUME, main.PROGRESS_DICT, "
            "main.SECRET_NAME, settings.CONTAINER_SETTINGS.get('CLIPPER_APP'))")
    env = {**os.environ, "CLIPPER_APP": "clipper-test"}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert out.stdout.split() == ["clipper-test", "clipper-test-jobs", "clipper-test-progress",
                                  "ai-podcast-clipper-secret", "clipper-test"]
    source = (BACKEND / "main.py").read_text(encoding="utf-8")
    shipped = source[source.index(".add_local_python_source("):].split(")")[0]
    assert '"clipper_names"' in shipped and '"settings"' in shipped
    for module in ("pipeline.py", "transfer.py", "benchmark_gpu.py", "main.py"):
        text = (BACKEND / module).read_text(encoding="utf-8")
        assert "clipper_names" in text and '"ai-podcast-clipper-jobs"' not in text, module


# --------------------------------------------------------------------------- #
# 11. Cost inputs, 13. run diagnostics
# --------------------------------------------------------------------------- #

def test_orchestrator_is_priced_with_what_it_requested_or_used():
    base = costs.estimate([], orchestrator_seconds=100)
    assert base["usd"] == round(100 * (0.125 * costs.CPU_CORE_PRICE_PER_S + 0.125 * costs.MEMORY_GIB_PRICE_PER_S), 4)
    used = costs.estimate([], orchestrator_seconds=1000, orchestrator={"cpu": 1.0, "memoryGiB": 2.0})
    assert used["usd"] == round(1000 * (costs.CPU_CORE_PRICE_PER_S + 2 * costs.MEMORY_GIB_PRICE_PER_S), 4)


def test_main_requests_the_resources_the_estimate_prices():
    pytest.importorskip("modal")
    import main

    assert main.PROCESS_JOB_CPU == main.TRANSCRIBER_CPU == costs.MODAL_DEFAULT_CPU
    assert main.PROCESS_JOB_MEMORY_MB == main.TRANSCRIBER_MEMORY_MB == costs.MODAL_DEFAULT_MEMORY_MB
    usage = main.measured(0.125, 0.125, seconds=10.0, cpu_used=25.0)
    assert usage["cpu"] == 2.5 and usage["memoryGiB"] >= 0.125          # billed: max(request, use)
    assert main.measured(4.0, 4.0, seconds=10.0, cpu_used=1.0)["cpu"] == 4.0


def test_a_finished_run_keeps_clip_timings_and_cold_starts(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")
    install_fake_modal(monkeypatch, FakeVolume(remote_files()))
    monkeypatch.setattr(pipeline, "process_on_modal", lambda *a, **k: gpu_result())
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert [t["clip"] for t in data["clipTimings"]] == ["clip_0.mp4", "clip_1.mp4"]
    assert data["coldStarts"] == [{"stage": "transcribe", "enterSeconds": 12.5, "gpu": "NVIDIA A10"},
                                  {"stage": "render", "enterSeconds": 9.0, "gpu": "NVIDIA A10"}]
    [line] = run_history.read_ledger(tmp_path)
    assert line["clipTimings"] == data["clipTimings"] and line["coldStarts"] == data["coldStarts"]
    assert {"usd", "gpuSeconds", "stages"} <= set(line["cost"])        # existing keys unchanged
