"""Offline tests for getting the video to Modal: the laptop download (temp folder, resume,
IPv4), deleting the local copy after a run, upload fingerprints and --prune-videos.

    python -m pytest tests -q
"""

import json
import pathlib
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

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


def test_download_retry_resumes_the_partial_file(tmp_path, monkeypatch):
    calls, _ = fake_yt_dlp(monkeypatch, ["HTTP Error 403: Forbidden", "ok"])
    retries = []
    info = downloader.download("https://youtu.be/x", tmp_path, ffmpeg_location="ffmpeg", cookies_browser="firefox",
                               on_retry=lambda *a: retries.append(a[0]), sleep=lambda s: None)
    assert len(calls) == 2 and retries == [1]
    assert (tmp_path / "input.mp4").read_bytes() == b"video"
    assert (tmp_path / "input.part").exists()           # kept for yt-dlp to resume, not deleted
    assert calls[0]["cookiesfrombrowser"] == ("firefox",) and calls[0]["ffmpeg_location"] == "ffmpeg"
    assert calls[0]["continuedl"] is True and calls[0]["source_address"] == "0.0.0.0"
    assert calls[0]["retry_sleep_functions"]["http"](10) == 30
    meta = downloader.video_meta(info)
    assert meta["title"] == "A talk" and meta["duration"] == 3600 and len(meta["tags"]) == 30


def test_ipv4_can_be_turned_off(tmp_path):
    assert "source_address" not in downloader.ydl_options(tmp_path, ipv4=False)
    assert [downloader.backoff(n) for n in (1, 2, 3, 4, 5, 9)] == [2, 4, 8, 16, 30, 30]


def test_cookie_options_prefer_a_cookies_file():
    assert downloader.cookie_options("chrome", "C:/c.txt") == {"cookiefile": "C:/c.txt"}
    assert downloader.cookie_options("firefox") == {"cookiesfrombrowser": ("firefox",)}
    assert downloader.cookie_options() == {}
    options = downloader.ydl_options(pathlib.Path("."), cookies_file="c.txt")
    assert options["cookiefile"] == "c.txt" and "cookiesfrombrowser" not in options


def fake_cookie_jar(monkeypatch, outcome):
    """yt_dlp stand-in whose cookiejar raises (outcome = message) or holds the given domains."""
    class DownloadError(Exception):
        pass

    class YoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        @property
        def cookiejar(self):
            if isinstance(outcome, str):
                raise DownloadError(outcome)
            return [types.SimpleNamespace(domain=d) for d in outcome]

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(
        YoutubeDL=YoutubeDL, utils=types.SimpleNamespace(DownloadError=DownloadError)))


def test_check_cookies(monkeypatch):
    assert downloader.check_cookies() is None
    fake_cookie_jar(monkeypatch, "Failed to decrypt with DPAPI")
    assert "DPAPI" in downloader.check_cookies(cookies_browser="chrome")
    fake_cookie_jar(monkeypatch, [".google.com"])
    assert "no YouTube cookies" in downloader.check_cookies(cookies_browser="firefox")
    fake_cookie_jar(monkeypatch, [".youtube.com", ".google.com"])
    assert downloader.check_cookies(cookies_file="c.txt") is None


def test_unreadable_chrome_cookies_are_skipped_with_a_hint(monkeypatch):
    lines = []
    monkeypatch.setattr(runner, "log", lines.append)
    monkeypatch.setenv("YTDLP_COOKIES_FROM_BROWSER", "chrome")
    fake_cookie_jar(monkeypatch, "Failed to decrypt with DPAPI")
    assert runner.youtube_cookies() == {"browser": None, "file": None}
    assert "YTDLP_COOKIES_FILE" in lines[-1] and "continuing without them" in lines[-1]
    fake_cookie_jar(monkeypatch, [".youtube.com"])
    assert runner.youtube_cookies()["browser"] is None          # checked once per run


def test_a_cookies_file_wins_and_a_missing_one_falls_back(tmp_path, monkeypatch):
    lines = []
    monkeypatch.setattr(runner, "log", lines.append)
    fake_cookie_jar(monkeypatch, [".youtube.com"])
    cookies = tmp_path / "youtube.txt"
    cookies.write_text("# Netscape HTTP Cookie File")
    monkeypatch.setenv("YTDLP_COOKIES_FILE", f'"{cookies}"')
    monkeypatch.setenv("YTDLP_COOKIES_FROM_BROWSER", "firefox")
    assert runner.youtube_cookies() == {"browser": None, "file": str(cookies)}
    monkeypatch.setattr(runner, "_COOKIES", None)
    monkeypatch.setenv("YTDLP_COOKIES_FILE", str(tmp_path / "missing.txt"))
    assert runner.youtube_cookies() == {"browser": "firefox", "file": None}
    assert any("not found" in line for line in lines)


