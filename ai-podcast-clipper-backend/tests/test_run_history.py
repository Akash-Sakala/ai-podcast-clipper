"""Offline tests for the run history behind the stats page (run_history.py, runner.py).

The logs below are shortened copies of real runner.log files (paths and Gemini
error texts cut).

    python -m pytest tests -q
"""

import datetime as dt
import json
import pathlib
import sys
import time
import types

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import run_history  # noqa: E402
import runner  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# First version (WhisperX, one GPU class): no "Timings", no GPU name, no cost.
OLD_LOG = """\
[20:11:46] Downloading https://youtu.be/BqSxjmvXzzY
[youtube] Extracting URL: https://youtu.be/BqSxjmvXzzY
[20:12:09] Downloaded: 57 Years Apart - A Boy And a Man Talk About Life
[20:12:09] Uploading input.mp4 (80 MB) to volume ai-podcast-clipper-jobs
[20:12:26] Calling the deployed GPU class (this can take several minutes)
[20:19:32] GPU processing finished in 426s: {'clips': ['clip_0.mp4'], 'errors': []}
[20:19:41] Saved clips\\clip_0.mp4
[20:20:01] 3 clip(s) ready
"""

# Parakeet on one GPU: a full run with no clips, a re-rank that failed the next
# morning (network error), then a re-rank that worked.
SINGLE_GPU_LOG = """\
[23:40:16] Downloading https://youtu.be/qy8Gr27yLMk
[23:41:02] Downloaded: OpenAI Co-Founder (YouTube language: en-US)
[23:41:02] Uploading input.mp4 (254 MB) to volume ai-podcast-clipper-jobs
[23:41:47] Calling AiPodcastClipper.process_job (transcript=parakeet)
[23:42:25] GPU processing finished in 38s on A10
[23:42:29] No clips were produced - Gemini found no suitable moments. Timings: {'download': 46.7, 'upload': 44.8, \
'gpu_transcribe': 16.3, 'gpu_select': 3.5, 'gpu_render': 0.0, 'gpu_total': 38.1, 'total': 133.3}
[10:29:58] Uploading input.mp4 (254 MB) to volume ai-podcast-clipper-jobs
[10:30:05] Calling AiPodcastClipper.process_job (transcript=reuse)
Traceback (most recent call last):
[10:32:29] Could not clean up remote files: [Errno 11001] getaddrinfo failed
[10:36:14] Uploading input.mp4 (254 MB) to volume ai-podcast-clipper-jobs
[10:36:25] Calling AiPodcastClipper.process_job (transcript=reuse)
[10:50:30] GPU processing finished in 846s on A10
[10:51:06] Re-ranked: 5 clip(s) ready, best first (Gemini error: 429). Timings: {'rerank': True, 'upload': 10.9, \
'gpu_select': 17.9, 'gpu_render': 811.8, 'gpu_total': 845.5, 'total': 891.6}
"""
SINGLE_GPU_JOB = {"createdAt": "2026-10-23T18:10:14.523Z", "rerankedAt": "2026-10-24T05:21:06.048699Z",
                  "updatedAt": "2026-10-24T05:21:06.048699Z", "engine": {"parakeet": {"audioSeconds": 1814.1}}}

# Parallel renderer: cost in the log.
PARALLEL_LOG = """\
[14:06:40] Downloading https://youtu.be/JHo-aiePjoM
[14:09:35] Uploading input.mp4 (905 MB) to volume ai-podcast-clipper-jobs
[14:12:37] Calling process_job (transcript=parakeet)
[14:15:58] GPU processing finished in 201s on A10, about $0.117
[14:16:43] 5 clip(s) ready, best first. Timings: {'download': 180.0, 'upload': 181.9, 'gpu_transcribe': 45.0, \
'gpu_select': 69.0, 'gpu_render': 83.4, 'gpu_total': 201.1, 'total': 608.3}
[15:26:36] Uploading input.mp4 (905 MB) to volume ai-podcast-clipper-jobs
[15:26:44] Calling process_job (transcript=reuse)
[15:29:12] GPU processing finished in 148s on A10, about $0.119
[15:30:27] Re-ranked: 5 clip(s) ready, best first. Timings: {'rerank': True, 'upload': 8.2, 'gpu_select': 32.1, \
'gpu_render': 111.3, 'gpu_total': 148.2, 'total': 231.1}
"""
PARALLEL_JOB = {"createdAt": "2026-10-25T08:36:27.141Z", "rerankedAt": "2026-10-25T10:00:27.160872Z",
                "updatedAt": "2026-10-25T10:00:27.160872Z",
                "cost": {"usd": 0.1193, "gpuSeconds": 319, "gpu": "A10", "containers": 5}}


