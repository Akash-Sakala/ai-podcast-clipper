"""The two-phase run (phases.py) end to end with a fake Modal and fake media steps: what goes
up, which calls are spawned with what, what the laptop does meanwhile, and every failure
ordering (the cloud fails while the video downloads, the download fails after the ranking,
nothing to render, the audio or the footage can't be had on their own, an old deployment,
a lost connection, a stop signal). Also: one progress bar over several files, the cost of
two calls, the fps-capped YouTube format choice and the audio-only download with the real
yt-dlp against a local web server.

    python -m pytest tests/test_two_phase.py -q
"""

import functools
import http.server
import json
import pathlib
import shutil
import socketserver
import sys
import threading
import time
import types

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import cloud  # noqa: E402
import costs  # noqa: E402
import cutter  # noqa: E402
import downloader  # noqa: E402
import jobstore  # noqa: E402
import phases  # noqa: E402
import pipeline  # noqa: E402
import run_history  # noqa: E402
import transfer  # noqa: E402
import videostore  # noqa: E402
import youtube  # noqa: E402

JOB_ID = "20261114-120000-abcdef"
MOMENTS = [{"start": 61.2, "end": 104.9, "rank": 1, "score": 81, "title": "a"},
           {"start": 300.0, "end": 345.5, "rank": 2, "score": 77, "title": "b"}]
USAGE = {"container": "t1", "gpu": "NVIDIA A10", "seconds": 40.0, "enterSeconds": 12.0, "cpu": 0.125,
         "memoryGiB": 3.0, "scaledown": 10}


def read(job_dir):
    return json.loads((job_dir / "job.json").read_text(encoding="utf-8"))


def make_job(tmp_path, **fields):
    job_dir = tmp_path / "jobs" / JOB_ID
    job_dir.mkdir(parents=True)
    data = {"id": JOB_ID, "source": "youtube", "title": "https://youtu.be/x", "youtubeUrl": "https://youtu.be/x",
            "status": "queued", "clips": [], **fields}
    (job_dir / "job.json").write_text(json.dumps(data), encoding="utf-8")
    (job_dir / "thumbnail.jpg").write_bytes(b"jpg")
    return job_dir


def phase_one(moments=MOMENTS):
    return {"moments": [dict(m) for m in moments], "ranking": {"valid": len(moments)},
            "timings": {"transcribe": 41.0, "select": 50.0}, "transcription": {"audioSeconds": 1800.0},
            "subtitles": "parakeet", "clipWords": None, "gpu": "A10",
            "usages": [{**USAGE, "stage": "transcribe"}], "orchestratorSeconds": 95.0,
            "orchestrator": {"cpu": 0.125, "memoryGiB": 0.5}}


def phase_two(n=2):
    return {"clips": [f"clip_{i}.mp4" for i in range(n)], "errors": [], "timings": {"render": 64.0},
            "clipTimings": [{"clip": f"clip_{i}.mp4", "seconds": 50.0} for i in range(n)], "gpu": "A10",
            "cost": {"usd": 0.05}, "usages": [{**USAGE, "container": f"r{i}", "stage": "render", "cpu": 4.0}
                                              for i in range(n)],
            "orchestratorSeconds": 70.0, "orchestrator": {"cpu": 0.125, "memoryGiB": 0.25}}


class FakeCall:
    def __init__(self, world, name, outcome, polls):
        self.world, self.name, self.outcome, self.polls = world, name, outcome, polls
        self.object_id = f"fc-{name}"
        self.cancelled = False

    def get(self, timeout=None):
        if self.cancelled:
            raise RuntimeError("the call was cancelled")
        if self.polls:
            self.polls -= 1
            item = self.world.flaky.pop(0) if self.world.flaky else None
            if item is not None:
                raise item
            time.sleep(0.01)
            raise TimeoutError()
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        result = self.outcome(self) if callable(self.outcome) else self.outcome
        self.world.events.append(f"{self.name} returned")
        return result

    def cancel(self, terminate_containers=False):
        self.cancelled = True
        self.world.events.append(f"{self.name} cancelled")


