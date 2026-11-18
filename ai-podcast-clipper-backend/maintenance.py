"""One-off commands: covers and posters for older videos, the Most replayed backfill, pruning
the videos older versions kept."""

import pathlib
import time

import replay
import settings
import videostore
from jobstore import Job, log
from media import discard_local_video, file_fingerprint, save_clip_poster, save_thumbnail
from youtube import fetch_youtube_info, youtube_cookies


def backfill_thumbnails(data_dir: pathlib.Path) -> int:
    """Save missing covers for uploaded videos and missing posters for all clips."""
    covers = posters = 0
    for job_dir in sorted((data_dir / "jobs").glob("*")):
        if not (job_dir / "job.json").exists():
            continue
        job = Job(job_dir)
        if (job.data.get("source") == "upload" and not (job_dir / "thumbnail.jpg").exists()
                and save_thumbnail(job, video=(job_dir / "input.mp4") if (job_dir / "input.mp4").exists()
                                   else videostore.find(job))):
            covers += 1
        for clip in sorted((job_dir / "clips").glob("clip_*.mp4")):
            if not clip.with_suffix(".jpg").exists() and save_clip_poster(clip):
                posters += 1
    log(f"Saved {covers} video cover(s) and {posters} clip poster(s)")
    return 0


def backfill_replay(data_dir: pathlib.Path, force: bool = False, pause_s: float = 3.0,
                    fetch=replay.fetch_signals, sleep=time.sleep) -> int:
    """Read the Most replayed graph and SponsorBlock segments for older YouTube videos,
    so their next re-rank uses them, and report how the current clips line up with the
    replay hotspots (clips chosen by Gemini alone, before this feature)."""
    totals = {"videos": 0, "withGraph": 0, "clips": 0, "clipsOnPeak": 0, "topPeakCovered": 0}
    for job_dir in sorted((data_dir / "jobs").glob("*")):
        if not (job_dir / "job.json").exists():
            continue
        job = Job(job_dir)
        if job.data.get("source") != "youtube" or not job.data.get("youtubeUrl"):
            continue
        if job.data.get("replay") and not force:
            record = job.data["replay"]
        else:
            if totals["videos"]:
                sleep(pause_s)                          # be gentle with YouTube
            record, _ = fetch(job.data["youtubeUrl"], cookies_browser=youtube_cookies()["browser"],
                              sponsorblock=settings.SPONSORBLOCK, fetch_info=fetch_youtube_info, log=log)
            job.data["replay"] = record
            job.save()  # keeps updatedAt: the video itself did not change
        totals["videos"] += 1
        sponsor = record.get("sponsor") or {}
        skips = len(sponsor.get("segments") or [])
        if not record.get("available"):
            reason = "; ".join(record.get("errors") or []) or "YouTube shows no graph for it"
            log(f"{job.id}: no Most replayed graph ({reason[:120]}); SponsorBlock {skips} segment(s)")
            continue
        hits = replay.hit_report(record.get("peaks") or [], job.data.get("clipDetails") or [])
        totals["withGraph"] += 1
        totals["clips"] += hits["clips"]
        totals["clipsOnPeak"] += hits["clipsOnPeak"]
        totals["topPeakCovered"] += int(hits["topPeakCovered"])
        log(f"{job.id}: {len(record.get('peaks') or [])} hotspot(s); {hits['clipsOnPeak']} of {hits['clips']} "
            f"current clip(s) on a hotspot; top hotspot {'covered' if hits['topPeakCovered'] else 'missed'}; "
            f"SponsorBlock {skips} segment(s){', a highlight' if sponsor.get('highlight') is not None else ''}")
    log(f"{totals['withGraph']} of {totals['videos']} YouTube video(s) have a Most replayed graph. "
        f"Before this feature, {totals['clipsOnPeak']} of {totals['clips']} of their clips sat on a hotspot "
        f"and {totals['topPeakCovered']} of {totals['withGraph']} covered the most replayed one. "
        "Re-rank a video to use the graph.")
    return 0


def prune_videos(data_dir: pathlib.Path, dry_run: bool = False) -> int:
    """Delete the videos older versions kept in finished job folders, and every video kept for
    re-ranks (videostore.py; running jobs' excepted). Failed and running jobs keep the video in
    their folder (Try again reuses it). Uploads keep a fingerprint and a cover first."""
    freed, count = 0.0, 0
    for job_dir in sorted((data_dir / "jobs").glob("*")):
        videos = [p for p in job_dir.glob("input.*") if p.is_file()]
        if not videos or not (job_dir / "job.json").exists():
            continue
        job = Job(job_dir)
        size = sum(p.stat().st_size for p in videos) / 1024 / 1024
        if job.data.get("status") != "done":
            log(f"{job.id}: kept ({job.data.get('status')}, {size:.0f} MB)")
            continue
        if dry_run:
            log(f"{job.id}: would delete {size:.0f} MB")
            freed, count = freed + size, count + 1
            continue
        if job.data.get("source") == "upload":
            if not job.data.get("fingerprint"):
                fingerprint = file_fingerprint(job_dir / "input.mp4")
                if fingerprint:
                    job.data["fingerprint"] = fingerprint
                    job.save()  # keeps updatedAt: the video itself did not change
            if not (job_dir / "thumbnail.jpg").exists():
                save_thumbnail(job)
        freed_here = discard_local_video(job)
        log(f"{job.id}: deleted {freed_here:.0f} MB")
        freed, count = freed + freed_here, count + 1
    verb = "Would free" if dry_run else "Freed"
    log(f"{verb} {freed / 1024:.1f} GB from {count} video(s) in job folders")
    store = videostore.summary()
    if dry_run:
        log(f"Kept videos ({store['dir']}): would remove {store['count']} video(s), "
            f"{store['bytes'] / 1024 ** 3:.1f} GB")
    else:
        removed, freed_bytes = videostore.clear(data_dir)
        log(f"Kept videos ({store['dir']}): removed {removed} video(s), {freed_bytes / 1024 ** 3:.1f} GB")
    return 0
