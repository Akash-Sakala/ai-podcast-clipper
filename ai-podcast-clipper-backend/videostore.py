"""Videos kept on this laptop after a successful run, so a re-rank needs no new download
(YouTube) and no file added again (an upload). Laptop only: never bundled into the Modal image.

Layout: <videos dir>/<job id>/input.mp4 and <job id>/video.json
        {"jobId", "dataDir", "source", "title", "bytes", "keptAt", "lastUsedAt"}.
A video is kept for VIDEO_KEEP_DAYS after its last use; the whole store stays under
VIDEO_CACHE_MAX_GB (least recently used removed first). cleanup() runs at the start of every
run; write_status() tells the web app (data/stats/video-store.json) where the store is.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import shutil
import time

import settings
from jobstore import Job, app_data_root, log, now_iso, pid_alive, replace_file

VIDEO = "input.mp4"
META = "video.json"
STATUS_FILE = "video-store.json"           # in <data dir>/stats
JOB_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")
FINAL_STATUSES = ("done", "failed")
GB = 1024 ** 3


def videos_dir() -> pathlib.Path:
    """VIDEO_CACHE_DIR, else %LOCALAPPDATA%\\ai-podcast-clipper\\videos on Windows (outside
    OneDrive), else $XDG_DATA_HOME (or ~/.local/share)/ai-podcast-clipper/videos."""
    custom = settings.video_cache_dir()
    if custom:
        return pathlib.Path(custom).expanduser()
    return app_data_root() / "videos"


def _data_dir(job: Job) -> pathlib.Path:
    return job.dir.resolve().parent.parent


def _same_dir(a, b) -> bool:
    try:
        return os.path.normcase(str(pathlib.Path(a).resolve())) == os.path.normcase(str(pathlib.Path(b).resolve()))
    except (OSError, TypeError, ValueError):
        return False


def _parse_time(value) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _read_meta(folder: pathlib.Path) -> dict | None:
    try:
        data = json.loads((folder / META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path: pathlib.Path, data: dict) -> bool:
    """Atomic write (temp file, then replace). Never raises."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        replace_file(tmp, path)
        return True
    except OSError as exc:
        log(f"Could not write {path.name} ({type(exc).__name__}: {exc})")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _size(path: pathlib.Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def holds(path) -> bool:
    """Whether this path is inside the store."""
    try:
        pathlib.Path(path).resolve().relative_to(videos_dir().resolve())
        return True
    except (ValueError, OSError, TypeError):
        return False


def find(job: Job) -> pathlib.Path | None:
    """The job's kept video, if there is one (and it is not empty)."""
    video = videos_dir() / job.id / VIDEO
    return video if _size(video) > 0 else None


def use(job: Job) -> pathlib.Path | None:
    """find(), and note the use (the 7 days start again)."""
    video = find(job)
    if video is not None:
        meta = _read_meta(video.parent) or _new_meta(job, video)
        meta["lastUsedAt"] = now_iso()
        _write_json(video.parent / META, meta)
    return video


def local_video(job: Job) -> pathlib.Path | None:
    """The video of a job on this laptop: input.mp4 in the job folder (a failed run's copy, or
    the file added again), else the kept one (noted as used). None when neither exists."""
    video = job.dir / VIDEO
    if video.exists():
        return video
    return use(job)


def _new_meta(job: Job, video: pathlib.Path) -> dict:
    stamp = now_iso()
    return {"jobId": job.id, "dataDir": str(_data_dir(job)), "source": job.data.get("source"),
            "title": job.data.get("title"), "bytes": _size(video), "keptAt": stamp, "lastUsedAt": stamp}


def _move(source: pathlib.Path, target: pathlib.Path) -> None:
    """Move a file: a rename on the same drive, else a copy then delete (shutil.move)."""
    try:
        os.replace(source, target)
    except OSError:
        shutil.move(str(source), str(target))


def keep(job: Job, source: pathlib.Path) -> bool:
    """Move a finished run's video into the store (then keep the store under its cap, never
    removing this job's video). False, with nothing deleted, when keeping is off, the file
    alone is larger than the cap, or the move fails: the caller then deletes it as before.
    Never raises."""
    try:
        source = pathlib.Path(source)
        days, max_gb = settings.video_keep_days(), settings.video_cache_max_gb()
        if days <= 0 or not source.is_file():
            return False
        size = _size(source)
        if holds(source):
            return use(job) is not None
        if max_gb > 0 and size > max_gb * GB:
            log(f"Not keeping the video ({size / GB:.1f} GB): larger than the {max_gb:g} GB for kept videos")
            return False
        folder = videos_dir() / job.id
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / (VIDEO + ".part")
        try:
            part.unlink(missing_ok=True)
            _move(source, part)               # a broken move never leaves a truncated input.mp4
            replace_file(part, folder / VIDEO)
        except OSError as exc:
            log(f"Could not keep the video ({type(exc).__name__}: {exc})")
            if source.exists():
                try:
                    part.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        meta = _new_meta(job, folder / VIDEO)
        _write_json(folder / META, meta)
        log(f"Kept the video for re-ranks ({size / GB:.2f} GB, {days:g} days after its last use)")
        _enforce_cap(protect={job.id})
        write_status(_data_dir(job))
        return True
    except Exception as exc:  # noqa: BLE001 - keeping is a convenience, never a reason to fail a run
        log(f"Could not keep the video ({type(exc).__name__}: {exc})")
        return False


def take_out(job: Job, target: pathlib.Path) -> bool:
    """Move the kept video out of the store to target (--keep-video wins over the store)."""
    video = find(job)
    if video is None:
        return False
    try:
        _move(video, target)
    except OSError as exc:
        log(f"Could not move the kept video into the job folder: {exc}")
        return False
    _remove(video.parent)
    return True


# --------------------------------------------------------------------------- #
# Clean-up
# --------------------------------------------------------------------------- #

def _entries() -> list[dict]:
    """Every folder in the store: {"folder", "id", "meta", "bytes" (all its files), "video" (input.mp4's
    size), "used" (epoch seconds)}."""
    root = videos_dir()
    try:
        folders = [f for f in root.iterdir() if f.is_dir()]
    except OSError:
        return []
    entries = []
    for folder in folders:
        meta = _read_meta(folder) if JOB_ID.fullmatch(folder.name) else None
        size = sum(_size(p) for p in folder.glob("*") if p.is_file())
        used = _parse_time((meta or {}).get("lastUsedAt"))
        if used is None:
            try:
                used = max([p.stat().st_mtime for p in folder.glob("*") if p.is_file()] or [folder.stat().st_mtime])
            except OSError:
                used = time.time()
        entries.append({"folder": folder, "id": folder.name, "meta": meta, "bytes": size,
                        "video": _size(folder / VIDEO), "used": used})
    return entries


def _job_running(job_dir: pathlib.Path) -> bool:
    """Like transfer.local_state's "active": a non-final status with a live pid or no pid yet,
    or a job.json that can't be read. A missing job.json is not running."""
    try:
        data = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    if not isinstance(data, dict):
        return True
    return data.get("status") not in FINAL_STATUSES and (not data.get("pid") or pid_alive(data["pid"]))


def _protected(entry: dict, current_job: str | None) -> bool:
    if entry["id"] == current_job:
        return True
    meta = entry["meta"]
    if meta and meta.get("dataDir"):
        return _job_running(pathlib.Path(meta["dataDir"]) / "jobs" / entry["id"])
    return False


def _remove(folder: pathlib.Path) -> bool:
    """Delete a store folder, its video first. False (nothing more deleted) when the video
    can't be deleted, e.g. open in a player on Windows."""
    try:
        (folder / VIDEO).unlink(missing_ok=True)
    except OSError as exc:
        log(f"Could not remove the kept video {folder.name} ({type(exc).__name__}); trying again next run")
        return False
    shutil.rmtree(folder, ignore_errors=True)
    return True


def _enforce_cap(protect: set[str], entries: list[dict] | None = None) -> list[dict]:
    """Remove the least recently used videos until the store is under VIDEO_CACHE_MAX_GB."""
    max_gb = settings.video_cache_max_gb()
    if max_gb <= 0:
        return []
    entries = _entries() if entries is None else entries
    total = sum(e["bytes"] for e in entries)
    removed = []
    for entry in sorted(entries, key=lambda e: e["used"]):
        if total <= max_gb * GB:
            break
        if entry["meta"] is None or entry["id"] in protect or _protected(entry, None):
            continue
        if _remove(entry["folder"]):
            total -= entry["bytes"]
            removed.append(entry)
    return removed


def cleanup(data_dir, current_job: str | None = None, now: float | None = None) -> list[str]:
    """Start of every run: remove kept videos (a) of jobs deleted from this data folder, (b) not
    used for VIDEO_KEEP_DAYS, (c) least recently used first until the store is under its cap.
    Never the current job or a running job. Returns the job ids removed. Never raises."""
    try:
        now = time.time() if now is None else now
        days = settings.video_keep_days()
        removed: list[str] = []
        left = []
        for entry in _entries():
            meta = entry["meta"]
            if _protected(entry, current_job):
                left.append(entry)
                continue
            if meta is None:                      # not one of ours, or a broken keep: age only
                gone = now - entry["used"] > max(days, 0) * 86400
            else:
                orphan = (_same_dir(meta.get("dataDir"), data_dir)
                          and not (pathlib.Path(data_dir) / "jobs" / entry["id"]).is_dir())
                gone = orphan or now - entry["used"] > days * 86400
            if gone and _remove(entry["folder"]):
                removed.append(entry["id"])
            else:
                left.append(entry)
        removed += [e["id"] for e in _enforce_cap({current_job} if current_job else set(), left)]
        if removed:
            log(f"Removed {len(removed)} kept video(s) not used for {days:g} days, of deleted jobs "
                f"or over the {settings.video_cache_max_gb():g} GB cap")
        return removed
    except Exception as exc:  # noqa: BLE001
        log(f"Could not clean up the kept videos ({type(exc).__name__}: {exc})")
        return []


def clear(data_dir=None) -> tuple[int, int]:
    """Remove every kept video except those of running jobs: (count, bytes)."""
    count = freed = 0
    for entry in _entries():
        if _protected(entry, None):
            continue
        if _remove(entry["folder"]):
            count, freed = count + 1, freed + entry["video"]
    if data_dir is not None:
        write_status(data_dir)
    return count, freed


def summary() -> dict:
    entries = [e for e in _entries() if (e["folder"] / VIDEO).is_file()]
    return {"dir": str(videos_dir().resolve()), "count": len(entries),
            "bytes": sum(_size(e["folder"] / VIDEO) for e in entries),
            "keepDays": settings.video_keep_days(), "maxGb": settings.video_cache_max_gb()}


def write_status(data_dir) -> None:
    """data/stats/video-store.json: where the store is and its limits, for the web app."""
    try:
        _write_json(pathlib.Path(data_dir) / "stats" / STATUS_FILE, {**summary(), "updatedAt": now_iso()})
    except Exception as exc:  # noqa: BLE001
        log(f"Could not write {STATUS_FILE} ({type(exc).__name__}: {exc})")