class World:
    """A fake Modal (functions, progress Dict, volume) plus fake media steps, with a log."""

    def __init__(self, tmp_path, monkeypatch, first=None, second=None, polls=3):
        self.tmp, self.events, self.spawns, self.uploads, self.removed = tmp_path, [], [], [], []
        self.files: dict[str, bytes] = {}
        self.outcomes = {"transcribe_and_rank": first if first is not None else self.phase_one,
                         "render_clips": second if second is not None else self.phase_two}
        self.polls, self.flaky, self.calls = polls, [], {}
        self.missing_functions = False
        self.progress = {"transcribe_and_rank": {"stage": "ranking"}, "render_clips": {"stage": "rendering"}}
        self.install(monkeypatch)

    # Results that also do what the containers do on the volume
    def phase_one(self, call):
        self.files[f"/{JOB_ID}/transcript.json"] = b'[{"word": "hi"}]'
        self.files[f"/{JOB_ID}/moments.json"] = json.dumps(MOMENTS).encode()
        return phase_one()

    def phase_two(self, call):
        for i in range(2):
            self.files[f"/{JOB_ID}/clips/clip_{i}.mp4"] = f"clip {i}".encode()
        return phase_two()

    def install(self, monkeypatch):
        world = self

        class Function:
            def __init__(self, name):
                self.name = name

            def hydrate(self):
                if world.missing_functions:
                    raise NotFound(f"Function {self.name} not found")

            def spawn(self, *args, **kwargs):
                world.events.append(f"spawn {self.name}")
                world.spawns.append((self.name, args))
                world.current = self.name
                call = FakeCall(world, self.name, world.outcomes[self.name], world.polls)
                world.calls[self.name] = call
                return call

        class Volume:
            def read_file_into_fileobj(self, path, f):
                if path not in world.files:
                    raise FileNotFoundError(path)
                f.write(world.files[path])

            def remove_file(self, path, recursive=False):
                world.removed.append(path)

            def listdir(self, path, recursive=False):
                return []

            def batch_upload(self, force=False):
                class Batch:
                    def __enter__(self):
                        return self

                    def __exit__(self, *exc):
                        return False

                    def put_file(self, local, remote):
                        world.events.append(f"put {remote}")
                        world.uploads.append((pathlib.Path(local).name, remote))

                return Batch()

        NotFound = type("NotFoundError", (Exception,), {})
        modal = types.SimpleNamespace(
            Function=types.SimpleNamespace(from_name=lambda app, name: Function(name)),
            Volume=types.SimpleNamespace(from_name=lambda *a, **k: Volume()),
            Dict=types.SimpleNamespace(from_name=lambda *a, **k: types.SimpleNamespace(
                get=lambda key: world.progress.get(getattr(world, "current", ""), None), pop=lambda key: None)),
            FunctionCall=types.SimpleNamespace(from_id=lambda call_id: types.SimpleNamespace(
                cancel=lambda terminate_containers=False: world.events.append(f"cancel {call_id}"))),
            exception=types.SimpleNamespace(TimeoutError=type("ModalTimeout", (Exception,), {}),
                                            ConnectionError=type("ModalConnection", (Exception,), {}),
                                            NotFoundError=NotFound))
        self.modal = modal
        monkeypatch.setitem(sys.modules, "modal", modal)
        monkeypatch.setattr(youtube, "DOWNLOADS_DIR", self.tmp / "downloads")
        monkeypatch.setattr(pipeline, "fetch_replay", lambda *a, **k: None)
        monkeypatch.setattr(pipeline, "refresh_modal_bill", lambda *a, **k: None)
        monkeypatch.setattr(transfer, "save_clip_poster", lambda clip: True)
        monkeypatch.setattr(transfer.time, "sleep", lambda s: None)
        monkeypatch.setattr(cloud.time, "sleep", lambda s: None)

        def upload(volume, local, remote, progress):
            world.events.append(f"put {remote}")
            world.uploads.append((pathlib.Path(local).name, remote))
            progress(advance=pathlib.Path(local).stat().st_size)

        monkeypatch.setattr(transfer, "upload_with_progress", upload)

        def upload_input(job, volume, update_status=True, video=None):
            video = video or job.dir / "input.mp4"
            world.events.append(f"put /{job.id}/input.mp4")
            world.uploads.append((video.name, f"/{job.id}/input.mp4"))

        monkeypatch.setattr(transfer, "upload_input", upload_input)

        def audio_download(job, on_progress=None):
            world.events.append("audio download")
            folder = youtube.download_folder(job) / "audio-download"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "audio.m4a").write_bytes(b"aac")
            return folder / "audio.m4a", "140"

        def extract(source, out_dir):
            world.events.append(f"extract from {source.name}")
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "audio.m4a").write_bytes(b"audio")
            return out_dir / "audio.m4a"

        def footage(video, moments, out_dir):
            world.events.append(f"cut {video.name}")
            out_dir.mkdir(parents=True, exist_ok=True)
            pieces = []
            for i, m in enumerate(moments):
                (out_dir / f"clip_{i}.mp4").write_bytes(b"piece")
                pieces.append({"index": i, "path": out_dir / f"clip_{i}.mp4", "bytes": 5,
                               "targetUs": int(m["start"] * 1e6)})
            return pieces

        monkeypatch.setattr(phases, "download_youtube_audio", audio_download)
        monkeypatch.setattr(phases.cutter, "extract_audio", extract)
        monkeypatch.setattr(phases.cutter, "make_footage", footage)
        monkeypatch.setattr(phases, "download_youtube", self.video_download())

        def whole_download(job, attempts=3, **kwargs):
            world.events.append("whole video download")
            folder = youtube.download_folder(job)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "input.mp4").write_bytes(b"video")
            return folder / "input.mp4"

        monkeypatch.setattr(transfer, "download_youtube", whole_download)

    def video_download(self, fail=None, wait_for_cloud=False, until_stopped=False):
        """until_stopped: the download never finishes by itself (it now starts before the
        audio upload, long before the cloud answers); it ends when on_progress raises."""
        world = self
        pause = threading.Event()                          # a real wait: World turns time.sleep off

        def download(job, on_progress=None, audio_format=None, update_status=True):
            world.events.append(f"video download (audio {audio_format})")
            world.audio_format = audio_format
            for _ in range(5000 if until_stopped else 0):
                on_progress(0.5)
                pause.wait(0.002)
            assert not until_stopped, "the download was never stopped"
            for step in range(200):                    # gives the cloud time to answer meanwhile
                on_progress(min(0.99, step / 50))
                if wait_for_cloud and world.events[-1].endswith("returned"):
                    break
                time.sleep(0.005)
            if fail is not None:
                raise fail
            folder = youtube.download_folder(job)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "input.mp4").write_bytes(b"video")
            world.events.append("video ready")
            return folder / "input.mp4"

        return download


# --------------------------------------------------------------------------- #
# Runs that succeed
# --------------------------------------------------------------------------- #

