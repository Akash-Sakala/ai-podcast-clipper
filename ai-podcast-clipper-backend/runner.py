"""
Local job runner for AI Podcast Clipper.

Runs on your laptop. The web app starts it for every job, but you can also use
it directly from the terminal:

    python runner.py --video "C:\\path\\to\\podcast.mp4"
    python runner.py --youtube "https://www.youtube.com/watch?v=..."
    python runner.py --job-dir "..\\data\\jobs\\<job id>"     (what the web app calls)
    python runner.py --job-dir "..\\data\\jobs\\<job id>" --rerank   (re-rank & re-render)
    python runner.py --job-dir "..\\data\\jobs\\<job id>" --cancel   (stop a running video, as the app's Cancel)
    python runner.py --job-dir "..\\data\\jobs\\<job id>" --keep-clips @choice.json
                                                               (after a re-rank: the clips to keep)
    python runner.py --thumbnails                              (covers and clip posters for older videos)
    python runner.py --prune-videos [--dry-run]                (delete kept videos: job folders and the re-rank store)
    python runner.py --backfill-replay [--force]               (Most replayed graph + SponsorBlock for older videos)
    python runner.py --modal-bill                              (this month's Modal bill -> data/stats, stats page)
    python runner.py --check-rates                             (Modal's live prices as the cost estimate reads them)

Steps: (YouTube) read the Most replayed graph and SponsorBlock segments on this laptop
-> the audio into the Modal jobs volume (YouTube: the audio stream alone; an MP4: copied
out of it) -> Modal transcribes and ranks it while this laptop downloads the YouTube video
-> the laptop cuts each chosen clip's footage out of the video and uploads only that ->
Modal renders the clips -> download them into <job dir>/clips -> update <job dir>/job.json
-> delete the video, the audio and the footage everywhere (see phases.py; the whole video is
uploaded as before when the audio or the footage can't be had on their own).

YouTube videos download on this laptop into a temp folder outside OneDrive
(<temp>/ai-podcast-clipper/<job id>) and are deleted when the run succeeds. A failed download
keeps its partial file there, and a failed run its finished download, so Try again resumes
or reuses it; folders left behind for over two days are cleared at the next run. An
uploaded MP4 is read from the job folder. Once the clips are saved, input.mp4 is deleted
from the job folder and the volume: only job.json, the transcript, the moments, the clips
and the thumbnail stay. Re-rank downloads a YouTube video again; an upload is added again.
Transcription is never repeated: phase 1 keeps transcript.json in the job folder at once,
so Try again after a later failure only ranks and renders.

In the cloud, a GPU container transcribes the audio with Parakeet TDT 0.6B v2
(English), Gemini ranks the best moments, and every clip renders in its own GPU
container at the same time. The live progress (stage, share of the clips done)
and the estimated cost are mirrored into job.json for the web app.

Requires `modal deploy main.py` to have been run once.
"""

import argparse
import json
import os
import pathlib
import shutil
import signal
import sys

import settings  # loads ai-podcast-clipper-backend/.env
from billing import live_rates, refresh_modal_bill
from cancel import cancel_job
from clipsets import apply_comparison, read_selection
from jobstore import DEFAULT_DATA_DIR, Job, log
from maintenance import backfill_replay, backfill_thumbnails, prune_videos
from media import file_fingerprint
from pipeline import run_job, run_rerank


