"""
Run history for the stats page.

When a run of a job ends (first run, re-rank or failed attempt), runner.py records it
twice:

    job.json "runs"          the job's own history (deleted with the job folder)
    <data>/stats/runs.jsonl  the stats ledger: one JSON object per line, append-only.
                             The stats page reads only this file, so deleting a video
                             keeps its cost and times in the stats.

A run entry:
    {"mode": "full" | "rerank", "status": "done" | "no-clips" | "failed",
     "startedAt": ISO UTC, "finishedAt": ISO UTC, "clips": 5, "videoSeconds": 2243.0,
     "timings": {"download", "upload", "replay", "gpu_transcribe", "gpu_select",
                 "gpu_render", "gpu_total", "total"}  (seconds; a step that did not run is missing;
                 two-phase runs add "audio", "downloadWait", "cut", "clipUpload", see phases.py,
                 and their "download" overlaps phase 1: only "downloadWait" of it adds to "total"),
     "cost": {"usd", "gpuSeconds", "gpu", "containers", "stages", "rates",
              "gemini": {"usd", "calls", "tokens", "models", "unpriced", "prices"}} | null}
     ("usd" is Modal's alone; "gemini" is the Gemini API estimate from the calls' token
     counts, see gemini_costs.py, and is missing for runs before it was recorded)

Optional run fields (only when known): "failedStage", "maxClips", "promptVersion",
"usdInr", "clipSources", and for measuring speed "clipTimings" (per clip: seconds,
enterSeconds, steps, encoder), "coldStarts" (per container: stage, enterSeconds, gpu)
and "resumedFrom" (the clips of an earlier attempt were downloaded instead of rendered).

A ledger line adds the job it belongs to:
    {"jobId", "title", "source": "youtube" | "upload", "youtubeUrl", "realTimeFactor", ...run entry}

No third-party imports (it uses fx, settings and jobstore from this folder).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib

import fx
import settings
from jobstore import BACKEND_DIR, Job, log

LEDGER = pathlib.Path("stats") / "runs.jsonl"


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _video_seconds(job: dict) -> float | None:
    parakeet = (job.get("engine") or {}).get("parakeet") or {}
    seconds = parakeet.get("audioSeconds") or (job.get("meta") or {}).get("duration")
    return float(seconds) if seconds else None


def runner_entry(job: dict, *, mode: str, status: str, started: float, timings: dict,
                 cost: dict | None, clips: int | None) -> dict:
    """The entry for a run that just ended (started: time.time() at its start)."""
    steps = {k: float(v) for k, v in timings.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    return {"mode": mode, "status": status,
            "startedAt": iso(dt.datetime.fromtimestamp(started, dt.timezone.utc)),
            "finishedAt": iso(dt.datetime.now(dt.timezone.utc)),
            "clips": clips, "videoSeconds": _video_seconds(job),
            "timings": steps, "cost": dict(cost) if cost else None}


def ledger_record(job: dict, entry: dict) -> dict:
    parakeet = (job.get("engine") or {}).get("parakeet") or {}
    return {"jobId": job.get("id"), "title": job.get("title"), "source": job.get("source"),
            "youtubeUrl": job.get("youtubeUrl"), "realTimeFactor": parakeet.get("realTimeFactor"), **entry}


def ledger_path(data_dir: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(data_dir) / LEDGER


def append_ledger(data_dir: pathlib.Path, record: dict) -> pathlib.Path:
    """Add one line to the ledger (created with its folder on first use). One short
    write per run, so two runners finishing together cannot interleave a line."""
    path = ledger_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as ledger:
        ledger.write(line)
    return path


def read_ledger(data_dir: pathlib.Path) -> list[dict]:
    """Every recorded run, oldest first; unreadable lines are skipped."""
    path = ledger_path(data_dir)
    if not path.exists():
        return []
    runs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("jobId") and record.get("startedAt"):
            runs.append(record)
    return runs


def prompt_version() -> str | None:
    """Short hash of the ranking prompts, so the stats can later be split by prompt."""
    digest = hashlib.sha256()
    try:
        for path in sorted((BACKEND_DIR / "prompts").glob("*.md")):
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    except OSError:
        return None
    return digest.hexdigest()[:8]


def clip_sources(job: Job, details: list[dict] | None, ranking: dict | None) -> dict | None:
    """Most replayed and SponsorBlock numbers of a run, for the stats page."""
    record = job.data.get("replay")
    if not record and not (ranking or {}).get("replay"):
        return None
    details = details or []
    audience = [d["audience"] for d in details if isinstance(d.get("audience"), (int, float))]
    sponsor = (record or {}).get("sponsor") or {}
    skipped: dict[str, float] = {}
    for segment in sponsor.get("segments") or []:
        seconds = max(0.0, float(segment.get("end") or 0) - float(segment.get("start") or 0))
        skipped[segment.get("category") or "other"] = round(skipped.get(segment.get("category") or "other", 0)
                                                             + seconds, 1)
    report = (ranking or {}).get("replay") or {}
    return {"graph": bool((record or {}).get("available")), "hotspots": len((record or {}).get("peaks") or []),
            "clips": len(details), "clipsOnHotspot": sum(1 for d in details if d.get("peak")),
            "audienceAvg": round(sum(audience) / len(audience), 1) if audience else None,
            "sponsorBlock": sponsor.get("segments") is not None if record else False,
            "skippedSeconds": skipped, "droppedForSponsor": int(report.get("droppedForSponsor") or 0)}


def record_run(job: Job, *, mode: str, status: str, started: float, timings: dict,
               cost: dict | None = None, clips: list[str] | None = None, details: list[dict] | None = None,
               ranking: dict | None = None, failed_stage: str | None = None,
               extra: dict | None = None) -> list[dict] | None:
    """Record a run that just ended: returns job.json "runs" with it added, and appends it
    to the stats ledger (<data>/stats/runs.jsonl, which outlives the job folder).
    Never raises: stats are best effort."""
    data_dir = job.dir.parent.parent
    try:
        entry = runner_entry(job.data, mode=mode, status=status, started=started, timings=timings,
                             cost=cost, clips=None if clips is None else len(clips))
        if failed_stage:
            entry["failedStage"] = failed_stage
        entry["maxClips"] = settings.MAX_CLIPS
        entry["promptVersion"] = prompt_version()
        entry["usdInr"] = fx.usd_inr(data_dir, log=log)
        sources = clip_sources(job, details, ranking) if status != "failed" else None
        if sources:
            entry["clipSources"] = sources
        entry.update(extra or {})
    except Exception as exc:  # noqa: BLE001
        log(f"Could not record the run for the stats page: {exc}")
        return job.data.get("runs")
    try:
        append_ledger(data_dir, ledger_record(job.data, entry))
    except Exception as exc:  # noqa: BLE001
        log(f"Could not add the run to the stats ledger: {exc}")
    return [*(job.data.get("runs") or []), entry]
