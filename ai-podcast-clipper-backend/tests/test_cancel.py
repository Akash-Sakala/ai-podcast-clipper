"""Cancel from the app (cancel.py) and the data folder outside the repo (jobstore.default_data_dir)."""

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

import cancel
import jobstore
import transfer


def make_job(tmp_path, **fields) -> pathlib.Path:
    job_dir = tmp_path / "data" / "jobs" / "20261128-120000-abcdef"
    job_dir.mkdir(parents=True)
    data = {"id": job_dir.name, "source": "youtube", "title": "t", "status": "processing", "mode": "full",
            "message": "Rendering", "error": None, "clips": [], "pid": 4242,
            "progress": {"stage": "rendering", "done": 1, "total": 5},
            "modalCallId": "fc-parent", "modalChildCallIds": ["fc-a", "fc-b"],
            "liveClips": [{"name": "clip_0.mp4"}], "plannedClips": [{"name": "clip_0.mp4"}],
            "createdAt": "2026-11-28T06:00:00Z", "updatedAt": "2026-11-28T06:00:00Z"}
    data.update(fields)
    (job_dir / "job.json").write_text(json.dumps(data), encoding="utf-8")
    return job_dir


class FakeVolume:
    def __init__(self):
        self.removed = []

    def remove_file(self, path, recursive=False):
        self.removed.append(path)


@pytest.fixture
def calls(monkeypatch):
    cancelled = []
    monkeypatch.setattr(transfer, "cancel_call_id", lambda call_id: cancelled.append(call_id) or True)
    return cancelled


def run_cancel(job_dir, volume=None, stop_ok=True, is_runner=True, alive=True):
    stopped = []
    volume = volume or FakeVolume()

    def stop(pid):
        stopped.append(pid)
        return stop_ok

    code = cancel.cancel_job(job_dir, open_volume=lambda: volume, stop=stop,
                             runner_check=lambda pid: is_runner, alive=lambda pid: alive, sleep=lambda s: None)
    return code, stopped, volume, json.loads((job_dir / "job.json").read_text(encoding="utf-8"))


def test_cancel_stops_the_runner_cancels_every_modal_call_and_cleans_up(tmp_path, calls):
    job_dir = make_job(tmp_path)
    (job_dir / "moments.json.new").write_text("[]")
    (job_dir / "job.json.4242.1.abcd.tmp").write_text("{")
    code, stopped, volume, data = run_cancel(job_dir)
    assert code == 0 and stopped == [4242]
    assert calls == ["fc-parent", "fc-a", "fc-b"]
    assert volume.removed == [f"/{job_dir.name}"]
    assert data["status"] == "failed" and data["message"] == "Cancelled" and data["error"] == "Cancelled"
    assert data["cancelledAt"] and data["failedStage"] == "rendering"
    for gone in ("progress", "pid", "modalCallId", "modalChildCallIds", "liveClips", "plannedClips"):
        assert data[gone] is None
    assert not (job_dir / "moments.json.new").exists()
    assert not list(job_dir.glob("job.json.*.tmp"))


def test_a_cancelled_rerank_keeps_the_previous_clips_and_drops_the_new_set(tmp_path, calls):
    job_dir = make_job(tmp_path, mode="rerank", clips=["clip_0.mp4"], liveClipsSet="new")
    (job_dir / "clips_new").mkdir()
    (job_dir / "clips_new" / "clip_0.mp4").write_bytes(b"x")
    code, _, _, data = run_cancel(job_dir)
    assert code == 0
    assert data["status"] == "done" and data["message"] == "Cancelled - previous clips kept"
    assert data["error"] is None and data["clips"] == ["clip_0.mp4"] and data["liveClipsSet"] is None
    assert not (job_dir / "clips_new").exists()


def test_clips_kept_on_modal_for_try_again_stay_there(tmp_path, calls):
    job_dir = make_job(tmp_path, pendingSave={"elapsed": 120})
    _, _, volume, data = run_cancel(job_dir)
    assert volume.removed == [f"/{job_dir.name}/{name}" for name in transfer.REMOTE_MEDIA]
    assert data["pendingSave"] == {"elapsed": 120}


