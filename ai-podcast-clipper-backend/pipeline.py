"""Run and re-rank orchestration: get the job through Modal (phases.py), save the outputs,
record the run, clean up."""

import os
import pathlib
import re
import shutil
import time
import traceback

import replay
import settings
from billing import job_cost, refresh_modal_bill
from clipper_names import JOBS_VOLUME
from cloud import cancel_remote_call, cancel_stale_call
from jobstore import Job, log, now_iso, replace_file
from media import discard_local_video, file_fingerprint, save_thumbnail
from phases import forget_download, process_on_modal
from run_history import record_run
from transfer import (
    ClipSaver,
    cleanup_remote,
    commit_extras,
    discard_extras,
    download_outputs,
    pending_record,
    sweep_remote,
)
from youtube import clear_old_downloads, fetch_replay


def gemini_context(job: Job) -> dict:
    """What transcribe_and_rank passes to Gemini besides the transcript. The Most replayed and
    SponsorBlock parts follow the current settings, whatever an earlier run saved in
    job.json "replay": a switched-off source is left out, and with both off the video
    ranks exactly like one without a graph."""
    meta = job.data.get("meta") or {}
    context = {
        "title": meta.get("title") or job.data.get("title"),
        "channel": meta.get("channel"),
        "description": meta.get("description"),
    }
    signals = replay.ranking_context(job.data.get("replay"), settings.REPLAY_WEIGHT,
                                     most_replayed=settings.MOST_REPLAYED, sponsorblock=settings.SPONSORBLOCK)
    if signals:
        context["replay"] = signals
    return context


def run_on_modal(job: Job, volume, max_clips: int, mode: str, timings: dict, saver: ClipSaver,
                 keep_video: bool) -> dict:
    """process_on_modal with this job's Gemini context; closes the clip saver on failure."""
    try:
        result = process_on_modal(job, max_clips, mode, timings, saver, volume=volume, context=gemini_context(job),
                                  keep_video=keep_video)
    except BaseException:
        saver.close()
        raise
    cost = result.get("cost") or {}
    log(f"Modal finished in {float(timings.get('gpu_total') or 0):.0f}s on {result.get('gpu') or '?'}"
        + (f", about ${cost['usd']:.3f}" if cost.get("usd") is not None else ""))
    return result


def swap_in_new_clips(job: Job, new_dir: pathlib.Path) -> None:
    """Put the re-rendered clips (all downloaded into new_dir) in place of the old ones.

    File by file, never by renaming the clips folder: on Windows a folder can't be renamed
    while any file in it is open (the web app streaming a clip, a player, OneDrive). Each
    old file first moves to clips_previous/, then the new one takes its name (each step
    retried while the file is locked). If a step still fails, the files already swapped
    are put back and the error is raised, so the old clips stay complete. Old clips with
    no new counterpart (fewer clips this time) are removed afterwards (best effort)."""
    clips_dir = job.dir / "clips"
    backup = job.dir / "clips_previous"
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


def resume_pending(job: Job, mode: str, timings: dict) -> dict | None:
    """The result of an earlier run of this mode whose clips rendered but were not saved
    (job.json "pendingSave"; its clips are still on the volume), with its timings copied
    into `timings`. None when there is none."""
    pending = job.data.get("pendingSave") or {}
    if pending.get("mode") != mode or not isinstance(pending.get("result"), dict):
        return None
    log(f"Clips of the previous attempt ({pending.get('savedAt')}) are still on Modal: downloading "
        "them instead of rendering again")
    timings.update({k: v for k, v in (pending.get("timings") or {}).items() if k not in ("save", "total")})
    return {**pending["result"], "resumedFrom": pending.get("savedAt")}


def save_clips(job: Job, volume, result: dict, saver: ClipSaver, mode: str, timings: dict,
               started: float, swap_into: pathlib.Path | None = None) -> list[str]:
    """Download the run's clips (and swap them in for a re-rank). If that fails after the
    GPU work succeeded, the clips stay on the volume and job.json "pendingSave" lets the
    next run of this mode download them instead of running the GPU pipeline again."""
    saving = time.time()
    try:
        clips = download_outputs(job, volume, result, saver=saver)
        if swap_into is not None and clips:
            swap_in_new_clips(job, swap_into)
    except FileNotFoundError:
        raise
    except Exception:
        if result.get("clips"):
            job.update(pendingSave=pending_record(mode, result, timings, time.time() - started))
            log("The clips are kept on Modal: Try again (or Re-rank) downloads them without rendering again")
        raise
    timings["save"] = round(time.time() - saving, 1)
    return clips