def test_youtube_job_sends_audio_then_only_the_clip_footage(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0

    assert world.uploads == [("audio.m4a", f"/{JOB_ID}/audio.m4a"),
                             ("clip_0.mp4", f"/{JOB_ID}/footage/clip_0.mp4"),
                             ("clip_1.mp4", f"/{JOB_ID}/footage/clip_1.mp4")]        # never the whole video
    (first_name, first_args), (second_name, second_args) = world.spawns
    assert first_name == "transcribe_and_rank" and first_args[0] == JOB_ID and first_args[1] == 5
    assert first_args[3:] == ("parakeet", "audio.m4a")
    assert second_name == "render_clips" and second_args[1] == MOMENTS
    assert second_args[2] == [{"index": 0, "targetUs": 61_200_000}, {"index": 1, "targetUs": 300_000_000}]
    events = world.events
    assert events.index("audio download") < events.index("video download (audio 140)") \
        < events.index("spawn transcribe_and_rank")                 # the download starts before the audio is up
    assert events.index(f"put /{JOB_ID}/audio.m4a") < events.index("spawn transcribe_and_rank")
    assert events.index("video ready") < events.index("cut input.mp4") < events.index("spawn render_clips")
    assert world.audio_format == "140"                              # the video comes with that same audio

    data = read(job_dir)
    assert data["status"] == "done" and data["clips"] == ["clip_0.mp4", "clip_1.mp4"]
    assert data["videoTransfer"] == "audio-first" and data["engine"]["parakeet"] == {"audioSeconds": 1800.0}
    timings = data["timings"]
    assert {"audio", "download", "downloadWait", "cut", "clipUpload", "gpu_transcribe", "gpu_select",
            "gpu_render", "gpu_total", "save", "total"} <= set(timings) and "upload" not in timings
    assert (job_dir / "transcript.json").read_bytes() == b'[{"word": "hi"}]'
    assert json.loads((job_dir / "moments.json").read_text()) == MOMENTS
    assert world.removed == [f"/{JOB_ID}"] and not (tmp_path / "downloads" / JOB_ID).exists()
    assert data.get("modalCallId") is None
    stages = data["cost"]["stages"]
    assert stages["transcribe"] > 0 and stages["render"] > stages["transcribe"] and stages["rank"] > 0
    [line] = run_history.read_ledger(tmp_path)
    assert line["timings"] == data["runs"][-1]["timings"] and line["clips"] == 2


def test_planned_clips_are_in_job_json_before_the_footage_is_cut(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    real_update = jobstore.Job.update

    def spy(self, **fields):
        if fields.get("plannedClips"):
            world.events.append("planned " + ",".join(c["name"] for c in fields["plannedClips"]))
        return real_update(self, **fields)

    monkeypatch.setattr(jobstore.Job, "update", spy)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    events = world.events
    assert events.index("planned clip_0.mp4,clip_1.mp4") < events.index("cut input.mp4")
    assert read(job_dir)["plannedClips"] is None


def test_uploaded_mp4_copies_its_audio_and_cuts_its_own_file(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    (job_dir / "input.mp4").write_bytes(b"x" * 4096)
    world = World(tmp_path, monkeypatch)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert "extract from input.mp4" in world.events and "audio download" not in world.events
    assert not any(e.startswith("video download") for e in world.events)
    assert [r for _, r in world.uploads] == [f"/{JOB_ID}/audio.m4a", f"/{JOB_ID}/footage/clip_0.mp4",
                                            f"/{JOB_ID}/footage/clip_1.mp4"]
    data = read(job_dir)
    assert data["status"] == "done" and data["fingerprint"] and not (job_dir / "input.mp4").exists()
    assert data["timings"]["downloadWait"] == 0 and "download" not in data["timings"]


def test_progress_shows_the_cloud_stage_with_the_download_running_meanwhile(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch, polls=60)
    seen = []
    original = jobstore.set_progress

    def spy(job, stage, **fields):
        seen.append((stage, fields.get("download", fields.get("fraction"))))
        original(job, stage, **fields)

    monkeypatch.setattr(phases, "set_progress", spy)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    stages = [s for s, _ in seen]
    assert stages[0] == "audio" and "clips" in stages
    # the download's share shows next to the cloud stage, or alone once the cloud is done
    assert any(stage in ("transcribing", "ranking", "downloading") and isinstance(download, float) and download > 0
               for stage, download in seen)
    assert world.spawns[0][0] == "transcribe_and_rank"


def test_progress_moves_to_downloading_as_soon_as_the_ranking_returns(tmp_path, monkeypatch):
    """The ranking finishes while the video is still downloading: the strip must leave the
    ranking label then, not when the download ends."""
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    inner = world.video_download()
    shown = threading.Event()                              # "downloading" reached the strip

    def slow_download(job, on_progress=None, audio_format=None, update_status=True):
        world.events.append(f"video download (audio {audio_format})")
        for step in range(100_000):
            if "transcribe_and_rank returned" in world.events:
                break
            on_progress(min(0.5, step / 100))
        # The download is still running: it only goes on once the strip has moved on by itself
        # (an Event, not a sleep: World turns time.sleep off, and threads are scheduled freely).
        world.events.append("strip moved on" if shown.wait(10) else "strip never moved on")
        on_progress(0.9)
        return inner(job, on_progress=on_progress, audio_format=audio_format, update_status=update_status)

    monkeypatch.setattr(phases, "download_youtube", slow_download)
    original = jobstore.set_progress

    def spy(job, stage, **fields):
        world.events.append(f"progress {stage}")
        original(job, stage, **fields)
        if stage == "downloading":
            shown.set()

    monkeypatch.setattr(phases, "set_progress", spy)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    events = world.events
    assert "strip moved on" in events                      # while the download was still running
    returned = events.index("transcribe_and_rank returned")
    moved = events.index("progress downloading", returned)
    ready = events.index("video ready")
    # No ranking label once the strip shows the download (an update already on its way when the
    # ranking returned may still land just before it).
    assert not [e for e in events[moved:ready] if e in ("progress ranking", "progress transcribing")]
    assert moved < events.index("strip moved on") < ready < events.index("progress clips")


def test_the_video_download_starts_while_the_audio_uploads(tmp_path, monkeypatch):
    """The audio upload is the slow part on a home uplink: the video must already be
    downloading during it, and the audio's progress shows the download's share."""
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    release = threading.Event()                            # the download ends only once the audio is up
    seen = {}

    def download(job, on_progress=None, audio_format=None, update_status=True):
        world.events.append(f"video download (audio {audio_format})")
        on_progress(0.4)
        world.events.append("video at 40%")
        assert release.wait(10), "the audio upload never ran while the video downloaded"
        folder = youtube.download_folder(job)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "input.mp4").write_bytes(b"video")
        world.events.append("video ready")
        return folder / "input.mp4"

    def upload(volume, local, remote, progress):
        if remote.endswith("audio.m4a"):
            for _ in range(2000):                          # until the download has reported its share
                if "video at 40%" in world.events:
                    break
                release.wait(0.005)
            progress(advance=pathlib.Path(local).stat().st_size)
            seen["progress"] = dict(read(job_dir)["progress"])
            world.events.append("audio uploaded")
            release.set()
        world.uploads.append((pathlib.Path(local).name, remote))

    monkeypatch.setattr(phases, "download_youtube", download)
    monkeypatch.setattr(transfer, "upload_with_progress", upload)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    events = world.events
    assert events.index("video download (audio 140)") < events.index("audio uploaded") < events.index("video ready")
    assert seen["progress"]["stage"] == "audio" and seen["progress"]["download"] == 0.4
    data = read(job_dir)
    assert data["status"] == "done" and data["clips"] == ["clip_0.mp4", "clip_1.mp4"]
    assert {"audio", "download", "downloadWait"} <= set(data["timings"])


def test_cloud_time_leaves_out_the_wait_for_the_video_download(tmp_path, monkeypatch):
    """Phase 1 returns while the video is still downloading: that wait is "downloadWait",
    and must not be counted again in "gpu_total" (the Modal calls' own wall time)."""
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    inner = world.video_download()
    held = threading.Event()                               # a real wait: World turns time.sleep off

    def late_download(job, on_progress=None, audio_format=None, update_status=True):
        for _ in range(100_000):
            if "transcribe_and_rank returned" in world.events:
                break
            held.wait(0.001)
        held.wait(0.5)                                     # the download ends half a second later
        return inner(job, on_progress=on_progress, audio_format=audio_format, update_status=update_status)

    monkeypatch.setattr(phases, "download_youtube", late_download)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    timings = read(job_dir)["timings"]
    assert timings["downloadWait"] >= 0.4
    assert timings["gpu_total"] < timings["downloadWait"]


def test_a_run_that_succeeds_after_a_failure_clears_where_it_failed(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, status="failed", failedStage="rendering", error="boom")
    World(tmp_path, monkeypatch)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert data["status"] == "done" and data.get("failedStage") is None and data.get("error") is None


def test_phase_one_time_ends_when_the_cloud_call_ended(tmp_path, monkeypatch):
    job = jobstore.Job(make_job(tmp_path))
    monkeypatch.setattr(phases.time, "time", lambda: 200.0)
    timings = {}
    phases.phase_one_done(job, None, {"timings": {"select": 20.0}}, "reuse", timings, 100.0, 130.0)
    assert timings == {"gpu_select": 20.0, "gpu_total": 30.0}
    phases.phase_one_done(job, None, {}, "reuse", timings, 100.0)          # no end given: now
    assert timings["gpu_total"] == 100.0


def test_an_upload_has_no_video_download_to_start(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    (job_dir / "input.mp4").write_bytes(b"x" * 4096)
    world = World(tmp_path, monkeypatch)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert not [e for e in world.events if e.startswith("video download")]
    assert "download" not in (read(job_dir)["timings"])


# --------------------------------------------------------------------------- #
# Failure orderings
# --------------------------------------------------------------------------- #

def test_a_failed_audio_upload_stops_the_video_download_and_keeps_its_part(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    monkeypatch.setattr(phases, "download_youtube", world.video_download(until_stopped=True))
    logged = []
    original_log = phases.log
    monkeypatch.setattr(phases, "log", lambda line: (logged.append(line), original_log(line)))

    def broken(volume, local, remote, progress):
        raise ConnectionError("the uplink dropped")

    monkeypatch.setattr(transfer, "upload_with_progress", broken)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    data = read(job_dir)
    assert data["status"] == "failed" and "uplink" in data["error"] and world.spawns == []
    assert "video ready" not in world.events
    assert any("Stopped the video download" in line for line in logged)      # not left running behind the run
    assert "download" in data["timings"]


def test_audio_cannot_be_prepared_so_the_started_download_finishes_and_goes_up_whole(tmp_path, monkeypatch):
    """A YouTube job whose audio downloads but can't be prepared: the video download that
    already started is waited for (never two downloads of one file), then uploaded whole."""
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)

    def no_ffmpeg(source, out_dir):
        raise FileNotFoundError(2, "No such file or directory", "ffmpeg")

    monkeypatch.setattr(phases.cutter, "extract_audio", no_ffmpeg)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    events = world.events
    assert events.index("video ready") < events.index(f"put /{JOB_ID}/input.mp4")
    if "whole video download" in events:                    # the real one finds the finished file and keeps it
        assert events.index("video ready") < events.index("whole video download")
    assert world.uploads == [("input.mp4", f"/{JOB_ID}/input.mp4")]
    data = read(job_dir)
    assert data["status"] == "done" and {"download", "upload"} <= set(data["timings"])


def test_ranking_fails_while_the_video_downloads(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch, polls=1)
    monkeypatch.setattr(phases, "download_youtube", world.video_download(until_stopped=True))
    world.outcomes["transcribe_and_rank"] = RuntimeError("GEMINI_API_KEY is missing")
    world.files[f"/{JOB_ID}/transcript.json"] = b"[]"              # transcribed, then the ranking broke
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    data = read(job_dir)
    assert data["status"] == "failed" and "GEMINI_API_KEY" in data["error"] and data["failedStage"] == "ranking"
    assert "video ready" not in world.events                        # the download was stopped early
    assert [name for name, _ in world.spawns] == ["transcribe_and_rank"]
    assert (job_dir / "transcript.json").exists()                   # Try again will not transcribe again


def test_download_fails_after_the_ranking_and_try_again_reuses_the_transcript(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch, polls=1)
    monkeypatch.setattr(phases, "download_youtube",
                        world.video_download(fail=RuntimeError("HTTP Error 403: Forbidden"), wait_for_cloud=True))
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    data = read(job_dir)
    assert data["status"] == "failed" and data["failedStage"] == "downloading" and "403" in data["error"]
    assert (job_dir / "transcript.json").exists() and data["engine"]["parakeet"] == {"audioSeconds": 1800.0}
    assert "transcribe_and_rank returned" in world.events

    # Try again: no audio, no transcription; the transcript goes up and phase 1 only ranks.
    world.spawns.clear()
    world.uploads.clear()
    monkeypatch.setattr(phases, "download_youtube", world.video_download())
    jobstore.Job(job_dir).update(status="queued", error=None)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert world.spawns[0][1][3:] == ("reuse", "audio.m4a")
    assert world.uploads[0] == ("transcript.json", f"/{JOB_ID}/transcript.json")
    assert all(not remote.endswith("audio.m4a") for _, remote in world.uploads)
    data = read(job_dir)
    assert data["status"] == "done" and data["engine"]["parakeet"] == {"audioSeconds": 1800.0}


def test_nothing_to_render_stops_the_download_and_finishes(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch, first=lambda call: phase_one(moments=[]), polls=1)
    monkeypatch.setattr(phases, "download_youtube", world.video_download(until_stopped=True))
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert data["status"] == "done" and data["clips"] == [] and data["message"].startswith("No clips")
    assert [name for name, _ in world.spawns] == ["transcribe_and_rank"] and "video ready" not in world.events
    assert not any("/footage/" in remote for _, remote in world.uploads)
    assert not (tmp_path / "downloads" / JOB_ID).exists() and world.removed == [f"/{JOB_ID}"]


def test_gemini_fallback_picks_are_rendered_and_explained(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)

    def fallback(call):
        call.world.phase_one(call)
        result = phase_one()
        result["ranking"] = {"valid": 0, "fallback": 2, "errors": ["primary: 429 quota"]}
        return result

    World(tmp_path, monkeypatch, first=fallback)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert "2 picked from the transcript" in read(job_dir)["message"]


def test_audio_on_its_own_fails_so_the_whole_video_goes_up(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)

    def refused(job, on_progress=None):
        raise RuntimeError("YouTube's choice for this video (18) has no separate audio stream")

    monkeypatch.setattr(phases, "download_youtube_audio", refused)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert world.uploads == [("input.mp4", f"/{JOB_ID}/input.mp4")]
    assert world.spawns[0][1][3:] == ("parakeet", "input.mp4") and world.spawns[1][1][2] is None
    data = read(job_dir)
    assert data["videoTransfer"] == "laptop-download" and "downloadWait" not in data["timings"]
    assert {"download", "upload"} <= set(data["timings"])


def test_a_missing_ffmpeg_executable_falls_back_to_the_whole_video(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    (job_dir / "input.mp4").write_bytes(b"x" * 4096)
    world = World(tmp_path, monkeypatch)

    def no_ffmpeg(source, out_dir):
        raise FileNotFoundError(2, "No such file or directory", "C:\\gone\\ffmpeg.exe")

    monkeypatch.setattr(phases.cutter, "extract_audio", no_ffmpeg)
    logged = []
    monkeypatch.setattr(phases, "log", logged.append)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert world.uploads[0] == ("input.mp4", f"/{JOB_ID}/input.mp4")
    assert any("Could not get the audio on its own (FileNotFoundError" in line for line in logged)
    assert read(job_dir)["status"] == "done"


def test_a_missing_source_video_still_raises_from_the_audio_step(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")      # no input.mp4
    World(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError, match="add the same MP4 again"):
        phases.audio_on_modal(jobstore.Job(job_dir), None, {})


def test_a_video_that_cannot_be_cut_is_uploaded_whole_for_phase_two(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)

    def unsupported(video, moments, out_dir):
        raise cutter.Unsupported("the video codec is vp9, not H.264")

    monkeypatch.setattr(phases.cutter, "make_footage", unsupported)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert [r for _, r in world.uploads] == [f"/{JOB_ID}/audio.m4a", f"/{JOB_ID}/input.mp4"]
    assert world.spawns[1][1][2] is None                           # render_clips reads input.mp4
    timings = read(job_dir)["timings"]
    assert "upload" in timings and "clipUpload" not in timings and "cut" in timings


def test_an_old_deployment_fails_before_anything_is_uploaded(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    world.missing_functions = True
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    assert "modal deploy main.py" in read(job_dir)["error"]
    assert world.uploads == [] and world.spawns == []


def test_a_lost_connection_while_waiting_is_ridden_out(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch, polls=4)
    world.flaky = [world.modal.exception.ConnectionError("laptop slept"), OSError("network is unreachable")]
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert read(job_dir)["status"] == "done"


def test_a_stop_signal_during_the_download_cancels_phase_one(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch, polls=10_000)
    monkeypatch.setattr(phases, "download_youtube", world.video_download(fail=KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        pipeline.run_job(job_dir, 5, keep_remote=False)
    assert world.calls["transcribe_and_rank"].cancelled and world.removed == [f"/{JOB_ID}"]
    assert read(job_dir)["status"] == "failed" and read(job_dir)["message"] == "Stopped"


def test_a_youtube_download_kept_from_a_failed_run_is_reused(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    monkeypatch.setattr(phases, "render_on_modal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("GPU")))
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    assert (tmp_path / "downloads" / JOB_ID / "input.mp4").exists()       # for Try again
    assert not (tmp_path / "downloads" / JOB_ID / "footage").exists()
    assert not (tmp_path / "downloads" / JOB_ID / "audio").exists()
    assert world.removed[-1] == f"/{JOB_ID}"


# --------------------------------------------------------------------------- #
# Re-rank
# --------------------------------------------------------------------------- #

def done_job(tmp_path, **fields):
    fields = {"status": "done", "clips": ["clip_0.mp4"], "engine": {"transcriber": "parakeet"}, **fields}
    job_dir = make_job(tmp_path, **fields)
    (job_dir / "clips").mkdir()
    (job_dir / "clips" / "clip_0.mp4").write_bytes(b"old")
    (job_dir / "transcript.json").write_text("[]")
    (job_dir / "moments.json").write_text("[]")
    return job_dir


def test_rerank_of_a_youtube_job_ranks_while_the_video_downloads(tmp_path, monkeypatch):
    job_dir = done_job(tmp_path)
    world = World(tmp_path, monkeypatch)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert world.uploads[0] == ("transcript.json", f"/{JOB_ID}/transcript.json")
    assert world.spawns[0][1][3] == "reuse" and "audio download" not in world.events
    assert world.events.index("spawn transcribe_and_rank") < world.events.index("video download (audio None)")
    data = read(job_dir)
    assert data["reranks"] == 1 and data["clips"] == ["clip_0.mp4"]
    assert data["comparison"]["new"]["clips"] == ["clip_0.mp4", "clip_1.mp4"]
    assert (job_dir / "clips_new" / "clip_0.mp4").read_bytes() == b"clip 0"
    assert (job_dir / "clips" / "clip_0.mp4").read_bytes() == b"old"


def test_rerank_of_a_legacy_whistle_job_uses_the_whole_video(tmp_path, monkeypatch):
    job_dir = done_job(tmp_path, engine={"transcriber": "whistle"})
    world = World(tmp_path, monkeypatch)
    first = phase_one()
    first["clipWords"] = [[{"word": "a"}], [{"word": "b"}]]
    world.outcomes["transcribe_and_rank"] = lambda call: (world.phase_one(call), first)[1]
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert [r for _, r in world.uploads] == [f"/{JOB_ID}/input.mp4", f"/{JOB_ID}/transcript.json"]
    assert world.spawns[0][1][3:] == ("uploaded", "input.mp4")
    assert world.spawns[1][1][2:] == (None, [[{"word": "a"}], [{"word": "b"}]])


def test_rerank_of_an_upload_without_its_file_asks_for_it(tmp_path, monkeypatch):
    job_dir = done_job(tmp_path, source="upload", youtubeUrl=None)
    world = World(tmp_path, monkeypatch)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 1
    data = read(job_dir)
    assert "add the same MP4 again" in data["error"] and data["clips"] == ["clip_0.mp4"]
    assert world.spawns == [] and world.uploads == []


# --------------------------------------------------------------------------- #
# Videos kept on the laptop for re-ranks (videostore.py)
# --------------------------------------------------------------------------- #

def kept_video(job_dir, content=b"kept video"):
    """Put a video in the store for this job, as an earlier successful run would have."""
    job = jobstore.Job(job_dir)
    source = job_dir / "kept-source.mp4"
    source.write_bytes(content)
    assert videostore.keep(job, source)
    return videostore.find(job)


def real_download_without_network(monkeypatch):
    """The real youtube.download_youtube, whose yt-dlp step must never be reached."""
    def no_download(*args, **kwargs):
        raise AssertionError("the kept video must be used: no download")

    monkeypatch.setattr(downloader, "download", no_download)
    monkeypatch.setattr(phases, "download_youtube", youtube.download_youtube)
    monkeypatch.setattr(transfer, "download_youtube", youtube.download_youtube)


def test_a_youtube_run_keeps_its_video_for_re_ranks(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    World(tmp_path, monkeypatch)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    video = videostore.find(jobstore.Job(job_dir))
    assert video is not None and video.read_bytes() == b"video"
    assert not (tmp_path / "downloads" / JOB_ID).exists() and not (job_dir / "input.mp4").exists()
    assert read(job_dir)["keptVideo"] is True
    status = json.loads((tmp_path / "stats" / "video-store.json").read_text())
    assert status["count"] == 1 and "dir" in status


def test_keeping_off_deletes_the_video_after_success_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_KEEP_DAYS", "0")
    job_dir = make_job(tmp_path)
    World(tmp_path, monkeypatch)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert videostore.find(jobstore.Job(job_dir)) is None and not (tmp_path / "downloads" / JOB_ID).exists()
    assert read(job_dir)["keptVideo"] is False
    upload_dir = tmp_path / "up"
    up = make_job(upload_dir, source="upload", youtubeUrl=None, title="talk.mp4")
    (up / "input.mp4").write_bytes(b"x" * 4096)
    World(upload_dir, monkeypatch)
    assert pipeline.run_job(up, 5, keep_remote=False) == 0
    assert not (up / "input.mp4").exists() and videostore.find(jobstore.Job(up)) is None


def test_a_youtube_rerank_uses_the_kept_video_without_downloading(tmp_path, monkeypatch):
    job_dir = done_job(tmp_path)
    kept = kept_video(job_dir)
    world = World(tmp_path, monkeypatch)
    real_download_without_network(monkeypatch)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert "cut input.mp4" in world.events and read(job_dir)["reranks"] == 1
    assert kept.read_bytes() == b"kept video" and videostore.find(jobstore.Job(job_dir)) == kept
    assert read(job_dir)["keptVideo"] is True and not (job_dir / "input.mp4").exists()


def test_the_whole_video_fallback_never_deletes_the_kept_video(tmp_path, monkeypatch):
    job_dir = done_job(tmp_path)
    kept = kept_video(job_dir)
    world = World(tmp_path, monkeypatch)
    real_download_without_network(monkeypatch)

    def unsupported(video, moments, out_dir):
        raise cutter.Unsupported("two audio streams")

    monkeypatch.setattr(phases.cutter, "make_footage", unsupported)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert ("input.mp4", f"/{JOB_ID}/input.mp4") in world.uploads
    assert kept.read_bytes() == b"kept video"


def test_a_legacy_whole_video_rerank_uploads_the_kept_video_and_keeps_it(tmp_path, monkeypatch):
    job_dir = done_job(tmp_path, engine={"transcriber": "whistle"})
    kept = kept_video(job_dir)
    world = World(tmp_path, monkeypatch)
    real_download_without_network(monkeypatch)
    first = phase_one()
    first["clipWords"] = [[{"word": "a"}], [{"word": "b"}]]
    world.outcomes["transcribe_and_rank"] = lambda call: (world.phase_one(call), first)[1]
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert world.uploads[0] == ("input.mp4", f"/{JOB_ID}/input.mp4")
    assert kept.read_bytes() == b"kept video" and videostore.holds(kept)


def test_an_upload_run_moves_its_video_into_the_store_and_reranks_without_the_file(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path, source="upload", youtubeUrl=None, title="talk.mp4")
    (job_dir / "input.mp4").write_bytes(b"x" * 4096)
    World(tmp_path, monkeypatch)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    kept = videostore.find(jobstore.Job(job_dir))
    assert kept is not None and kept.read_bytes() == b"x" * 4096 and not (job_dir / "input.mp4").exists()
    data = read(job_dir)
    assert data["fingerprint"] and data["keptVideo"] is True

    world = World(tmp_path, monkeypatch)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 0
    assert "cut input.mp4" in world.events and read(job_dir)["reranks"] == 1
    assert kept.read_bytes() == b"x" * 4096 and not (job_dir / "input.mp4").exists()


def test_a_failed_rerank_leaves_the_kept_video_for_try_again(tmp_path, monkeypatch):
    job_dir = done_job(tmp_path, source="upload", youtubeUrl=None)
    kept = kept_video(job_dir)
    World(tmp_path, monkeypatch)
    monkeypatch.setattr(phases, "render_on_modal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("GPU")))
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False) == 1
    assert kept.read_bytes() == b"kept video"
    assert phases.local_upload(jobstore.Job(job_dir)) == kept


def test_a_failed_youtube_run_keeps_its_download_in_the_temp_folder(tmp_path, monkeypatch):
    job_dir = make_job(tmp_path)
    World(tmp_path, monkeypatch)
    monkeypatch.setattr(phases, "render_on_modal", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("GPU")))
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 1
    assert (tmp_path / "downloads" / JOB_ID / "input.mp4").exists()
    assert videostore.find(jobstore.Job(job_dir)) is None and "keptVideo" not in read(job_dir)


def test_keep_video_wins_over_the_store(tmp_path, monkeypatch):
    job_dir = done_job(tmp_path)
    kept_video(job_dir)
    World(tmp_path, monkeypatch)
    real_download_without_network(monkeypatch)
    assert pipeline.run_rerank(job_dir, 5, keep_remote=False, keep_video=True) == 0
    assert (job_dir / "input.mp4").read_bytes() == b"kept video"
    assert videostore.find(jobstore.Job(job_dir)) is None and read(job_dir)["keptVideo"] is False

    fresh = tmp_path / "fresh"
    new_dir = make_job(fresh)
    World(fresh, monkeypatch)
    assert pipeline.run_job(new_dir, 5, keep_remote=False, keep_video=True) == 0
    assert (new_dir / "input.mp4").read_bytes() == b"video" and videostore.find(jobstore.Job(new_dir)) is None


def test_a_run_removes_the_kept_video_of_a_deleted_job_first(tmp_path, monkeypatch):
    gone_id = "20261101-100000-aaaaaa"
    gone = make_job(tmp_path, id=gone_id)
    gone.rename(tmp_path / "jobs" / gone_id)
    kept_video(tmp_path / "jobs" / gone_id)
    shutil.rmtree(tmp_path / "jobs" / gone_id)          # the user deleted that job
    job_dir = make_job(tmp_path)
    World(tmp_path, monkeypatch)
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert sorted(p.name for p in videostore.videos_dir().iterdir()) == [JOB_ID]


# --------------------------------------------------------------------------- #
# Backward compatibility, progress and cost
# --------------------------------------------------------------------------- #

def test_a_pending_save_from_the_single_phase_version_still_resumes(tmp_path, monkeypatch):
    old_result = {"clips": ["clip_0.mp4"], "moments": [{"rank": 1, "start": 1.0, "end": 40.0}], "errors": [],
                  "subtitles": "parakeet", "timings": {"transcribe": 40, "select": 50, "render": 60},
                  "transcription": {}, "gpu": "A10", "ranking": {"valid": 1}, "cost": {"usd": 0.1},
                  "clipTimings": [], "usages": [], "orchestratorSeconds": 150.0,
                  "orchestrator": {"cpu": 0.125, "memoryGiB": 0.125}}
    job_dir = make_job(tmp_path, status="failed",
                       pendingSave=transfer.pending_record("full", old_result, {"upload": 400.0}, 600))
    world = World(tmp_path, monkeypatch)
    world.files[f"/{JOB_ID}/clips/clip_0.mp4"] = b"kept"
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    assert world.spawns == [] and (job_dir / "clips" / "clip_0.mp4").read_bytes() == b"kept"


def test_a_failed_save_remembers_how_the_video_was_sent(tmp_path, monkeypatch):
    job = jobstore.Job(make_job(tmp_path, videoTransfer="audio-first"))

    def offline(*args, **kwargs):
        raise ConnectionError("getaddrinfo failed")

    monkeypatch.setattr(pipeline, "download_outputs", offline)
    with pytest.raises(ConnectionError):
        pipeline.save_clips(job, None, {"clips": ["clip_0.mp4"]}, None, "full", {"audio": 40.0}, 0.0)
    assert read(job.dir)["pendingSave"]["videoTransfer"] == "audio-first"


def test_try_again_after_a_failed_save_keeps_how_the_video_was_sent(tmp_path, monkeypatch):
    """Every attempt clears videoTransfer; one that only downloads the kept clips puts back
    the value of the attempt that rendered them (the app names the steps from it)."""
    result = {**phases.merge_results(phase_one(), phase_two()), "clips": ["clip_0.mp4"]}
    pending = transfer.pending_record("full", result, {"audio": 40.0}, 300, "audio-first")
    job_dir = make_job(tmp_path, status="failed", failedStage="saving", videoTransfer="audio-first",
                       pendingSave=pending)
    world = World(tmp_path, monkeypatch)
    world.files[f"/{JOB_ID}/clips/clip_0.mp4"] = b"kept"
    assert pipeline.run_job(job_dir, 5, keep_remote=False) == 0
    data = read(job_dir)
    assert world.spawns == [] and data["status"] == "done" and data["videoTransfer"] == "audio-first"
    assert data.get("pendingSave") is None and data.get("failedStage") is None


def test_one_progress_bar_spans_several_files(tmp_path):
    job = jobstore.Job(make_job(tmp_path))
    total = transfer.UploadProgress(job, 300, every_s=0, stage="clips")
    a, b = transfer.FileProgress(total), transfer.FileProgress(total)
    a(advance=100)
    a(reset=True)                       # Modal starts that file over: only its bytes are taken back
    a(advance=100)
    b(advance=150)
    assert total.sent == 250 and read(job.dir)["progress"]["stage"] == "clips"
    assert read(job.dir)["progress"]["fraction"] == pytest.approx(250 / 300, abs=0.001)


def test_two_calls_cost_exactly_their_sum():
    first, second = phase_one(), phase_two()
    merged = phases.merge_results(first, second)
    alone = [costs.estimate(r["usages"], r["orchestratorSeconds"], orchestrator=r["orchestrator"])
             for r in (first, second)]
    assert merged["cost"]["usd"] == pytest.approx(sum(c["usd"] for c in alone), abs=2e-4)
    for stage in costs.STAGES:
        assert merged["cost"]["stages"][stage] == pytest.approx(sum(c["stages"][stage] for c in alone), abs=2e-5)
    assert merged["timings"] == {"transcribe": 41.0, "select": 50.0, "render": 64.0}
    assert merged["orchestratorSeconds"] == 165.0 and merged["moments"] == MOMENTS
    assert phases.merge_results(first, None)["clips"] == []


def test_cleanup_with_kept_clips_removes_every_uploaded_medium(tmp_path):
    removed = []
    volume = types.SimpleNamespace(remove_file=lambda path, recursive=False: removed.append(path))
    transfer.cleanup_remote(jobstore.Job(make_job(tmp_path)), volume, keep_clips=True)
    assert removed == [f"/{JOB_ID}/input.mp4", f"/{JOB_ID}/audio.m4a", f"/{JOB_ID}/audio.flac",
                       f"/{JOB_ID}/footage"]


# --------------------------------------------------------------------------- #
# YouTube formats (real yt-dlp, no network: a local web server)
# --------------------------------------------------------------------------- #

@pytest.fixture
def web(tmp_path):
    root = tmp_path / "www"
    root.mkdir()

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def youtube_formats(base, with_30fps):
    def fmt(fid, ext, vcodec, acodec, height=None, fps=None, abr=None):
        return {"format_id": fid, "url": f"{base}/f{fid}", "ext": ext, "vcodec": vcodec, "acodec": acodec,
                "height": height, "width": height and height * 16 // 9, "fps": fps, "abr": abr,
                "protocol": "http", "filesize": 4000}

    formats = [fmt("140", "m4a", "none", "mp4a.40.2", abr=129), fmt("251", "webm", "none", "opus", abr=140),
               fmt("135", "mp4", "avc1.4d401f", "none", 480, 30), fmt("298", "mp4", "avc1.4d4020", "none", 720, 60),
               fmt("299", "mp4", "avc1.64002a", "none", 1080, 60), fmt("303", "webm", "vp9", "none", 1080, 60),
               fmt("18", "mp4", "avc1.42001E", "mp4a.40.2", 360, 30)]
    if with_30fps:
        formats += [fmt("137", "mp4", "avc1.640028", "none", 1080, 30),
                    fmt("136", "mp4", "avc1.4d401f", "none", 720, 30)]
    return {"id": "x", "title": "A talk", "formats": formats, "extractor": "generic", "extractor_key": "Generic",
            "webpage_url": f"{base}/watch"}


@pytest.mark.parametrize("with_30fps, expected", [(False, "299+140"), (True, "137+140")])
def test_the_format_choice_prefers_30_fps_at_the_same_resolution(web, with_30fps, expected):
    yt_dlp = pytest.importorskip("yt_dlp")
    _, base = web
    options = {**downloader.ydl_options(pathlib.Path(".")), "quiet": True, "simulate": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        assert ydl.process_ie_result(youtube_formats(base, with_30fps), download=False)["format_id"] == expected
    options["format_sort"] = []                       # before: 60 fps whenever YouTube has it
    with yt_dlp.YoutubeDL(options) as ydl:
        assert ydl.process_ie_result(youtube_formats(base, with_30fps), download=False)["format_id"] == "299+140"


def test_the_audio_alone_is_the_stream_the_video_will_merge_in(web, tmp_path, monkeypatch):
    yt_dlp = pytest.importorskip("yt_dlp")
    root, base = web
    for fid in ("140", "251", "135", "137", "299", "18"):
        (root / f"f{fid}").write_bytes(fid.encode() * 1000)
    info = youtube_formats(base, True)

    def extract_info(self, url, download=False):            # YouTube's page, without the network
        assert download is False
        return self.process_ie_result(json.loads(json.dumps(info)), download=False)

    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", extract_info)
    fractions = []
    path, format_id, got = downloader.download_audio(f"{base}/watch", tmp_path / "audio", ipv4=False,
                                                     on_progress=fractions.append)
    assert format_id == "140" and path.name == "audio.m4a" and path.read_bytes() == b"140" * 1000
    assert got["title"] == "A talk" and fractions and not list((tmp_path / "audio").glob("*.part"))
    assert downloader.pinned_format("140").startswith("bv*[vcodec^=avc1][height<=1080]+140/")
    assert downloader.pinned_format(None) == downloader.FORMAT


def test_no_separate_audio_means_no_audio_first(web, tmp_path, monkeypatch):
    yt_dlp = pytest.importorskip("yt_dlp")
    _, base = web
    info = youtube_formats(base, False)
    info["formats"] = [f for f in info["formats"] if f["format_id"] == "18"]      # one progressive file
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info",
                        lambda self, url, download=False: self.process_ie_result(info, download=False))
    with pytest.raises(RuntimeError, match="no separate audio stream"):
        downloader.download_audio(f"{base}/watch", tmp_path / "audio", ipv4=False)


def test_a_progress_callback_can_stop_the_real_download(web, tmp_path, monkeypatch):
    """phases.StopVideoDownload raised in the progress hook ends yt-dlp's download and leaves
    the partial file for Try again."""
    yt_dlp = pytest.importorskip("yt_dlp")
    root, base = web
    (root / "f140").write_bytes(b"a" * 3_000_000)
    info = youtube_formats(base, False)
    info["formats"] = [dict(f, filesize=3_000_000) for f in info["formats"] if f["format_id"] in ("140", "135")]
    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info",
                        lambda self, url, download=False: self.process_ie_result(info, download=False))

    def stop(fraction):
        raise phases.StopVideoDownload()

    with pytest.raises(phases.StopVideoDownload):
        downloader.download_audio(f"{base}/watch", tmp_path / "a", ipv4=False, on_progress=stop)
    shutil.rmtree(tmp_path / "a", ignore_errors=True)
