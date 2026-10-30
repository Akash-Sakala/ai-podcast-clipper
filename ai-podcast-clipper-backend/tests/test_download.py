"""Offline tests for getting the video to Modal: the cloud download, the laptop fallback,
deleting the local copy after a run, upload fingerprints and --prune-videos.

    python -m pytest tests -q
"""

import json
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import costs  # noqa: E402
import downloader  # noqa: E402
import runner  # noqa: E402


def make_job(tmp_path, **fields):
    job_dir = tmp_path / "jobs" / fields.pop("id", "20261025-220000-abcdef")
    job_dir.mkdir(parents=True)
    data = {"id": job_dir.name, "source": "youtube", "title": "https://youtu.be/x",
            "youtubeUrl": "https://youtu.be/x", "status": "queued", "clips": [], **fields}
    (job_dir / "job.json").write_text(json.dumps(data), encoding="utf-8")
    return job_dir


def read(job_dir):
    return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# downloader.py
# --------------------------------------------------------------------------- #

def test_progress_spans_both_streams_and_never_reaches_100():
    seen = []
    progress = downloader.Progress(seen.append)
    progress({"status": "downloading", "filename": "v", "downloaded_bytes": 50, "total_bytes": 100})
    assert seen[-1] == 0.5
    progress({"status": "finished", "filename": "v", "downloaded_bytes": 100, "total_bytes": 100})
    progress({"status": "downloading", "filename": "a", "downloaded_bytes": 0, "total_bytes_estimate": 25})
    assert seen[-1] == pytest.approx(0.8)
    progress({"status": "finished", "filename": "a", "downloaded_bytes": 25})
    assert seen[-1] == 0.99                      # the merge is still to come
    progress({"status": "downloading", "filename": "b"})  # no size known: ignored
    assert len(seen) == 4


def test_blocked_errors_are_recognised():
    assert downloader.is_blocked("ERROR: [youtube] x: Sign in to confirm you're not a bot.")
    assert downloader.is_blocked("HTTP Error 429: Too Many Requests")
    assert not downloader.is_blocked("ERROR: [youtube] x: Video unavailable")
    assert not downloader.is_blocked(None)


def fake_yt_dlp(monkeypatch, outcomes):
    """yt_dlp stand-in: each extract_info call takes the next outcome (an error message or "ok")."""
    class DownloadError(Exception):
        pass

    calls = []

    class YoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            calls.append(self.options)
            outcome = outcomes[len(calls) - 1]
            out = pathlib.Path(self.options["outtmpl"].replace("%(ext)s", "mp4"))
            if outcome != "ok":
                out.with_suffix(".part").write_bytes(b"partial")
                raise DownloadError(outcome)
            out.write_bytes(b"video")
            return {"title": "A talk", "channel": "Pod", "duration": 3600, "tags": ["a"] * 40}

    module = types.SimpleNamespace(YoutubeDL=YoutubeDL, utils=types.SimpleNamespace(DownloadError=DownloadError))
    monkeypatch.setitem(sys.modules, "yt_dlp", module)
    return calls, DownloadError


def test_download_retries_a_failed_piece_with_fresh_urls(tmp_path, monkeypatch):
    calls, _ = fake_yt_dlp(monkeypatch, ["HTTP Error 403: Forbidden", "ok"])
    retries = []
    info = downloader.download("https://youtu.be/x", tmp_path, ffmpeg_location="ffmpeg", cookies_browser="firefox",
                               on_retry=lambda *a: retries.append(a[0]), sleep=lambda s: None)
    assert len(calls) == 2 and retries == [1]
    assert (tmp_path / "input.mp4").read_bytes() == b"video" and not (tmp_path / "input.part").exists()
    assert calls[0]["cookiesfrombrowser"] == ("firefox",) and calls[0]["ffmpeg_location"] == "ffmpeg"
    meta = downloader.video_meta(info)
    assert meta["title"] == "A talk" and meta["duration"] == 3600 and len(meta["tags"]) == 30


