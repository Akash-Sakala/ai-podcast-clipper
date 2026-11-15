"""Offline tests for the run history behind the stats page (run_history.py, runner.py):
job.json "runs" and the stats ledger data/stats/runs.jsonl.

    python -m pytest tests -q
"""

import datetime as dt
import json
import pathlib
import shutil
import sys
import time
import types

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import jobstore  # noqa: E402
import pipeline  # noqa: E402
import run_history  # noqa: E402
import transfer  # noqa: E402

EARLIER_RUN = {"mode": "full", "status": "done", "startedAt": "2026-11-14T08:00:00Z",
               "finishedAt": "2026-11-14T08:05:00Z", "clips": 5, "videoSeconds": 4332.3,
               "timings": {"total": 300.0}, "cost": {"usd": 0.12, "gpuSeconds": 400, "gpu": "A10", "containers": 6}}


def _job_dir(tmp_path, data, log=""):
    job_dir = tmp_path / "jobs" / "20261025-140627-9e57e4"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"id": job_dir.name, "source": "youtube", "title": "t",
                                                  "status": "done", "clips": [], **data}))
    if log:
        (job_dir / "runner.log").write_text(log, encoding="utf-8")
    return job_dir


def test_rerank_appends_to_job_history_and_ledger(tmp_path, monkeypatch):
    job_dir = _job_dir(tmp_path, {"createdAt": "2026-11-14T08:00:00Z", "clips": ["clip_0.mp4"], "runs": [EARLIER_RUN],
                                  "engine": {"transcriber": "parakeet",
                                             "parakeet": {"audioSeconds": 4332.3, "realTimeFactor": 111.0}}})
    (job_dir / "clips").mkdir()
    (job_dir / "clips" / "clip_0.mp4").write_bytes(b"old")
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "transcript.json").write_text("[]")

    class _Volume:
        def read_file_into_fileobj(self, path, f):
            f.write(b"new")

        def remove_file(self, *a, **k):
            pass

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(
        Volume=types.SimpleNamespace(from_name=lambda *a, **k: _Volume())))
    monkeypatch.setattr(transfer, "upload_input", lambda *a, **k: None)

    def fake_gpu(job, max_clips, mode, timings, saver=None, **kwargs):
        timings.update({"gpu_select": 20.0, "gpu_render": 90.0, "gpu_total": 115.0})
        return {"clips": ["clip_0.mp4"], "moments": [{"rank": 1}], "errors": [], "gpu": "A10",
                "ranking": {"valid": 1}, "cost": {"usd": 0.1, "gpuSeconds": 300, "containers": 2}}

    monkeypatch.setattr(pipeline, "process_on_modal", fake_gpu)
    before = time.time()
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    runs = json.loads((job_dir / "job.json").read_text())["runs"]
    assert [r["mode"] for r in runs] == ["full", "rerank"]
    latest = runs[-1]
    assert latest["status"] == "done" and latest["clips"] == 1
    assert latest["cost"] == {"usd": 0.1, "gpuSeconds": 300, "gpu": "A10", "containers": 2, "stages": None,
                              "rates": "built-in"}
    assert latest["timings"]["gpu_render"] == 90.0 and "rerank" not in latest["timings"]
    assert latest["videoSeconds"] == 4332.3
    started = dt.datetime.fromisoformat(latest["startedAt"].replace("Z", "+00:00")).timestamp()
    assert before - 1 <= started <= time.time()

    # The ledger gets only the new run (the earlier one was recorded when it ran).
    [line] = run_history.read_ledger(tmp_path)
    assert line["jobId"] == job_dir.name and line["title"] == "t" and line["source"] == "youtube"
    assert line["mode"] == "rerank" and line["realTimeFactor"] == 111.0
    assert {k: line[k] for k in latest} == latest
    assert latest["usdInr"] is None and latest["maxClips"] == 5 and len(latest["promptVersion"]) == 8
    assert "clipSources" not in latest                   # no Most replayed or SponsorBlock data


def test_failed_run_is_recorded(tmp_path, monkeypatch):
    job_dir = _job_dir(tmp_path, {"createdAt": "2026-10-25T08:36:27Z", "source": "upload", "status": "queued"})
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")

    class _Volume:
        def remove_file(self, *a, **k):
            pass

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(
        Volume=types.SimpleNamespace(from_name=lambda *a, **k: _Volume())))
    monkeypatch.setattr(transfer, "upload_input", lambda *a, **k: None)

    def broken_gpu(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline, "process_on_modal", broken_gpu)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    [run] = json.loads((job_dir / "job.json").read_text())["runs"]
    assert (run["mode"], run["status"], run["cost"]) == ("full", "failed", None)
    assert "total" in run["timings"]
    [line] = run_history.read_ledger(tmp_path)
    assert (line["status"], line["source"]) == ("failed", "upload")



def test_ledger_outlives_the_job_folder(tmp_path):
    job_dir = _job_dir(tmp_path, {"meta": {"duration": 1800}})
    job = jobstore.Job(job_dir)
    runs = run_history.record_run(job, mode="full", status="done", started=time.time() - 60,
                             timings={"total": 60.0, "rerank": True}, cost={"usd": 0.05}, clips=["clip_0.mp4"])
    assert len(runs) == 1 and runs[0]["videoSeconds"] == 1800.0
    shutil.rmtree(job_dir)
    [line] = run_history.read_ledger(tmp_path)
    assert line["jobId"] == job_dir.name and line["cost"] == {"usd": 0.05} and line["clips"] == 1
    assert line["timings"] == {"total": 60.0}


def test_ledger_skips_broken_lines_and_appends_in_order(tmp_path):
    path = run_history.append_ledger(tmp_path, {"jobId": "a", "startedAt": "2026-11-14T08:00:00Z"})
    with open(path, "a", encoding="utf-8", newline="\n") as f:   # LF, like the runner
        f.write('{"jobId": "half-written\n\n[1, 2]\n{"no": "job"}\n')
    run_history.append_ledger(tmp_path, {"jobId": "b", "startedAt": "2026-11-14T09:00:00Z", "title": "Ünïcode"})
    assert [r["jobId"] for r in run_history.read_ledger(tmp_path)] == ["a", "b"]
    assert path.read_bytes().count(b"\r") == 0                           # LF lines, also on Windows
    assert run_history.read_ledger(tmp_path / "empty") == []


def test_a_broken_ledger_never_fails_the_run(tmp_path, monkeypatch):
    job = jobstore.Job(_job_dir(tmp_path, {"runs": [EARLIER_RUN]}))

    def full_disk(*a, **k):
        raise OSError("No space left on device")

    monkeypatch.setattr(run_history, "append_ledger", full_disk)
    runs = run_history.record_run(job, mode="rerank", status="done", started=time.time(), timings={})
    assert [r["mode"] for r in runs] == ["full", "rerank"]
