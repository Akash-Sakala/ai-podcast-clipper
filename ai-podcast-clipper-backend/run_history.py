"""
Run history for the stats page: every run of a job (the first run, each re-rank,
failed runs) as one entry in job.json "runs".

runner.py appends an entry when a run ends. Jobs from before the history existed
get theirs rebuilt from runner.log, which keeps every run's lines:

    python runner.py --backfill-stats

An entry:
    {"mode": "full" | "rerank", "status": "done" | "no-clips" | "failed",
     "startedAt": ISO UTC, "finishedAt": ISO UTC, "clips": 5,
     "pipeline": "parallel" | "single-gpu", "videoSeconds": 2243.0,
     "timings": {"download", "upload", "gpu_transcribe", "gpu_select", "gpu_render",
                 "gpu_total", "total"}  (seconds; a step that did not run is missing),
     "cost": {"usd", "gpuSeconds", "gpu", "containers", "estimated"} | null,
     "recordedBy": "runner" | "log"}

Costs: runs since the parallel renderer report Modal's per-container usage (costs.py).
Older runs ran everything in one GPU container, so their cost is estimated from the
logged GPU time and marked "estimated". Standard library only.
"""

from __future__ import annotations

import ast
import datetime as dt
import re

import costs

OLD_SCALEDOWN_S = 20          # scaledown_window of the single GPU class before the parallel renderer
PRE_PARAKEET_GPU = "L40S"     # GPU of the first (WhisperX) version, whose log does not name it

_LINE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\] (.*)$")
_GPU_DONE = re.compile(r"GPU processing finished in (\d+(?:\.\d+)?)s(?: on ([\w-]+))?(?:, about \$(\d+(?:\.\d+)?))?")
_CALLING = re.compile(r"Calling .*?(?:\(transcript=(\w+)\))?$")
_CLIPS = re.compile(r"(\d+) clip\(s\) ready")
_TIMINGS = re.compile(r"Timings: (\{.*\})\s*$")


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def estimate_cost(gpu_seconds: float, gpu: str | None) -> dict:
    """One GPU container ran the whole job: the cost of the old single-GPU pipeline."""
    label = gpu or "A10"
    estimate = costs.estimate([{"container": "job", "gpu": label, "seconds": gpu_seconds,
                                "scaledown": OLD_SCALEDOWN_S}])
    return {"usd": estimate["usd"], "gpuSeconds": estimate["gpuSeconds"], "gpu": label,
            "containers": 1, "estimated": True}


def _stamped_lines(text: str, first_day: dt.datetime) -> list[tuple[dt.datetime, str]]:
    """Log lines with a full local timestamp. Lines carry only a time of day, so the
    date starts at the job's creation day and moves on when the clock goes backwards."""
    lines, day, previous = [], first_day.date(), None
    tz = first_day.tzinfo
    for raw in text.splitlines():
        match = _LINE.match(raw.strip())
        if not match:
            continue
        clock = dt.time(int(match[1]), int(match[2]), int(match[3]))
        if previous is not None and clock < previous:
            day += dt.timedelta(days=1)
        previous = clock
        lines.append((dt.datetime.combine(day, clock, tzinfo=tz), match[4]))
    return lines


def _starts_run(message: str, current: dict | None) -> bool:
    if message.startswith("Downloading http"):
        return True
    if message.startswith("Copying ") or message.startswith("Uploading input.mp4"):
        # A full run uploads after downloading; a re-rank starts with the upload.
        return current is None or current["called"]
    return False


def parse_log(text: str, job: dict, tz: dt.tzinfo | None = None) -> list[dict]:
    """Rebuild the run history of a job from its runner.log."""
    created = parse_iso(job.get("createdAt")) or dt.datetime.now(dt.timezone.utc)
    created = created.astimezone(tz) if tz else created.astimezone()
    runs, current = [], None
    for moment, message in _stamped_lines(text, created):
        if _starts_run(message, current):
            current = {"start": moment, "end": moment, "called": False, "lines": []}
            runs.append(current)
        if current is None:
            continue
        current["end"] = moment
        current["lines"].append((moment, message))
        if message.startswith("Calling "):
            current["called"] = True
    _anchor_last_run(runs, job)
    video_seconds = _video_seconds(job)
    entries = [entry for entry in (_run_entry(run, video_seconds) for run in runs) if entry]
    # The log rounds the cost; job.json keeps the latest run's full estimate.
    latest = job.get("cost")
    if entries and latest and entries[-1]["cost"] and not entries[-1]["cost"]["estimated"]:
        entries[-1]["cost"] = {**latest, "estimated": False}
    return entries


