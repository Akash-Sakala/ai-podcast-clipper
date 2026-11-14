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
                 "gpu_render", "gpu_total", "total"}  (seconds; a step that did not run is missing),
     "cost": {"usd", "gpuSeconds", "gpu", "containers"} | null}

A ledger line adds the job it belongs to:
    {"jobId", "title", "source": "youtube" | "upload", "youtubeUrl", "realTimeFactor", ...run entry}

Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib

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