def ranking_message(clips: list[str], errors: list[str], ranking: dict) -> str:
    """Job summary that says why clips are missing or came from the fallback."""
    if clips:
        message = f"{len(clips)} clip(s) ready, best first"
    else:
        message = "No clips were produced"
    notes = []
    if ranking.get("fallback"):
        notes.append(f"{ranking['fallback']} picked from the transcript because Gemini returned "
                     f"{ranking.get('valid', 0)} usable moment(s)")
    if ranking.get("errors"):
        notes.append(f"Gemini error: {ranking['errors'][-1][:160]}")
    if errors:
        notes.append(f"{len(errors)} clip(s) failed to render - see the log")
    if not clips and not notes:
        notes.append("the transcript was empty" if ranking.get("reason") == "empty transcript"
                     else "Gemini found no usable moments")
    return message + (" (" + "; ".join(notes) + ")" if notes else "")


def clip_details(clips: list[str], clip_moments: list[dict]) -> list[dict]:
    """clip_N.mp4 is rendered from clip_moments[N] (sorted best first)."""
    details = []
    for name in clips:
        index = int(re.search(r"\d+", name).group(0))
        moment = clip_moments[index] if index < len(clip_moments) else {}
        details.append({
            "name": name,
            "rank": moment.get("rank", index + 1),
            "score": moment.get("score"),
            "title": moment.get("title"),
            "hook": moment.get("hook"),
            "reason": moment.get("reason"),
            "start": moment.get("start"),
            "end": moment.get("end"),
            "scores": moment.get("scores"),
            "source": moment.get("source", "gemini"),
            "gemini": moment.get("gemini"),
            "audience": moment.get("audience"),
            "peak": moment.get("peak"),
        })
    return details


def forget_video(job: Job) -> None:
    """After a successful run: an upload keeps its fingerprint (to be recognised when it is
    added again), then input.mp4 leaves the job folder."""
    if job.data.get("source") == "upload" and not job.data.get("fingerprint"):
        fingerprint = file_fingerprint(job.dir / "input.mp4")
        if fingerprint:
            job.update(fingerprint=fingerprint)
    freed = discard_local_video(job)
    if freed:
        log(f"Deleted the video from the job folder ({freed:.0f} MB)")


def run_diagnostics(result: dict) -> dict:
    """Optional job.json / ledger fields for measuring render speed and cold starts:
    clipTimings  per clip: {clip, seconds, enterSeconds, steps: {cut, detect, score, render}, encoder}
    coldStarts   per container that started for this run: {stage, enterSeconds, gpu}"""
    out = {}
    timings = [t for t in result.get("clipTimings") or [] if isinstance(t, dict)]
    if timings:
        out["clipTimings"] = timings
    starts = [{"stage": u.get("stage"), "enterSeconds": u.get("enterSeconds"), "gpu": u.get("gpu")}
              for u in result.get("usages") or [] if isinstance(u, dict) and (u.get("enterSeconds") or 0) > 0]
    if starts:
        out["coldStarts"] = starts
    if result.get("resumedFrom"):
        out["resumedFrom"] = result["resumedFrom"]   # clips of an earlier attempt, downloaded now
    return out


def rerank_job(job: Job, volume, max_clips: int, timings: dict, keep_video: bool = False,
               started: float | None = None) -> tuple[dict, list[str]]:
    """Re-rank & re-render: reuse the saved transcript, skip transcription. The video is
    downloaded again (YouTube) or taken from the job folder (an upload added again).
    The old clips and moments.json stay until every new clip is downloaded."""
    started = time.time() if started is None else started
    if not (job.dir / "transcript.json").exists():
        raise FileNotFoundError("transcript.json is missing from the job folder, so this job cannot be re-ranked")
    new_dir = job.dir / "clips_new"
    result = resume_pending(job, "rerank", timings)
    saver = ClipSaver(job, volume, new_dir)
    if result is not None:
        try:
            clips = save_clips(job, volume, result, saver, "rerank", timings, started, swap_into=new_dir)
        except FileNotFoundError:
            log("The kept clips are no longer on Modal: re-ranking again")
            job.update(pendingSave=None)
            discard_extras(job)
            result = None
    if result is None:
        shutil.rmtree(new_dir, ignore_errors=True)
        # Jobs from the removed Whistle engine get their subtitles re-transcribed with Parakeet.
        transcriber = (job.data.get("engine") or {}).get("transcriber") or "parakeet"
        mode = "uploaded" if transcriber == "whistle" else "reuse"

        fetch_replay(job, timings)
        previous = job.dir / "moments.json"
        if previous.exists():
            shutil.copy(previous, job.dir / "moments.previous.json")

        saver = ClipSaver(job, volume, new_dir)
        result = run_on_modal(job, volume, max_clips, mode, timings, saver, keep_video)
        clips = save_clips(job, volume, result, saver, "rerank", timings, started, swap_into=new_dir)
    if clips:
        commit_extras(job)
    else:  # keep the old clips (and their moments.json) if the re-run produced none
        shutil.rmtree(new_dir, ignore_errors=True)
        discard_extras(job)
        clips = job.data.get("clips") or []
        result = {**result, "moments": None}
    return result, clips