def test_upload_progress_reports_share_and_eta(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "log", lambda line: None)
    job = runner.Job(make_job(tmp_path))
    progress = runner.UploadProgress(job, size=1000, every_s=0)
    assert progress(name="/x/input.mp4", size=1000) is None       # a new task: no bytes yet
    progress(None, advance=250)                                   # Modal's partial(cb, task_id)(advance=n)
    progress(task_id=None, advance=250)
    data = read(job.dir)["progress"]
    assert data["stage"] == "uploading" and data["fraction"] == 0.5 and data["etaSeconds"] is not None
    progress(task_id=None, complete=True)
    progress(advance=600)
    assert progress.fraction == 0.99                               # capped until the run moves on


def test_a_retried_piece_is_not_counted_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "log", lambda line: None)
    progress = runner.UploadProgress(runner.Job(make_job(tmp_path)), size=1000, every_s=0)
    progress(advance=400)
    progress(advance=-300)            # a failed piece: Modal subtracts what it had sent
    progress(advance=300)             # and sends it again
    assert progress.sent == 400
    progress(reset=True)              # older volumes start the file over
    assert progress.sent == 0
    progress(advance=-50)
    assert progress.sent == 0


def test_a_stalled_upload_is_flagged_and_cleared(tmp_path, monkeypatch):
    lines = []
    monkeypatch.setattr(runner, "log", lines.append)
    now = [1000.0]
    job = runner.Job(make_job(tmp_path))
    progress = runner.UploadProgress(job, size=1000, every_s=0, clock=lambda: now[0])
    progress(advance=100)
    now[0] += runner.UPLOAD_STALL_S - 1
    progress.check_stall()
    assert not progress.stalled
    now[0] += 2
    progress.check_stall()
    assert progress.stalled and "no data sent" in lines[-1]
    assert read(job.dir)["progress"]["stalled"] is True and read(job.dir)["progress"]["etaSeconds"] is None
    progress(advance=100)
    assert not progress.stalled and "Upload moving again" in lines
    assert "stalled" not in read(job.dir)["progress"] or not read(job.dir)["progress"]["stalled"]


def test_upload_falls_back_to_a_plain_batch_when_progress_is_unavailable(tmp_path, monkeypatch):
    put = []

    class _Batch:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def put_file(self, local, remote):
            put.append(remote)

    class _Volume:
        def hydrate(self):
            raise RuntimeError("no such private API")

        def batch_upload(self, force=False):
            return _Batch()

    monkeypatch.setattr(runner, "log", lambda line: None)
    monkeypatch.setattr(runner, "limit_upload_piece_time", lambda seconds: True)
    job = runner.Job(make_job(tmp_path))
    runner.upload_with_progress(_Volume(), tmp_path / "input.mp4", "/x/input.mp4", runner.UploadProgress(job, 10))
    assert put == ["/x/input.mp4"]


def test_upload_pieces_get_a_time_limit(monkeypatch):
    import asyncio

    http_utils = pytest.importorskip("modal._utils.http_utils")
    registry = http_utils.ClientSessionRegistry
    monkeypatch.setattr(registry, "get_session", registry.get_session)
    monkeypatch.setattr(registry, "_client_session_active", False, raising=False)
    monkeypatch.setattr(http_utils, "on_shutdown", lambda coro: coro.close())
    proxy_support = pytest.importorskip("modal._utils.proxy_support")
    monkeypatch.setattr(proxy_support, "get_proxy_url", lambda *a, **k: None)   # this sandbox has a proxy
    assert runner.limit_upload_piece_time(600)

    async def make():
        session = registry.get_session()
        timeout = session.timeout.total
        await session.close()
        return timeout

    assert asyncio.run(make()) == 600
    monkeypatch.setattr(registry, "_clipper_timeout", None, raising=False)


def test_upload_context_sends_fewer_pieces_at_once(monkeypatch):
    modal_volume = pytest.importorskip("modal.volume")
    from modal_proto import api_pb2

    built = {}

    class _Volume:
        object_id, client = "vo-1", "client"

        def hydrate(self):
            pass

        def _get_metadata(self):
            return types.SimpleNamespace(version=api_pb2.VolumeFsVersion.VOLUME_FS_VERSION_V2)

    monkeypatch.setattr(modal_volume, "VolumeUploadContextManager2", lambda *a, **k: built.update(args=a, **k) or "ctx")
    assert runner.upload_context(_Volume(), progress="cb") == "ctx"
    assert built["args"] == ("vo-1", "client") and built["put_concurrency"] == runner.UPLOAD_PIECES_AT_ONCE
    assert built["progress_cb"] == "cb" and built["force"] is True