def test_a_runner_that_does_not_stop_changes_nothing(tmp_path, calls):
    job_dir = make_job(tmp_path)
    before = (job_dir / "job.json").read_text(encoding="utf-8")
    code, _, volume, _ = run_cancel(job_dir, stop_ok=False)
    assert code == 1 and calls == [] and volume.removed == []
    assert (job_dir / "job.json").read_text(encoding="utf-8") == before


def test_a_recycled_pid_is_never_stopped(tmp_path, calls):
    job_dir = make_job(tmp_path)
    code, stopped, _, data = run_cancel(job_dir, is_runner=False)
    assert code == 0 and stopped == []
    assert data["status"] == "failed" and data["cancelledAt"]


def test_a_runner_that_just_started_is_waited_for(tmp_path, calls):
    job_dir = make_job(tmp_path, status="queued", pid=None, progress=None)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:                     # the runner writes its pid
            data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
            (job_dir / "job.json").write_text(json.dumps({**data, "pid": 777}), encoding="utf-8")

    stopped = []
    code = cancel.cancel_job(job_dir, open_volume=FakeVolume, stop=lambda pid: stopped.append(pid) or True,
                             runner_check=lambda pid: True, alive=lambda pid: True, sleep=sleep)
    assert code == 0 and stopped == [777] and len(waits) == 3


def test_a_finished_video_is_left_alone(tmp_path, calls):
    job_dir = make_job(tmp_path, status="done", clips=["clip_0.mp4"], modalCallId=None, modalChildCallIds=None)
    code, stopped, volume, data = run_cancel(job_dir, alive=False)
    assert code == 0 and stopped == [] and calls == [] and volume.removed == []
    assert data["status"] == "done" and "cancelledAt" not in data


def test_modal_calls_that_could_not_be_cancelled_stay_for_the_next_sweep(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "cancel_call_id", lambda call_id: call_id != "fc-b")
    job_dir = make_job(tmp_path)
    code, _, _, data = run_cancel(job_dir)
    assert code == 0 and data["status"] == "failed"
    assert data["modalCallId"] == "fc-parent" and data["modalChildCallIds"] == ["fc-a", "fc-b"]


def test_a_volume_that_cannot_be_reached_still_cancels(tmp_path, calls):
    job_dir = make_job(tmp_path)

    def offline():
        raise ConnectionError("no network")

    code = cancel.cancel_job(job_dir, open_volume=offline, stop=lambda pid: True,
                             runner_check=lambda pid: True, alive=lambda pid: True)
    data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    assert code == 0 and data["status"] == "failed" and calls == ["fc-parent", "fc-a", "fc-b"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_stop_process_tree_ends_a_runner_and_its_children(tmp_path):
    script = tmp_path / "runner.py"
    script.write_text("import subprocess, sys, time\n"
                      "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                      "time.sleep(60)\n")
    proc = subprocess.Popen([sys.executable, str(script)], start_new_session=True)
    try:
        time.sleep(0.5)
        assert cancel.is_runner(proc.pid)
        assert cancel.stop_process_tree(proc.pid, wait_s=5, sleep=lambda s: proc.poll() or time.sleep(s))
        proc.wait(timeout=5)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_only_a_runner_counts_as_a_runner():
    assert not cancel.is_runner(os.getpid())          # never this process
    assert not cancel.is_runner(None) and not cancel.is_runner("x") and not cancel.is_runner(0)


def test_the_data_folder_moves_out_of_the_repo_unless_only_the_repo_holds_jobs(tmp_path, monkeypatch):
    legacy = tmp_path / "repo" / "data"
    monkeypatch.setattr(jobstore, "LEGACY_DATA_DIR", legacy)
    monkeypatch.setattr(jobstore, "app_data_root", lambda: tmp_path / "local" / "ai-podcast-clipper")
    local = tmp_path / "local" / "ai-podcast-clipper" / "data"
    assert jobstore.default_data_dir() == local                   # a fresh install
    (legacy / "jobs").mkdir(parents=True)
    assert jobstore.default_data_dir() == legacy                  # an older version's jobs, not moved yet
    (local / "jobs").mkdir(parents=True)
    assert jobstore.default_data_dir() == local                   # moved