def finish_cleanup(job: Job, volume, keep_remote: bool) -> None:
    """End of every run: stop the Modal call if it is still running, then remove the job's
    volume folder (only the uploaded video, audio and clip footage when its clips are kept
    for the next try)."""
    cancel_remote_call(job)
    if not keep_remote:
        cleanup_remote(job, volume, keep_clips=bool(job.data.get("pendingSave")))


def start_run(job: Job, volume, keep_remote: bool) -> None:
    """Start of every run: note --keep-remote (the sweep then waits a day before removing the
    folder), stop a previous attempt still on Modal, sweep the volume, clear old downloads."""
    if keep_remote or "keepRemote" in job.data:
        job.update(keepRemote=True if keep_remote else None)
    cancel_stale_call(job)
    sweep_remote(volume, job.dir.parent.parent, current_job=job.id)
    clear_old_downloads()


def stopped(job: Job, failed_stage: str, keep_clips: bool) -> None:
    """The runner got a stop signal (or Ctrl+C): say so in job.json before cleaning up."""
    job.update(status="done" if keep_clips else "failed",
               message="Stopped - previous clips kept" if keep_clips else "Stopped",
               error="The runner was stopped before the job finished", progress=None,
               failedStage=(job.data.get("progress") or {}).get("stage") or failed_stage)


def run_rerank(job_dir: pathlib.Path, max_clips: int, keep_remote: bool, keep_video: bool = False) -> int:
    import modal

    job = Job(job_dir)
    job.update(pid=os.getpid(), error=None, status="queued", progress=None, videoTransfer=None,
               message="Re-rank & re-render: getting the video ready")
    volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)
    timings: dict = {"rerank": True}
    job_start = time.time()
    try:
        start_run(job, volume, keep_remote)
        result, clips = rerank_job(job, volume, max_clips, timings, keep_video, started=job_start)
        timings["total"] = round(time.time() - job_start + float((job.data.get("pendingSave") or {})
                                                                    .get("elapsed") or 0), 1)
        errors = result.get("errors") or []
        ranking = result.get("ranking") or {}
        message = "Re-ranked: " + ranking_message(clips, errors, ranking)
        if result.get("moments") is None:
            message += " - kept the previous clips"
            details = job.data.get("clipDetails") or []
        else:
            details = clip_details(clips, result.get("moments") or [])
        cost = job_cost(result, job.dir.parent.parent)
        extra = run_diagnostics(result)
        runs = record_run(job, mode="rerank", status="done" if result.get("clips") else "no-clips",
                          started=job_start, timings=timings, cost=cost, clips=result.get("clips") or [],
                          details=details if result.get("moments") is not None else [], ranking=ranking,
                          extra=extra)
        job.update(status="done", message=message, clips=clips, clipErrors=errors, timings=timings,
                   ranking=ranking, clipDetails=details, reranks=int(job.data.get("reranks") or 0) + 1,
                   rerankedAt=now_iso(), progress=None, cost=cost, runs=runs, pendingSave=None, **extra)
        log(f"{message}. Timings: {timings}")
        if not keep_video:
            forget_video(job)
        forget_download(job, keep_video)
        refresh_modal_bill(job.dir.parent.parent)   # for the stats page; best effort
        return 0
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        cancel_remote_call(job)
        timings["total"] = round(time.time() - job_start, 1)
        runs = record_run(job, mode="rerank", status="failed", started=job_start, timings=timings,
                          failed_stage=(job.data.get("progress") or {}).get("stage") or "processing")
        job.update(status="done" if job.data.get("clips") else "failed",
                   message="Re-rank failed - previous clips kept" if job.data.get("clips") else "Failed",
                   error=f"{type(exc).__name__}: {exc}", progress=None, runs=runs,
                   failedStage=(job.data.get("progress") or {}).get("stage") or "processing")
        discard_extras(job)
        return 1
    except BaseException:   # a stop signal or Ctrl+C
        stopped(job, "processing", keep_clips=bool(job.data.get("clips")))
        raise
    finally:
        finish_cleanup(job, volume, keep_remote)