def test_waiting_for_modal_rides_out_a_lost_connection(tmp_path, monkeypatch):
    modal = pytest.importorskip("modal")
    lines = []
    monkeypatch.setattr(runner, "log", lines.append)
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    monkeypatch.setattr(modal.Dict, "from_name", lambda *a, **k: types.SimpleNamespace(
        get=lambda key: None, pop=lambda key: None))
    outcomes = [modal.exception.ConnectionError("Deadline exceeded"), modal.exception.ConnectionError("again"),
                TimeoutError(), {"clips": ["clip_0.mp4"]}]

    class _Call:
        def get(self, timeout=None):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    assert runner.wait_for(_Call(), runner.Job(make_job(tmp_path))) == {"clips": ["clip_0.mp4"]}
    assert "Lost the connection to Modal" in lines[0] and lines[-1] == "Connection to Modal is back"
    assert len([line for line in lines if line.startswith("Lost")]) == 1


def test_waiting_for_modal_gives_up_after_a_long_outage(tmp_path, monkeypatch):
    modal = pytest.importorskip("modal")
    monkeypatch.setattr(runner, "log", lambda line: None)
    now = [0.0]

    def sleep(seconds):
        now[0] += 60

    monkeypatch.setattr(runner.time, "sleep", sleep)
    monkeypatch.setattr(runner.time, "time", lambda: now[0])
    monkeypatch.setattr(modal.Dict, "from_name", lambda *a, **k: types.SimpleNamespace(
        get=lambda key: None, pop=lambda key: None))

    class _Call:
        def get(self, timeout=None):
            raise modal.exception.ConnectionError("Deadline exceeded")

    with pytest.raises(modal.exception.ConnectionError):
        runner.wait_for(_Call(), runner.Job(make_job(tmp_path)))
    assert now[0] > runner.MODAL_OUTAGE_S


def test_download_gives_up_at_once_when_blocked(tmp_path, monkeypatch):
    calls, error = fake_yt_dlp(monkeypatch, ["Sign in to confirm you're not a bot", "ok"])
    with pytest.raises(error):
        downloader.download("https://youtu.be/x", tmp_path, sleep=lambda s: None)
    assert len(calls) == 1


# --------------------------------------------------------------------------- #
# Getting the video to Modal
# --------------------------------------------------------------------------- #

def patch_transfer(monkeypatch, tmp_path):
    """Stand-ins for the YouTube download and the Modal upload; returns a log of what ran."""
    ran = []
    monkeypatch.setattr(runner, "DOWNLOADS_DIR", tmp_path / "downloads")

    def laptop(job, attempts=3):
        ran.append("laptop")
        folder = runner.download_folder(job)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "input.mp4").write_bytes(b"video")
        runner.apply_meta(job, {"title": "A talk"})
        return folder / "input.mp4"

    def upload(job, volume, update_status=True, video=None):
        ran.append("upload")
        ran.append((video or job.dir / "input.mp4").read_bytes())

    monkeypatch.setattr(runner, "download_youtube", laptop)
    monkeypatch.setattr(runner, "upload_input", upload)
    return ran


