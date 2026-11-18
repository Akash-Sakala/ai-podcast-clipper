"""Videos kept on the laptop for re-ranks (videostore.py): keep, find, use, the 7-day rule,
the size cap (least recently used first), never the current or a running job, deleted jobs of
this data folder only, a locked file, keeping switched off, the status file for the web app
and --prune-videos.

    python -m pytest tests/test_videostore.py -q
"""

import datetime as dt
import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import jobstore  # noqa: E402
import maintenance  # noqa: E402
import videostore  # noqa: E402

DAY = 86400


def make_job(data_dir, job_id, **fields):
    job_dir = data_dir / "jobs" / job_id
    job_dir.mkdir(parents=True)
    data = {"id": job_id, "source": "youtube", "title": "A talk", "status": "done", **fields}
    (job_dir / "job.json").write_text(json.dumps(data), encoding="utf-8")
    return jobstore.Job(job_dir)


def kept(data_dir, job_id, size=100, days_ago=0.0, now=None, **fields):
    """A job with a video kept `days_ago` days before `now`."""
    job = make_job(data_dir, job_id, **fields)
    source = job.dir / "input.mp4"
    source.write_bytes(b"v" * size)
    assert videostore.keep(job, source)
    meta_file = videostore.videos_dir() / job_id / "video.json"
    meta = json.loads(meta_file.read_text())
    stamp = dt.datetime.fromtimestamp((now or dt.datetime.now().timestamp()) - days_ago * DAY, dt.timezone.utc)
    meta["lastUsedAt"] = stamp.isoformat().replace("+00:00", "Z")
    meta_file.write_text(json.dumps(meta))
    return job


def ids():
    root = videostore.videos_dir()
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def test_the_store_is_redirected_by_the_tests():
    assert videostore.videos_dir() == pathlib.Path(os.environ["VIDEO_CACHE_DIR"])
    assert "kept-videos" in videostore.videos_dir().name


def test_default_folder_is_outside_onedrive(monkeypatch):
    monkeypatch.delenv("VIDEO_CACHE_DIR")
    monkeypatch.setenv("XDG_DATA_HOME", "/data/share")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\a\AppData\Local")
    expected = (pathlib.Path(r"C:\Users\a\AppData\Local") if os.name == "nt" else pathlib.Path("/data/share"))
    assert videostore.videos_dir() == expected / "ai-podcast-clipper" / "videos"


def test_keep_moves_the_file_and_find_use_holds(tmp_path):
    job = make_job(tmp_path, "20261115-101500-ab12cd")
    source = job.dir / "input.mp4"
    source.write_bytes(b"video")
    assert videostore.find(job) is None
    assert videostore.keep(job, source)
    assert not source.exists()
    video = videostore.find(job)
    assert video is not None and video.read_bytes() == b"video" and videostore.holds(video)
    assert not videostore.holds(source) and not list(video.parent.glob("*.part"))
    meta = json.loads((video.parent / "video.json").read_text())
    assert meta["jobId"] == job.id and meta["dataDir"] == str(tmp_path.resolve()) and meta["bytes"] == 5
    assert meta["source"] == "youtube" and meta["title"] == "A talk" and meta["keptAt"] == meta["lastUsedAt"]
    meta["lastUsedAt"] = "2026-01-01T00:00:00Z"
    (video.parent / "video.json").write_text(json.dumps(meta))
    assert videostore.use(job) == video
    assert json.loads((video.parent / "video.json").read_text())["lastUsedAt"] > "2026-01-01T00:00:00Z"
    status = json.loads((tmp_path / "stats" / "video-store.json").read_text())
    assert status["count"] == 1 and status["bytes"] == 5 and status["keepDays"] == 7 and status["maxGb"] == 10


def test_an_empty_kept_file_is_not_found(tmp_path):
    job = make_job(tmp_path, "20261115-101500-ab12cd")
    (videostore.videos_dir() / job.id).mkdir(parents=True)
    (videostore.videos_dir() / job.id / "input.mp4").write_bytes(b"")
    assert videostore.find(job) is None and videostore.use(job) is None