def test_old_log_steps_come_from_line_times_and_cost_is_estimated():
    [run] = run_history.parse_log(OLD_LOG, {"createdAt": "2026-10-15T14:41:43.484Z"}, IST)
    assert (run["mode"], run["status"], run["clips"], run["pipeline"]) == ("full", "done", 3, "single-gpu")
    assert run["startedAt"] == "2026-10-15T14:41:46Z" and run["finishedAt"] == "2026-10-15T14:50:01Z"
    assert run["timings"] == {"download": 23.0, "upload": 17.0, "gpu_total": 426.0, "total": 495.0}
    cost = run["cost"]
    assert cost["estimated"] and cost["gpu"] == "L40S"     # the first version ran on an L40S
    # 426 s of work + boot + 20 s scale-down, at L40S + 0.125 core + 0.125 GiB.
    assert abs(cost["usd"] - run_history.estimate_cost(426, "L40S")["usd"]) < 1e-9
    assert 0.24 < cost["usd"] < 0.25


def test_runs_cross_midnight_and_failed_attempts_are_kept():
    runs = run_history.parse_log(SINGLE_GPU_LOG, SINGLE_GPU_JOB, IST)
    assert [(r["mode"], r["status"]) for r in runs] == [("full", "no-clips"), ("rerank", "failed"), ("rerank", "done")]
    assert runs[0]["startedAt"] == "2026-10-23T18:10:16Z"
    assert runs[1]["startedAt"] == "2026-10-24T04:59:58Z"  # 10:29:58 IST the next day
    assert runs[1]["cost"] is None and runs[1]["timings"]["upload"] == 7.0
    assert runs[2]["timings"]["gpu_render"] == 811.8 and runs[2]["cost"]["gpu"] == "A10"
    assert all(r["cost"]["estimated"] for r in (runs[0], runs[2]))
    assert runs[2]["videoSeconds"] == 1814.1


def test_parallel_runs_use_logged_cost_and_job_cost_for_the_latest():
    first, rerank = run_history.parse_log(PARALLEL_LOG, PARALLEL_JOB, IST)
    assert first["pipeline"] == rerank["pipeline"] == "parallel"
    assert first["cost"] == {"usd": 0.117, "gpuSeconds": None, "gpu": "A10", "containers": None, "estimated": False}
    assert rerank["cost"] == {"usd": 0.1193, "gpuSeconds": 319, "gpu": "A10", "containers": 5, "estimated": False}
    assert rerank["mode"] == "rerank" and "download" not in rerank["timings"]
    assert first["timings"]["total"] == 608.3


def test_rerank_days_later_takes_its_date_from_the_job():
    job = {**PARALLEL_JOB, "rerankedAt": "2026-10-28T10:00:27Z", "updatedAt": "2026-10-28T10:00:27Z"}
    first, rerank = run_history.parse_log(PARALLEL_LOG, job, IST)
    assert first["startedAt"].startswith("2026-10-25") and rerank["startedAt"].startswith("2026-10-28")


def test_history_before_skips_the_run_in_progress():
    started = dt.datetime(2026, 10, 25, 15, 26, 30, tzinfo=IST).timestamp()
    before = run_history.history_before(PARALLEL_JOB, PARALLEL_LOG, started, IST)
    assert [r["mode"] for r in before] == ["full"]
    assert run_history.history_before({"runs": [{"mode": "full"}]}, PARALLEL_LOG, started) == [{"mode": "full"}]


