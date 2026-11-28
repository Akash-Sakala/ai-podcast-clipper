"""The clip sets of a job folder: the live set in clips/, the new set a re-rank leaves in
clips_new/ for the owner to compare with it (job.json "comparison"), and putting a set in
place file by file (Windows: a clip may be open in the web app or a player)."""

import json
import os
import pathlib
import re
import shutil

from jobstore import Job, log, now_iso, replace_file
from media import save_clip_poster

CLIP_NAME = re.compile(r"clip_(\d+)\.mp4")
NEW_DIR = "clips_new"
FINAL_DIR = "clips_final"
BACKUP_DIR = "clips_previous"
NEW_MOMENTS = "moments.json.new"
SETS = ("new", "previous")


class ComparisonError(ValueError):
    """A selection that can't be applied; nothing was changed."""


def swap_in_new_clips(job: Job, new_dir: pathlib.Path) -> None:
    """Put the clips in new_dir (all downloaded or copied there) in place of the live ones.

    File by file, never by renaming the clips folder: on Windows a folder can't be renamed
    while any file in it is open (the web app streaming a clip, a player, OneDrive). Each
    old file first moves to clips_previous/, then the new one takes its name (each step
    retried while the file is locked). If a step still fails, the files already swapped
    are put back and the error is raised, so the old clips stay complete. Old clips with
    no new counterpart (fewer clips this time) are removed afterwards (best effort)."""
    clips_dir = job.dir / "clips"
    backup = job.dir / BACKUP_DIR
    shutil.rmtree(backup, ignore_errors=True)
    clips_dir.mkdir(exist_ok=True)
    backup.mkdir(exist_ok=True)
    new_files = sorted(p for p in new_dir.iterdir() if p.is_file() and not p.name.endswith(".part"))
    done: list[tuple[pathlib.Path, pathlib.Path | None]] = []
    try:
        for new in new_files:
            target = clips_dir / new.name
            saved = None
            if target.exists():
                saved = backup / new.name
                replace_file(target, saved)
            done.append((target, saved))
            replace_file(new, target)
    except OSError:
        for target, saved in reversed(done):
            try:
                if saved is not None:
                    replace_file(saved, target)
                else:
                    target.unlink(missing_ok=True)
            except OSError as exc:
                log(f"Could not restore {target.name}: {exc}")
        raise
    names = {p.name for p in new_files}
    for old in clips_dir.glob("clip_*"):
        if old.name not in names:
            try:
                replace_file(old, backup / old.name, attempts=4)
            except OSError as exc:
                log(f"Could not remove the old {old.name}: {exc}")
    shutil.rmtree(backup, ignore_errors=True)
    shutil.rmtree(new_dir, ignore_errors=True)


def previous_set_time(job: Job) -> str | None:
    """When the live set was made: the last re-rank, else the last successful run's end,
    else the job's creation."""
    if job.data.get("rerankedAt"):
        return job.data["rerankedAt"]
    for run in reversed(job.data.get("runs") or []):
        if isinstance(run, dict) and run.get("status") == "done" and run.get("finishedAt"):
            return run["finishedAt"]
    return job.data.get("createdAt")


def has_live_clips(job: Job) -> bool:
    clips_dir = job.dir / "clips"
    return any((clips_dir / name).exists() for name in job.data.get("clips") or [])


def discard_comparison(job: Job) -> None:
    """Drop an undecided new set (and any staging left by a killed apply): the live set
    stays as it is."""
    for name in (NEW_DIR, FINAL_DIR):
        shutil.rmtree(job.dir / name, ignore_errors=True)
    try:
        (job.dir / NEW_MOMENTS).unlink(missing_ok=True)
    except OSError:
        pass
    if job.data.get("comparison"):
        log("Discarded the new clips of the last re-rank that were never chosen from")
        job.update(comparison=None)