def stop_on_signals() -> list[str]:
    """Turn a stop request (SIGTERM; SIGBREAK, Ctrl+Break on Windows; SIGHUP) into SystemExit,
    so the run cancels its Modal job and cleans the volume on the way out. A process killed
    outright (Task Manager, TerminateProcess) can't do that: the next run's sweep does."""
    installed = []

    def stop(signum, frame):
        raise SystemExit(128 + int(signum))

    for name in ("SIGTERM", "SIGBREAK", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, stop)
            installed.append(name)
        except (ValueError, OSError):    # not the main thread, or not allowed here
            pass
    return installed


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Podcast Clipper local runner")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--job-dir", help="Existing job folder created by the web app")
    source.add_argument("--video", help="Path to a local .mp4 file")
    source.add_argument("--youtube", help="YouTube video URL")
    source.add_argument("--thumbnails", action="store_true",
                        help="Save missing video covers and clip posters in --data-dir, then exit")
    source.add_argument("--prune-videos", action="store_true",
                        help="Delete the input.mp4 of finished jobs in --data-dir (kept by older versions) "
                             "and every video kept for re-ranks (VIDEO_CACHE_DIR), then exit")
    source.add_argument("--modal-bill", action="store_true",
                        help="Save this month's Modal bill (modal billing summary) for the stats page, then exit")
    source.add_argument("--check-rates", action="store_true",
                        help="Print Modal's live prices as the cost estimate reads them, then exit")
    source.add_argument("--backfill-replay", action="store_true",
                        help="Read the Most replayed graph and SponsorBlock segments for older YouTube videos "
                             "in --data-dir, report how their clips line up with it, then exit")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", str(DEFAULT_DATA_DIR)),
                        help="Where job folders are stored (default: %%LOCALAPPDATA%%\\ai-podcast-clipper\\data, "
                             "or ../data while it holds the jobs of an older version)")
    parser.add_argument("--max-clips", type=int, default=None,
                        help="Clips per video (default: the app's Settings, else 5)")
    parser.add_argument("--keep-remote", action="store_true",
                        help="Keep the job files in the Modal volume after downloading "
                             "(a later run removes them once they are a day old)")
    parser.add_argument("--keep-video", action="store_true",
                        help="Keep input.mp4 in the job folder after the run, instead of the kept videos "
                             "(e.g. for benchmark_gpu.py)")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --prune-videos: only list what would be deleted")
    parser.add_argument("--force", action="store_true",
                        help="With --backfill-replay: also redo videos that already have it")
    parser.add_argument("--cancel", action="store_true",
                        help="With --job-dir: stop the video's runner, cancel its Modal calls and clean up")
    parser.add_argument("--rerank", action="store_true",
                        help="With --job-dir: re-rank & re-render a finished job from its saved transcript")
    parser.add_argument("--keep-clips", metavar="JSON",
                        help='With --job-dir, after a re-rank: the clips to keep, as JSON '
                             '[{"set": "new"|"previous", "clip": "clip_0.mp4"}, ...] or @<file> holding it '
                             "(the file is deleted after reading)")
    args = parser.parse_args()
    if args.cancel:
        if not args.job_dir:
            parser.error("--cancel needs --job-dir")
        return cancel_job(pathlib.Path(args.job_dir).resolve())
    stop_on_signals()
    data_dir = (pathlib.Path(args.job_dir).resolve().parent.parent if args.job_dir
                else pathlib.Path(args.data_dir).resolve())
    settings.apply_user_settings(data_dir)
    if args.max_clips is None:
        args.max_clips = settings.MAX_CLIPS
    if args.modal_bill:
        bill = refresh_modal_bill(data_dir)
        print(json.dumps(bill, indent=2) if bill else "Could not read the Modal bill (see the message above)")
        return 0 if bill else 1
    if args.check_rates:
        rates = live_rates(data_dir, max_age_hours=0)
        print(json.dumps({"source": rates.get("source"), "A10 per hour": round(rates["gpu"]["A10"] * 3600, 4),
                          "CPU core per hour": round(rates["cpu"] * 3600, 4),
                          "memory GiB per hour": round(rates["memory"] * 3600, 4)}, indent=2))
        return 0
    if args.thumbnails:
        return backfill_thumbnails(pathlib.Path(args.data_dir).resolve())
    if args.prune_videos:
        return prune_videos(pathlib.Path(args.data_dir).resolve(), args.dry_run)
    if args.backfill_replay:
        return backfill_replay(pathlib.Path(args.data_dir).resolve(), args.force)
    if args.keep_clips is not None:
        if not args.job_dir:
            parser.error("--keep-clips needs --job-dir")
        try:
            keep = read_selection(args.keep_clips)
        except (OSError, ValueError) as exc:
            log(f"Could not read the clips to keep: {exc}")
            job = Job(pathlib.Path(args.job_dir).resolve())
            job.update(status="done" if job.data.get("clips") else job.data.get("status"), progress=None,
                       error=f"Could not read the clips to keep: {exc}")
            return 2
        return apply_comparison(pathlib.Path(args.job_dir).resolve(), keep)
    if args.rerank:
        if not args.job_dir:
            parser.error("--rerank needs --job-dir")
        return run_rerank(pathlib.Path(args.job_dir).resolve(), args.max_clips, args.keep_remote, args.keep_video)

    if args.job_dir:
        job_dir = pathlib.Path(args.job_dir).resolve()
    else:
        data_dir = pathlib.Path(args.data_dir).resolve()
        if args.video:
            video = pathlib.Path(args.video).resolve()
            if not video.exists():
                parser.error(f"{video} does not exist")
            job = Job.create(data_dir, "upload", video.name)
            log(f"Copying {video} into {job.dir}")
            shutil.copy(video, job.dir / "input.mp4")
            job.update(fingerprint=file_fingerprint(job.dir / "input.mp4"))
        else:
            job = Job.create(data_dir, "youtube", args.youtube, youtube_url=args.youtube)
        job_dir = job.dir
        log(f"Created job {job.id} in {job_dir}")

    return run_job(job_dir, args.max_clips, args.keep_remote, args.keep_video)


if __name__ == "__main__":
    sys.exit(main())