def test_a_cross_drive_move_copies_then_deletes(tmp_path, monkeypatch):
    job = make_job(tmp_path, "20261115-101500-ab12cd")
    source = job.dir / "input.mp4"
    source.write_bytes(b"video")
    real_replace = os.replace

    def no_rename(a, b):
        if pathlib.Path(a) == source:
            raise OSError(18, "Invalid cross-device link")
        return real_replace(a, b)

    monkeypatch.setattr(videostore.os, "replace", no_rename)
    assert videostore.keep(job, source)
    assert not source.exists() and videostore.find(job).read_bytes() == b"video"


def test_a_failed_move_keeps_the_source_and_no_part(tmp_path, monkeypatch):
    job = make_job(tmp_path, "20261115-101500-ab12cd")
    source = job.dir / "input.mp4"
    source.write_bytes(b"video")

    def broken(a, b):
        raise PermissionError("in use")

    monkeypatch.setattr(videostore, "_move", broken)
    assert videostore.keep(job, source) is False
    assert source.read_bytes() == b"video" and videostore.find(job) is None
    assert not list((videostore.videos_dir() / job.id).glob("*.part"))


def test_keeping_off_keeps_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_KEEP_DAYS", "0")
    job = make_job(tmp_path, "20261115-101500-ab12cd")
    (job.dir / "input.mp4").write_bytes(b"video")
    assert videostore.keep(job, job.dir / "input.mp4") is False
    assert (job.dir / "input.mp4").exists() and videostore.find(job) is None


def test_a_video_larger_than_the_cap_is_not_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_CACHE_MAX_GB", str(50 / 1024 ** 3))       # 50 bytes
    job = make_job(tmp_path, "20261115-101500-ab12cd")
    (job.dir / "input.mp4").write_bytes(b"v" * 100)
    assert videostore.keep(job, job.dir / "input.mp4") is False
    assert (job.dir / "input.mp4").exists()


def test_videos_not_used_for_seven_days_go(tmp_path):
    now = dt.datetime.now().timestamp()
    kept(tmp_path, "20261101-100000-aaaaaa", days_ago=8, now=now)
    kept(tmp_path, "20261110-100000-bbbbbb", days_ago=6, now=now)
    assert videostore.cleanup(tmp_path, now=now) == ["20261101-100000-aaaaaa"]
    assert ids() == ["20261110-100000-bbbbbb"]


def test_the_cap_removes_the_least_recently_used_first(tmp_path, monkeypatch):
    now = dt.datetime.now().timestamp()
    kept(tmp_path, "20261101-100000-aaaaaa", size=100, days_ago=3, now=now)
    kept(tmp_path, "20261102-100000-bbbbbb", size=100, days_ago=1, now=now)
    kept(tmp_path, "20261103-100000-cccccc", size=100, days_ago=2, now=now)
    one = max(e["bytes"] for e in videostore._entries())          # a video and its video.json
    monkeypatch.setenv("VIDEO_CACHE_MAX_GB", str((one + 10) / 1024 ** 3))   # room for one: two must go
    removed = videostore.cleanup(tmp_path, now=now)
    assert removed == ["20261101-100000-aaaaaa", "20261103-100000-cccccc"]
    assert ids() == ["20261102-100000-bbbbbb"]


def test_keep_enforces_the_cap_but_never_on_the_job_just_kept(tmp_path, monkeypatch):
    old = kept(tmp_path, "20261101-100000-aaaaaa", size=300, days_ago=1)
    assert old
    monkeypatch.setenv("VIDEO_CACHE_MAX_GB", str(400 / 1024 ** 3))
    job = make_job(tmp_path, "20261115-101500-ab12cd")
    (job.dir / "input.mp4").write_bytes(b"v" * 300)
    assert videostore.keep(job, job.dir / "input.mp4")
    assert ids() == ["20261115-101500-ab12cd"]


def test_never_the_current_or_a_running_job(tmp_path, monkeypatch):
    now = dt.datetime.now().timestamp()
    kept(tmp_path, "20261101-100000-aaaaaa", days_ago=30, now=now)
    running = kept(tmp_path, "20261102-100000-bbbbbb", days_ago=30, now=now)
    running.update(status="processing", pid=os.getpid())
    kept(tmp_path, "20261103-100000-cccccc", days_ago=30, now=now)
    assert videostore.cleanup(tmp_path, current_job="20261101-100000-aaaaaa", now=now) == ["20261103-100000-cccccc"]
    assert ids() == ["20261101-100000-aaaaaa", "20261102-100000-bbbbbb"]
    assert videostore.clear(tmp_path) == (1, 100)        # the running job's video stays
    assert ids() == ["20261102-100000-bbbbbb"]