def finish_full_run(job: Job, result: dict, clips: list[str], timings: dict, job_start: float,
                    keep_video: bool) -> int:
    commit_extras(job)
    timings["total"] = round(time.time() - job_start + float((job.data.get("pendingSave") or {})
                                                             .get("elapsed") or 0), 1)
    errors = result.get("errors") or []
    ranking = result.get("ranking") or {}
    message = ranking_message(clips, errors, ranking)
    cost = job_cost(result, job.dir.parent.parent)
    details = clip_details(clips, result.get("moments") or [])
    extra = run_diagnostics(result)
    runs = record_run(job, mode="full", status="done" if clips else "no-clips", started=job_start,
                      timings=timings, cost=cost, clips=clips, details=details, ranking=ranking, extra=extra)
    job.update(status="done", message=message, clips=clips, clipErrors=errors, timings=timings,
               ranking=ranking, progress=None, clipDetails=details, cost=cost, runs=runs, pendingSave=None,
               **extra)
    log(f"{message}. Timings: {timings}")
    if not keep_video:
        forget_video(job)
    forget_download(job, keep_video)
    refresh_modal_bill(job.dir.parent.parent)   # for the stats page; best effort
    return 0


def transcript_mode(job: Job) -> str:
    """"reuse" when an earlier attempt of this job already transcribed it (transcript.json in
    the job folder, kept by phase 1): Try again then only ranks and renders. Else "parakeet"."""
    return "reuse" if (job.dir / "transcript.json").exists() else "parakeet"


def run_job(job_dir: pathlib.Path, max_clips: int, keep_remote: bool, keep_video: bool = False) -> int:
    import modal

    job = Job(job_dir)
    job.update(pid=os.getpid(), error=None, progress=None, videoTransfer=None)
    volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)
    timings: dict = {}
    job_start = time.time()
    failed_stage = "starting"

    try:
        start_run(job, volume, keep_remote)
        result = resume_pending(job, "full", timings)
        if result is not None:
            failed_stage = "saving"
            saver = ClipSaver(job, volume, job.dir / "clips")
            try:
                clips = save_clips(job, volume, result, saver, "full", timings, job_start)
            except FileNotFoundError:
                log("The kept clips are no longer on Modal: running the job again")
                job.update(pendingSave=None)
                discard_extras(job)
                timings.clear()
                result = None
            if result is not None:
                return finish_full_run(job, result, clips, timings, job_start, keep_video)

        if job.data.get("source") == "upload" and not (job.dir / "thumbnail.jpg").exists():
            save_thumbnail(job)

        mode = transcript_mode(job)
        earlier = (job.data.get("engine") or {}).get("parakeet") if mode == "reuse" else None
        job.update(engine={"transcriber": "parakeet", "language": "en", "subtitles": "parakeet",
                           "parakeet": earlier})
        if mode == "reuse":
            log("Reusing the transcript of the previous attempt")
        failed_stage = "uploading" if (job.dir / "input.mp4").exists() else "downloading"
        fetch_replay(job, timings)

        failed_stage = "processing"
        saver = ClipSaver(job, volume, job.dir / "clips")
        result = run_on_modal(job, volume, max_clips, mode, timings, saver, keep_video)
        job.update(engine={**job.data["engine"], "subtitles": result.get("subtitles", "parakeet"),
                           "gpu": result.get("gpu"),
                           "parakeet": result.get("transcription") or job.data["engine"].get("parakeet")})
        failed_stage = "saving"
        clips = save_clips(job, volume, result, saver, "full", timings, job_start)
        return finish_full_run(job, result, clips, timings, job_start, keep_video)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        cancel_remote_call(job)
        timings["total"] = round(time.time() - job_start, 1)
        runs = record_run(job, mode="full", status="failed", started=job_start, timings=timings,
                          failed_stage=(job.data.get("progress") or {}).get("stage") or failed_stage)
        job.update(status="failed", message="Failed", error=f"{type(exc).__name__}: {exc}", timings=timings,
                   failedStage=(job.data.get("progress") or {}).get("stage") or failed_stage, progress=None,
                   runs=runs)
        discard_extras(job)
        return 1
    except BaseException:   # a stop signal or Ctrl+C
        stopped(job, failed_stage, keep_clips=False)
        raise
    finally:
        finish_cleanup(job, volume, keep_remote)