def test_download_gives_up_at_once_when_blocked(tmp_path, monkeypatch):
    calls, error = fake_yt_dlp(monkeypatch, ["Sign in to confirm you're not a bot", "ok"])
    with pytest.raises(error):
        downloader.download("https://youtu.be/x", tmp_path, sleep=lambda s: None)
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Getting the video to Modal
# --------------------------------------------------------------------------- #

def patch_transfer(monkeypatch, cloud_result):
    """Stand-ins for the Modal calls; returns a log of what ran."""
    ran = []

    def cloud(job):
        ran.append("cloud")
        return cloud_result

    def laptop(job, attempts=3):
        ran.append("laptop")
        (job.dir / "input.mp4").write_bytes(b"video")
        runner.apply_meta(job, {"title": "From the laptop"})

    monkeypatch.setattr(runner, "cloud_download", cloud)
    monkeypatch.setattr(runner, "download_youtube", laptop)
    monkeypatch.setattr(runner, "upload_input", lambda job, volume, update_status=True: ran.append("upload"))
    monkeypatch.setattr(runner.settings, "YOUTUBE_DOWNLOAD", "cloud")
    return ran


CLOUD_USAGE = {"container": "ta-1", "cpuOnly": True, "cpu": 2.0, "memoryGiB": 2.0, "seconds": 40.0, "scaledown": 5}