def _read_moments(path: pathlib.Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _index(name: str) -> int:
    return int(CLIP_NAME.fullmatch(name).group(1))


def validate_selection(job: Job, keep) -> list[tuple[str, str]]:
    """[(set, clip name)] or ComparisonError."""
    comparison = job.data.get("comparison")
    if not isinstance(comparison, dict):
        raise ComparisonError("There is no re-rank waiting for a choice")
    if not isinstance(keep, list) or not keep:
        raise ComparisonError("Choose at least one clip to keep")
    available = {"new": set((comparison.get("new") or {}).get("clips") or []),
                 "previous": set(job.data.get("clips") or [])}
    folders = {"new": job.dir / NEW_DIR, "previous": job.dir / "clips"}
    picks: list[tuple[str, str]] = []
    for item in keep:
        if not isinstance(item, dict):
            raise ComparisonError("Each kept clip needs a set and a clip name")
        which, name = item.get("set"), item.get("clip")
        if which not in SETS or not isinstance(name, str) or not CLIP_NAME.fullmatch(name):
            raise ComparisonError(f"Not a clip of either set: {item!r}")
        if name not in available[which] or not (folders[which] / name).is_file():
            raise ComparisonError(f"{name} is not in the {which} clips")
        if (which, name) in picks:
            raise ComparisonError(f"{name} of the {which} clips is listed twice")
        picks.append((which, name))
    return picks


def merged_set(job: Job, picks: list[tuple[str, str]]) -> list[dict]:
    """The kept clips, best score first (ties: the new set first, then rank order), each as
    {set, name, moment, detail}."""
    comparison = job.data["comparison"]
    moments = {"new": _read_moments(job.dir / NEW_MOMENTS), "previous": _read_moments(job.dir / "moments.json")}
    details = {"new": {d.get("name"): d for d in (comparison.get("new") or {}).get("details") or []
                       if isinstance(d, dict)},
               "previous": {d.get("name"): d for d in job.data.get("clipDetails") or [] if isinstance(d, dict)}}
    items = []
    for which in SETS:
        for name in sorted((n for s, n in picks if s == which), key=_index):
            index = _index(name)
            detail = dict(details[which].get(name) or {"name": name, "rank": index + 1})
            moment = moments[which][index] if index < len(moments[which]) else None
            if not isinstance(moment, dict):
                moment = {k: detail.get(k) for k in ("rank", "score", "title", "hook", "reason", "start", "end",
                                                       "scores", "source", "gemini", "audience", "peak", "post")
                          if detail.get(k) is not None}
            items.append({"set": which, "name": name, "moment": dict(moment), "detail": detail})

    def score(item):
        value = item["detail"].get("score", item["moment"].get("score"))
        return -float(value) if isinstance(value, (int, float)) else float("inf")

    items.sort(key=score)   # stable: new before previous, then rank order, on equal scores
    for rank, item in enumerate(items, start=1):
        item["moment"]["rank"] = rank
        item["detail"].update(name=f"clip_{rank - 1}.mp4", rank=rank, **{"from": item["set"]})
    return items


def stage_final(job: Job, items: list[dict]) -> pathlib.Path:
    """Copies of the kept clips and posters as clip_0..clip_{n-1} in clips_final/ (copies:
    the sources stay untouched until the swap)."""
    final = job.dir / FINAL_DIR
    shutil.rmtree(final, ignore_errors=True)
    final.mkdir()
    folders = {"new": job.dir / NEW_DIR, "previous": job.dir / "clips"}
    for item in items:
        source = folders[item["set"]] / item["name"]
        target = final / item["detail"]["name"]
        shutil.copyfile(source, target)
        poster = source.with_suffix(".jpg")
        if poster.is_file():
            shutil.copyfile(poster, target.with_suffix(".jpg"))
        else:
            save_clip_poster(target)
    (final / "moments.json").write_text(json.dumps([i["moment"] for i in items], indent=2), encoding="utf-8")
    return final


def apply_comparison(job_dir: pathlib.Path, keep) -> int:
    """Keep the chosen clips of both sets as the job's clips, best first. Anything that
    fails before the swap leaves the live set and the pending comparison as they were."""
    job = Job(job_dir)
    before = job.data.get("status")
    restore = {"status": "done" if before in (None, "queued", "saving") else before,
               "message": job.data.get("message")}
    job.update(pid=os.getpid(), status="saving", error=None, progress=None, message="Keeping the chosen clips")
    final = job.dir / FINAL_DIR
    try:
        picks = validate_selection(job, keep)
        items = merged_set(job, picks)
        stage_final(job, items)
        moments_file = final / "moments.json"
        staged_moments = job.dir / "moments.json.final"
        replace_file(moments_file, staged_moments)      # out of the folder the swap moves file by file
        swap_in_new_clips(job, final)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(final, ignore_errors=True)
        try:
            (job.dir / "moments.json.final").unlink(missing_ok=True)
        except OSError:
            pass
        log(f"Could not keep the chosen clips: {type(exc).__name__}: {exc}")
        job.update(status=restore["status"], progress=None,
                   message="Choose the clips to keep" if job.data.get("comparison") else restore["message"],
                   error=f"Could not keep the chosen clips: {exc}" if isinstance(exc, ComparisonError)
                   else f"Could not keep the chosen clips ({type(exc).__name__}: {exc}); nothing was changed")
        return 1
    try:
        replace_file(job.dir / "moments.json.final", job.dir / "moments.json")
    except OSError as exc:
        log(f"Could not write moments.json: {exc}")
    kept = [i["detail"] for i in items]
    job.update(status="done", clips=[d["name"] for d in kept], clipDetails=kept, comparison=None,
               comparedAt=now_iso(), message=f"Kept {len(kept)} clip{'s' if len(kept) != 1 else ''}",
               error=None, progress=None)
    shutil.rmtree(job.dir / NEW_DIR, ignore_errors=True)
    try:
        (job.dir / NEW_MOMENTS).unlink(missing_ok=True)
    except OSError:
        pass
    log(job.data["message"] + ": " + ", ".join(f"{i['set']} {i['name']}" for i in items))
    return 0


def read_selection(value: str) -> list:
    """--keep-clips: JSON, or @<file> holding it (the web app's form; the file is deleted)."""
    if value.startswith("@"):
        path = pathlib.Path(value[1:])
        text = path.read_text(encoding="utf-8-sig")
        try:
            path.unlink()
        except OSError:
            pass
    else:
        text = value
    return json.loads(text)