def test_a_deleted_job_loses_its_video_in_its_own_data_folder_only(tmp_path):
    mine, other = tmp_path / "mine", tmp_path / "other"
    a = kept(mine, "20261101-100000-aaaaaa")
    kept(other, "20261102-100000-bbbbbb")
    import shutil
    shutil.rmtree(a.dir)
    shutil.rmtree(other / "jobs" / "20261102-100000-bbbbbb")       # deleted too, but in another data folder
    assert videostore.cleanup(mine) == ["20261101-100000-aaaaaa"]
    assert ids() == ["20261102-100000-bbbbbb"]


def test_foreign_folders_follow_the_age_rule_by_mtime(tmp_path):
    root = videostore.videos_dir()
    for name in ("notes", "20261101-100000-aaaaaa"):            # no video.json
        (root / name).mkdir(parents=True)
        (root / name / "input.mp4").write_bytes(b"x")
        old = dt.datetime.now().timestamp() - 9 * DAY
        os.utime(root / name / "input.mp4", (old, old))
    (root / "fresh").mkdir()
    (root / "fresh" / "file").write_bytes(b"x")
    assert sorted(videostore.cleanup(tmp_path)) == ["20261101-100000-aaaaaa", "notes"]
    assert ids() == ["fresh"]


def test_a_locked_video_is_skipped(tmp_path, monkeypatch):
    now = dt.datetime.now().timestamp()
    kept(tmp_path, "20261101-100000-aaaaaa", days_ago=30, now=now)
    kept(tmp_path, "20261102-100000-bbbbbb", days_ago=30, now=now)
    locked = videostore.videos_dir() / "20261101-100000-aaaaaa" / "input.mp4"
    real_unlink = pathlib.Path.unlink

    def unlink(self, missing_ok=False):
        if self == locked:
            raise PermissionError(32, "The process cannot access the file because it is being used")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    assert videostore.cleanup(tmp_path, now=now) == ["20261102-100000-bbbbbb"]
    assert locked.exists() and (locked.parent / "video.json").exists()


def test_keeping_off_removes_what_was_kept(tmp_path, monkeypatch):
    kept(tmp_path, "20261101-100000-aaaaaa")
    monkeypatch.setenv("VIDEO_KEEP_DAYS", "0")
    assert videostore.cleanup(tmp_path, now=dt.datetime.now().timestamp() + 1) == ["20261101-100000-aaaaaa"]


def test_write_status(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_CACHE_MAX_GB", "2.5")
    monkeypatch.setenv("VIDEO_KEEP_DAYS", "3")
    kept(tmp_path, "20261101-100000-aaaaaa", size=10)
    kept(tmp_path, "20261102-100000-bbbbbb", size=20)
    videostore.write_status(tmp_path)
    status = json.loads((tmp_path / "stats" / "video-store.json").read_text())
    assert status["dir"] == str(videostore.videos_dir().resolve())
    assert (status["count"], status["bytes"], status["keepDays"], status["maxGb"]) == (2, 30, 3.0, 2.5)
    assert status["updatedAt"].endswith("Z")
    assert not list((tmp_path / "stats").glob("*.tmp"))


def test_prune_videos_clears_the_store_and_respects_dry_run(tmp_path):
    kept(tmp_path, "20261101-100000-aaaaaa", size=10)
    kept(tmp_path, "20261102-100000-bbbbbb", size=20)
    assert maintenance.prune_videos(tmp_path, dry_run=True) == 0
    assert len(ids()) == 2
    assert maintenance.prune_videos(tmp_path) == 0
    assert ids() == []
    assert json.loads((tmp_path / "stats" / "video-store.json").read_text())["count"] == 0


@pytest.mark.parametrize("bad", ["", "not json", "[]"])
def test_a_broken_video_json_counts_as_foreign(tmp_path, bad):
    root = videostore.videos_dir() / "20261101-100000-aaaaaa"
    root.mkdir(parents=True)
    (root / "input.mp4").write_bytes(b"x")
    (root / "video.json").write_text(bad)
    assert videostore.cleanup(tmp_path) == []                 # fresh by mtime
    assert videostore.summary()["count"] == 1