def _job_dir(tmp_path, data, log=""):
    job_dir = tmp_path / "jobs" / "20261025-140627-9e57e4"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"id": job_dir.name, "source": "youtube", "title": "t",
                                                  "status": "done", "clips": [], **data}))
    if log:
        (job_dir / "runner.log").write_text(log, encoding="utf-8")
    return job_dir


def test_backfill_writes_runs_without_touching_updated_at(tmp_path):
    job_dir = _job_dir(tmp_path, {**PARALLEL_JOB, "updatedAt": "2026-10-25T10:00:27.160872Z"}, PARALLEL_LOG)
    assert runner.backfill_stats(tmp_path) == 0
    job = json.loads((job_dir / "job.json").read_text())
    assert len(job["runs"]) == 2 and job["updatedAt"] == "2026-10-25T10:00:27.160872Z"
    job["runs"] = [{"mode": "full"}]
    (job_dir / "job.json").write_text(json.dumps(job))
    runner.backfill_stats(tmp_path)                       # already has a history: left alone
    assert json.loads((job_dir / "job.json").read_text())["runs"] == [{"mode": "full"}]
    runner.backfill_stats(tmp_path, force=True)
    assert len(json.loads((job_dir / "job.json").read_text())["runs"]) == 2


def test_rerank_appends_to_history_seeded_from_the_log(tmp_path, monkeypatch):
    # runner.log times are the laptop's local time (IST); read them as IST on any machine.
    original = run_history.history_before
    monkeypatch.setattr(run_history, "history_before",
                        lambda job, text, started: original(job, text, started, IST))
    job_dir = _job_dir(tmp_path, {**PARALLEL_JOB, "clips": ["clip_0.mp4"],
                                  "engine": {"transcriber": "parakeet", "parakeet": {"audioSeconds": 4332.3}}},
                       PARALLEL_LOG.split("[15:26:36]")[0])     # only the first run is in the log
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
    monkeypatch.setattr(runner, "upload_input", lambda *a, **k: None)
    monkeypatch.setattr(runner, "upload_transcript", lambda *a, **k: None)

    def fake_gpu(job, max_clips, mode, timings, extra_usages=None):
        timings.update({"gpu_select": 20.0, "gpu_render": 90.0, "gpu_total": 115.0})
        return {"clips": ["clip_0.mp4"], "moments": [{"rank": 1}], "errors": [], "gpu": "A10",
                "ranking": {"valid": 1}, "cost": {"usd": 0.1, "gpuSeconds": 300, "containers": 2}}

    monkeypatch.setattr(runner, "run_gpu", fake_gpu)
    before = time.time()
    assert runner.run_rerank(job_dir, 5, keep_remote=False) == 0
    runs = json.loads((job_dir / "job.json").read_text())["runs"]
    assert [(r["mode"], r["recordedBy"]) for r in runs] == [("full", "log"), ("rerank", "runner")]
    latest = runs[-1]
    assert latest["status"] == "done" and latest["clips"] == 1 and latest["pipeline"] == "parallel"
    assert latest["cost"] == {"usd": 0.1, "gpuSeconds": 300, "gpu": "A10", "containers": 2, "estimated": False}
    assert latest["timings"]["gpu_render"] == 90.0 and "rerank" not in latest["timings"]
    assert latest["videoSeconds"] == 4332.3
    started = run_history.parse_iso(latest["startedAt"]).timestamp()
    assert before - 1 <= started <= time.time()


def test_failed_run_is_recorded(tmp_path, monkeypatch):
    job_dir = _job_dir(tmp_path, {"createdAt": "2026-10-25T08:36:27Z", "source": "upload", "status": "queued"})
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")

    class _Volume:
        def remove_file(self, *a, **k):
            pass

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(
        Volume=types.SimpleNamespace(from_name=lambda *a, **k: _Volume())))
    monkeypatch.setattr(runner, "upload_input", lambda *a, **k: None)

    def broken_gpu(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "run_gpu", broken_gpu)
    assert runner.run_job(job_dir, 5, keep_remote=False) == 1
    [run] = json.loads((job_dir / "job.json").read_text())["runs"]
    assert (run["mode"], run["status"], run["cost"]) == ("full", "failed", None)
    assert "total" in run["timings"]