def test_youtube_video_downloads_on_modal_and_never_touches_the_laptop(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    ran = patch_transfer(monkeypatch, {"ok": True, "meta": {"title": "A talk", "duration": 3600},
                                       "sizeMB": 500, "usage": CLOUD_USAGE})
    job, timings, usages = runner.Job(job_dir), {}, []
    runner.put_video_on_modal(job, None, timings, usages)
    assert ran == ["cloud"] and usages == [CLOUD_USAGE]
    assert "cloud_download" in timings and "download" not in timings and "upload" not in timings
    data = read(job_dir)
    assert data["title"] == "A talk" and data["meta"]["duration"] == 3600
    assert data["videoTransfer"] == "cloud-download" and not (job_dir / "input.mp4").exists()


def test_blocked_cloud_download_falls_back_to_the_laptop(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    ran = patch_transfer(monkeypatch, {"ok": False, "error": "not a bot", "blocked": True, "usage": CLOUD_USAGE})
    job, timings, usages = runner.Job(job_dir), {}, []
    runner.put_video_on_modal(job, None, timings, usages)
    assert ran == ["cloud", "laptop", "upload"] and usages == [CLOUD_USAGE]  # the failed try is still billed
    assert {"cloud_download", "download", "upload"} <= set(timings)
    data = read(job_dir)
    assert data["videoTransfer"] == "laptop-download" and data["downloadFallback"] == "blocked"
    assert data["title"] == "From the laptop"


def test_laptop_setting_skips_the_cloud(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    ran = patch_transfer(monkeypatch, {"ok": True})
    monkeypatch.setattr(runner.settings, "YOUTUBE_DOWNLOAD", "laptop")
    runner.put_video_on_modal(runner.Job(job_dir), None, {}, [])
    assert ran == ["laptop", "upload"] and "downloadFallback" not in read(job_dir)


def test_a_video_in_the_job_folder_is_uploaded(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)                 # e.g. a job from before this change
    (job_dir / "input.mp4").write_bytes(b"video")
    ran = patch_transfer(monkeypatch, {"ok": True})
    timings = {}
    runner.put_video_on_modal(runner.Job(job_dir), None, timings, [])
    assert ran == ["upload"] and "upload" in timings and read(job_dir)["videoTransfer"] == "upload"


def test_an_upload_without_its_video_asks_for_the_file_again(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    patch_transfer(monkeypatch, {"ok": True})
    with pytest.raises(FileNotFoundError, match="add the same MP4 again"):
        runner.put_video_on_modal(runner.Job(job_dir), None, {}, [])


def test_cloud_download_reports_a_missing_deployment_instead_of_raising(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)

    def from_name(app, name):
        raise RuntimeError(f"{name} not found")

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(Function=types.SimpleNamespace(
        from_name=from_name)))
    result = runner.cloud_download(runner.Job(job_dir))
    assert result["ok"] is False and "download_video not found" in result["error"]


def test_run_gpu_passes_the_download_usage_only_when_there_is_one(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    spawns = []

    class _Function:
        def spawn(self, *args, **kwargs):
            spawns.append((args, kwargs))
            return "call"

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(Function=types.SimpleNamespace(
        from_name=lambda app, name: _Function())))
    monkeypatch.setattr(runner, "wait_for", lambda call, job: {"clips": [], "timings": {}})
    runner.run_gpu(runner.Job(job_dir), 5, "parakeet", {}, [CLOUD_USAGE])
    runner.run_gpu(runner.Job(job_dir), 5, "parakeet", {}, [])
    assert spawns[0][1] == {"extra_usages": [CLOUD_USAGE]}
    assert spawns[1][1] == {} and len(spawns[1][0]) == 4    # works with an older deployment too


# --------------------------------------------------------------------------- #
# Deleting the video after a run
# --------------------------------------------------------------------------- #

def fake_volume(monkeypatch):
    removed = []

    class _Volume:
        def read_file_into_fileobj(self, path, f):
            f.write(b"clip")

        def remove_file(self, path, recursive=False):
            removed.append(path)

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(
        Volume=types.SimpleNamespace(from_name=lambda *a, **k: _Volume())))
    return removed


def fake_gpu(clips):
    seen = {}

    def run(job, max_clips, mode, timings, extra_usages=None):
        seen["usages"] = extra_usages
        return {"clips": clips, "moments": [{"rank": 1}], "errors": [], "gpu": "A10", "ranking": {"valid": 1},
                "cost": {"usd": 0.1, "gpuSeconds": 200, "containers": 2}}

    return run, seen


def test_finished_upload_forgets_its_video_but_keeps_a_fingerprint(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    (job_dir / "input.mp4").write_bytes(b"x" * 1000)
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")
    removed = fake_volume(monkeypatch)
    monkeypatch.setattr(runner, "upload_input", lambda *a, **k: None)
    gpu, _ = fake_gpu(["clip_0.mp4"])
    monkeypatch.setattr(runner, "run_gpu", gpu)
    assert runner.run_job(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert data["status"] == "done" and data["fingerprint"]
    assert not (job_dir / "input.mp4").exists() and (job_dir / "thumbnail.jpg").exists()
    assert (job_dir / "clips" / "clip_0.mp4").exists() and removed == [f"/{job_dir.name}"]


def test_keep_video_keeps_the_local_copy(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")
    fake_volume(monkeypatch)
    monkeypatch.setattr(runner, "upload_input", lambda *a, **k: None)
    monkeypatch.setattr(runner, "run_gpu", fake_gpu(["clip_0.mp4"])[0])
    assert runner.run_job(job_dir, 5, keep_remote=False, keep_video=True) == 0
    assert (job_dir / "input.mp4").exists()


def test_youtube_run_bills_the_download_and_cleans_the_volume(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    removed = fake_volume(monkeypatch)
    patch_transfer(monkeypatch, {"ok": True, "meta": {"title": "A talk"}, "usage": CLOUD_USAGE})
    gpu, seen = fake_gpu(["clip_0.mp4"])
    monkeypatch.setattr(runner, "run_gpu", gpu)
    assert runner.run_job(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert seen["usages"] == [CLOUD_USAGE] and "cloud_download" in data["runs"][-1]["timings"]
    assert removed == [f"/{job_dir.name}"] and not list(job_dir.glob("input.*"))


def test_failed_run_keeps_the_video_for_try_again(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")
    removed = fake_volume(monkeypatch)
    monkeypatch.setattr(runner, "upload_input", lambda *a, **k: None)

    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "run_gpu", broken)
    assert runner.run_job(job_dir, 5, keep_remote=False) == 1
    assert (job_dir / "input.mp4").exists() and removed == [f"/{job_dir.name}"]  # cloud copy still removed


def test_rerank_of_youtube_job_downloads_again(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, status="done", clips=["clip_0.mp4"], engine={"transcriber": "parakeet"})
    (job_dir / "transcript.json").write_text("[]")
    (job_dir / "clips").mkdir()
    (job_dir / "clips" / "clip_0.mp4").write_bytes(b"old")
    fake_volume(monkeypatch)
    ran = patch_transfer(monkeypatch, {"ok": True, "meta": {"title": "A talk"}, "usage": CLOUD_USAGE})
    monkeypatch.setattr(runner, "upload_transcript", lambda *a, **k: ran.append("transcript"))
    gpu, seen = fake_gpu(["clip_0.mp4"])
    monkeypatch.setattr(runner, "run_gpu", gpu)
    assert runner.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert ran == ["cloud", "transcript"] and seen["usages"] == [CLOUD_USAGE]
    assert read(job_dir)["reranks"] == 1


def test_rerank_of_upload_without_its_video_keeps_the_clips(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4", status="done",
                       clips=["clip_0.mp4"])
    (job_dir / "transcript.json").write_text("[]")
    fake_volume(monkeypatch)
    patch_transfer(monkeypatch, {"ok": True})
    assert runner.run_rerank(job_dir, 5, keep_remote=False) == 1
    data = read(job_dir)
    assert data["status"] == "done" and data["clips"] == ["clip_0.mp4"] and "add the same MP4" in data["error"]


# --------------------------------------------------------------------------- #
# Fingerprints and --prune-videos
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("size, expected", [
    # Computed with the web app's algorithm in Node (lib/fingerprint.ts, fileFingerprint).
    (1000, "30246a78903a9df13da43afdeeb46c425bd68b0fa26472a4c25015457c8afe4e"),
    (5 * 1024 * 1024 + 7, "4dc70cb741143253247c44a59e3cde140f1fe8ab8c2eb78ba87e94c391dc2c24"),
])
def test_fingerprint_matches_the_web_app(tmp_path, size, expected):
    path = tmp_path / "input.mp4"
    path.write_bytes(bytes((i * 31 + 7) % 251 for i in range(size)))
    assert runner.file_fingerprint(path) == expected
    assert runner.file_fingerprint(tmp_path / "missing.mp4") is None


def test_prune_deletes_finished_videos_only(tmp_path, monkeypatch):
    done_yt = make_job(tmp_path, id="20261020-100000-aaaaaa", status="done")
    done_up = make_job(tmp_path, id="20261020-110000-bbbbbb", status="done", source="upload", youtubeUrl=None,
                       updatedAt="2026-10-20T11:00:00Z")
    failed = make_job(tmp_path, id="20261020-120000-cccccc", status="failed")
    for job_dir in (done_yt, done_up, failed):
        (job_dir / "input.mp4").write_bytes(b"v" * 2048)
    (done_yt / "input.f137.mp4.part").write_bytes(b"partial")
    (done_up / "thumbnail.jpg").write_bytes(b"jpg")

    assert runner.prune_videos(tmp_path, dry_run=True) == 0
    assert all((d / "input.mp4").exists() for d in (done_yt, done_up, failed))

    assert runner.prune_videos(tmp_path) == 0
    assert not list(done_yt.glob("input.*")) and not (done_up / "input.mp4").exists()
    assert (failed / "input.mp4").exists()                    # Try again still has it
    upload = read(done_up)
    assert upload["fingerprint"] and upload["updatedAt"] == "2026-10-20T11:00:00Z"


# --------------------------------------------------------------------------- #
# Cost of the download container
# --------------------------------------------------------------------------- #

def test_cpu_only_download_is_billed_without_a_gpu():
    gpu = {"container": "g", "gpu": "NVIDIA A10", "seconds": 60, "cpu": 4, "memoryGiB": 4, "scaledown": 5}
    with_download = costs.estimate([gpu, CLOUD_USAGE])
    alone = costs.estimate([gpu])
    extra = with_download["usd"] - alone["usd"]
    cpu_seconds = costs.BOOT_SECONDS + 40 + 5
    assert extra == pytest.approx(cpu_seconds * (2 * costs.CPU_CORE_PRICE_PER_S + 2 * costs.MEMORY_GIB_PRICE_PER_S),
                                  abs=2e-4)
    assert with_download["gpuSeconds"] == alone["gpuSeconds"] and with_download["containers"] == 1
    assert with_download["gpus"] == {"A10": 1}