def _anchor_last_run(runs: list[dict], job: dict) -> None:
    """A re-rank days after the run before it has no midnight in between to count, so
    the last run takes its date from the job's last update instead."""
    updated = parse_iso(job.get("rerankedAt") or job.get("updatedAt"))
    if not runs or updated is None:
        return
    last = runs[-1]
    shift = (updated.astimezone(last["end"].tzinfo).date() - last["end"].date()).days
    if shift <= 0 or (len(runs) > 1 and last["start"] + dt.timedelta(days=shift) <= runs[-2]["end"]):
        return
    delta = dt.timedelta(days=shift)
    last["start"] += delta
    last["end"] += delta
    last["lines"] = [(moment + delta, message) for moment, message in last["lines"]]


def _video_seconds(job: dict) -> float | None:
    parakeet = (job.get("engine") or {}).get("parakeet") or {}
    seconds = parakeet.get("audioSeconds") or (job.get("meta") or {}).get("duration")
    return float(seconds) if seconds else None


def _first(lines, prefix):
    return next((moment for moment, message in lines if message.startswith(prefix)), None)


def _run_entry(run: dict, video_seconds: float | None) -> dict | None:
    lines = run["lines"]
    if not run["called"]:
        return None  # stopped before reaching the GPU: nothing ran in the cloud
    transcript, gpu_line, timings, clips, status = None, None, None, None, "failed"
    for _, message in lines:
        if message.startswith("Calling "):
            calling = _CALLING.search(message)
            transcript = calling[1] if calling else None
        if gpu_line is None and (found := _GPU_DONE.search(message)):
            gpu_line = found
        if (found := _TIMINGS.search(message)) and timings is None:
            try:
                timings = ast.literal_eval(found[1])
            except (ValueError, SyntaxError):
                timings = None
        if "No clips were produced" in message:
            status, clips = "no-clips", 0
        elif (found := _CLIPS.search(message)) and status == "failed":
            status, clips = "done", int(found[1])

    mode = "rerank" if transcript in ("reuse", "uploaded") or (timings or {}).get("rerank") else "full"
    parallel = bool(gpu_line and gpu_line[3])
    gpu = (gpu_line[2] if gpu_line else None) or (None if transcript else PRE_PARAKEET_GPU)

    steps = {k: float(v) for k, v in (timings or {}).items() if isinstance(v, (int, float)) and k != "rerank"}
    if not timings:  # older log format: derive the steps from the line times
        download, uploading, calling = _first(lines, "Downloading http"), _first(lines, "Uploading"), _first(lines, "Calling")
        if download and uploading:
            steps["download"] = (uploading - download).total_seconds()
        if uploading and calling:
            steps["upload"] = (calling - uploading).total_seconds()
        if gpu_line:
            steps["gpu_total"] = float(gpu_line[1])
        steps["total"] = (run["end"] - run["start"]).total_seconds()

    cost = None
    if gpu_line and gpu_line[3]:
        cost = {"usd": float(gpu_line[3]), "gpuSeconds": None, "gpu": gpu, "containers": None, "estimated": False}
    elif gpu_line and not parallel:
        cost = estimate_cost(float(gpu_line[1]), gpu)

    return {"mode": mode, "status": status, "startedAt": iso(run["start"]), "finishedAt": iso(run["end"]),
            "clips": clips, "pipeline": "parallel" if parallel else "single-gpu",
            "videoSeconds": video_seconds, "timings": steps, "cost": cost, "recordedBy": "log"}


def runner_entry(job: dict, *, mode: str, status: str, started: float, timings: dict,
                 cost: dict | None, clips: int | None) -> dict:
    """The entry runner.py appends when a run ends (started: time.time() at the start)."""
    steps = {k: float(v) for k, v in timings.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"mode": mode, "status": status,
            "startedAt": iso(dt.datetime.fromtimestamp(started, dt.timezone.utc)),
            "finishedAt": iso(dt.datetime.now(dt.timezone.utc)),
            "clips": clips, "pipeline": "parallel", "videoSeconds": _video_seconds(job),
            "timings": steps, "cost": {**cost, "estimated": False} if cost else None,
            "recordedBy": "runner"}


def history_before(job: dict, log_text: str, started: float, tz: dt.tzinfo | None = None) -> list[dict]:
    """The runs a job had before this one: job.json's own history, or (for a job from
    before the history existed) the runs found in runner.log that started earlier."""
    if isinstance(job.get("runs"), list):
        return list(job["runs"])
    cutoff = dt.datetime.fromtimestamp(started - 1, dt.timezone.utc)
    return [run for run in parse_log(log_text, job, tz)
            if (parse_iso(run["startedAt"]) or cutoff) < cutoff]