def test_youtube_video_downloads_outside_the_job_folder_and_is_deleted_after_upload(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    ran = patch_transfer(monkeypatch, tmp_path)
    job, timings = runner.Job(job_dir), {}
    runner.put_video_on_modal(job, None, timings)
    assert ran == ["laptop", "upload", b"video"]
    assert {"download", "upload"} <= set(timings)
    data = read(job_dir)
    assert data["title"] == "A talk" and data["videoTransfer"] == "laptop-download"
    assert not list(job_dir.glob("input.*")) and not runner.download_folder(job).exists()


def test_keep_video_moves_the_download_into_the_job_folder(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    patch_transfer(monkeypatch, tmp_path)
    job = runner.Job(job_dir)
    runner.put_video_on_modal(job, None, {}, keep_video=True)
    assert (job_dir / "input.mp4").read_bytes() == b"video" and not runner.download_folder(job).exists()


def test_a_video_in_the_job_folder_is_uploaded(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)                 # e.g. a job from before this change
    (job_dir / "input.mp4").write_bytes(b"video")
    ran = patch_transfer(monkeypatch, tmp_path)
    timings = {}
    runner.put_video_on_modal(runner.Job(job_dir), None, timings)
    assert ran == ["upload", b"video"] and "upload" in timings and read(job_dir)["videoTransfer"] == "upload"
    assert (job_dir / "input.mp4").exists()


def test_an_upload_without_its_video_asks_for_the_file_again(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    patch_transfer(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError, match="add the same MP4 again"):
        runner.put_video_on_modal(runner.Job(job_dir), None, {})


def test_failed_download_keeps_its_partial_file_for_try_again(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    monkeypatch.setattr(runner, "DOWNLOADS_DIR", tmp_path / "downloads")
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", types.SimpleNamespace(get_ffmpeg_exe=lambda: "ffmpeg"))
    monkeypatch.setattr(downloader.time, "sleep", lambda s: None)
    calls, _ = fake_yt_dlp(monkeypatch, ["Read timed out"] * 3)
    fake_volume(monkeypatch)
    monkeypatch.setattr(runner, "fetch_replay", lambda *a, **k: None)
    assert runner.run_job(job_dir, 5, keep_remote=False) == 1
    data = read(job_dir)
    assert data["status"] == "failed" and data["failedStage"] == "downloading" and "timed out" in data["error"]
    assert len(calls) == 3 and all(c["source_address"] == "0.0.0.0" for c in calls)
    assert (tmp_path / "downloads" / job_dir.name / "input.part").exists()   # Try again resumes it
    assert not list(job_dir.glob("input.*"))


def test_old_download_folders_are_cleared(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(runner, "DOWNLOADS_DIR", tmp_path / "downloads")
    old, new = tmp_path / "downloads" / "old", tmp_path / "downloads" / "new"
    for folder in (old, new):
        folder.mkdir(parents=True)
        (folder / "input.f137.mp4.part").write_bytes(b"x")
    now = os.path.getmtime(new)
    os.utime(old, (now - 49 * 3600, now - 49 * 3600))
    runner.clear_old_downloads(now=now)
    assert not old.exists() and new.exists()
    monkeypatch.setattr(runner, "DOWNLOADS_DIR", tmp_path / "missing")
    runner.clear_old_downloads()                  # no folder yet: nothing to do


def test_run_gpu_spawns_with_the_four_job_arguments(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    spawns = []

    class _Function:
        def spawn(self, *args, **kwargs):
            spawns.append((args, kwargs))
            return "call"

    monkeypatch.setitem(sys.modules, "modal", types.SimpleNamespace(Function=types.SimpleNamespace(
        from_name=lambda app, name: _Function())))
    monkeypatch.setattr(runner, "wait_for", lambda call, job: {"clips": [], "timings": {}})
    runner.run_gpu(runner.Job(job_dir), 5, "parakeet", {})
    assert spawns[0][1] == {} and len(spawns[0][0]) == 4


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

    def run(job, max_clips, mode, timings):
        seen["mode"] = mode
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


def test_youtube_run_cleans_the_volume_and_the_download(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    removed = fake_volume(monkeypatch)
    patch_transfer(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "fetch_replay", lambda *a, **k: None)
    gpu, _ = fake_gpu(["clip_0.mp4"])
    monkeypatch.setattr(runner, "run_gpu", gpu)
    assert runner.run_job(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert {"download", "upload"} <= set(data["runs"][-1]["timings"])
    assert removed == [f"/{job_dir.name}"] and not list(job_dir.glob("input.*"))
    assert not (tmp_path / "downloads" / job_dir.name).exists()


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
    assert (job_dir / "input.mp4").exists() and removed == [f"/{job_dir.name}"]  # the volume copy is still removed


def test_rerank_of_youtube_job_downloads_again(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, status="done", clips=["clip_0.mp4"], engine={"transcriber": "parakeet"})
    (job_dir / "transcript.json").write_text("[]")
    (job_dir / "clips").mkdir()
    (job_dir / "clips" / "clip_0.mp4").write_bytes(b"old")
    fake_volume(monkeypatch)
    ran = patch_transfer(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "fetch_replay", lambda *a, **k: None)
    monkeypatch.setattr(runner, "upload_transcript", lambda *a, **k: ran.append("transcript"))
    gpu, seen = fake_gpu(["clip_0.mp4"])
    monkeypatch.setattr(runner, "run_gpu", gpu)
    assert runner.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert ran == ["laptop", "upload", b"video", "transcript"] and seen["mode"] == "reuse"
    assert read(job_dir)["reranks"] == 1


def test_rerank_of_upload_without_its_video_keeps_the_clips(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4", status="done",
                       clips=["clip_0.mp4"])
    (job_dir / "transcript.json").write_text("[]")
    fake_volume(monkeypatch)
    patch_transfer(monkeypatch, tmp_path)
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
