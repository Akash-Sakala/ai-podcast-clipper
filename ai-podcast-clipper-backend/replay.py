"""
YouTube "Most replayed" graph and SponsorBlock segments: where viewers rewatch, and
what to leave out.

The runner fetches both on the laptop before every run of a YouTube job
(`fetch_signals`): one metadata-only yt-dlp call (no download, about 3 s) and one
SponsorBlock call. When YouTube refuses the metadata call ("confirm you're not a
bot") but not the download, the runner takes the graph from the download's info
(`with_graph`). The result is stored in job.json "replay" and handed to Gemini
ranking (moments.py) through the context.

    heatmap   100 equal segments of the video, each with a replay intensity 0-1
              (1 = the video's most replayed segment). Only videos with enough views
              (usually tens of thousands) have one.
    peaks     replay hotspots found in the heatmap, corrected for its biases:
              - the opening segments (everyone starts there),
              - one-segment bumps at chapter starts (viewers jump to chapters),
              - sponsor reads (skipped, so they are valleys anyway);
              then only peaks that stand clearly above the video's median, merged
              when closer than PEAK_MERGE_S.
    sponsor   SponsorBlock segments to keep out of clips (sponsor, self-promotion,
              subscribe reminders, intro, outro, cold-open previews) and the
              highlight point viewers marked, if any. SponsorBlock data is
              CC BY-NC-SA 4.0, https://sponsor.ajay.app

Standard library only at import time (yt-dlp is imported by `fetch_youtube`), so
the moments.py container and the tests can use the pure helpers.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

# --------------------------------------------------------------------------- #
# Settings (laptop side: runner.py reads them for every run, no redeploy)
# --------------------------------------------------------------------------- #

# Share of the final score that comes from the audience (0 = Gemini only).
DEFAULT_WEIGHT = 0.4
# Peaks must rise this far above the video's median replay intensity (0-1 scale).
PEAK_MIN_LIFT = 0.12
# Peaks closer than this are one hotspot.
PEAK_MERGE_S = 90.0
# Never treat the first / last seconds as hotspots (start-of-video and end-screen bias).
SKIP_START_S = 45.0
SKIP_END_S = 30.0
# Neighbouring segments at least this share of the peak value belong to the hotspot.
HOT_SHARE = 0.85
MAX_PEAKS = 8

SPONSORBLOCK_URL = "https://sponsor.ajay.app/api/skipSegments/"
# Kept out of clips. "filler" is left out on purpose: SponsorBlock calls it very aggressive.
SKIP_CATEGORIES = ("sponsor", "selfpromo", "interaction", "intro", "outro", "preview")
HIGHLIGHT_CATEGORY = "poi_highlight"
# A clip may touch a skipped segment by this much (seconds, or share of the clip).
SPONSOR_OVERLAP_S = 3.0
SPONSOR_OVERLAP_SHARE = 0.15

_YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def youtube_id(url: str | None) -> str | None:
    match = _YOUTUBE_ID.search(url or "")
    return match.group(1) if match else None


# --------------------------------------------------------------------------- #
# Fetching (laptop)
# --------------------------------------------------------------------------- #

def compact_heatmap(raw: list | None) -> list[list[float]]:
    """yt-dlp's [{start_time, end_time, value}] -> [[start, end, value], ...] sorted by time."""
    out = []
    for point in raw or []:
        try:
            start, end, value = float(point["start_time"]), float(point["end_time"]), float(point["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            out.append([round(start, 2), round(end, 2), round(min(max(value, 0.0), 1.0), 4)])
    return sorted(out)


def compact_chapters(raw: list | None) -> list[dict]:
    out = []
    for chapter in raw or []:
        try:
            out.append({"start": round(float(chapter["start_time"]), 2), "end": round(float(chapter["end_time"]), 2),
                        "title": str(chapter.get("title") or "")[:120]})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def fetch_youtube(url: str, cookies_browser: str | None = None, *, cookies_file: str | None = None,
                  ipv4: bool = False) -> dict:
    """Metadata only, no download. `process=False` skips format selection, so missing
    formats (YouTube's SABR streaming) cannot fail the call. Raises on yt-dlp errors."""
    import yt_dlp

    options = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
               "js_runtimes": {"deno": {}, "node": {}}}
    if cookies_file:
        options["cookiefile"] = cookies_file
    elif cookies_browser:
        options["cookiesfrombrowser"] = (cookies_browser,)
    if ipv4:
        options["source_address"] = "0.0.0.0"
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False, process=False) or {}


def fetch_sponsorblock(video_id: str, timeout_s: float = 10.0, opener: Callable | None = None) -> list[dict]:
    """SponsorBlock segments for one video, through the privacy-preserving hashed
    endpoint (only the first 4 characters of the video id's SHA-256 leave the laptop).
    [] when nobody submitted any (HTTP 404). Raises on network errors."""
    prefix = hashlib.sha256(video_id.encode()).hexdigest()[:4]
    query = urllib.parse.urlencode({
        "categories": json.dumps([*SKIP_CATEGORIES, HIGHLIGHT_CATEGORY]),
        "actionTypes": json.dumps(["skip", "mute", "poi"]),
    })
    request = urllib.request.Request(f"{SPONSORBLOCK_URL}{prefix}?{query}",
                                     headers={"User-Agent": "ai-podcast-clipper (personal use)"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout_s) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    for entry in data if isinstance(data, list) else []:
        if entry.get("videoID") == video_id:
            return parse_sponsor_segments(entry.get("segments"))
    return []


def parse_sponsor_segments(raw: list | None) -> list[dict]:
    out = []
    for segment in raw or []:
        try:
            start, end = (float(x) for x in segment["segment"][:2])
        except (KeyError, TypeError, ValueError):
            continue
        category = str(segment.get("category") or "")
        if category not in (*SKIP_CATEGORIES, HIGHLIGHT_CATEGORY):
            continue
        out.append({"start": round(start, 2), "end": round(max(end, start), 2), "category": category,
                    "votes": segment.get("votes"), "locked": bool(segment.get("locked"))})
    return sorted(out, key=lambda s: s["start"])


def fetch_signals(url: str, *, cookies_browser: str | None = None, sponsorblock: bool = True,
                  max_peaks: int = MAX_PEAKS, fetch_info: Callable | None = None,
                  fetch_sponsors: Callable | None = None, log: Callable = print) -> tuple[dict, dict | None]:
    """(replay, info): the job.json "replay" record and yt-dlp's info dict (None if the
    metadata call failed). Never raises: a failure is recorded in replay["errors"]."""
    fetch_info = fetch_info or fetch_youtube
    fetch_sponsors = fetch_sponsors or fetch_sponsorblock
    replay: dict = {"fetchedAt": now_iso(), "available": False, "heatmap": [], "chapters": [], "peaks": [],
                    "sponsor": None, "errors": []}
    info = None
    try:
        info = fetch_info(url, cookies_browser)
        replay["heatmap"] = compact_heatmap(info.get("heatmap"))
        replay["chapters"] = compact_chapters(info.get("chapters"))
        replay["duration"] = info.get("duration")
        replay["views"] = info.get("view_count")
        replay["available"] = len(replay["heatmap"]) >= 10
    except Exception as exc:  # noqa: BLE001 - the job goes on without the graph
        replay["errors"].append(f"YouTube: {type(exc).__name__}: {str(exc)[:200]}")
        log(f"Could not read the Most replayed graph: {exc}")

    video_id = (info or {}).get("id") or youtube_id(url)
    if sponsorblock and video_id:
        try:
            segments = fetch_sponsors(video_id)
            replay["sponsor"] = {
                "segments": [s for s in segments if s["category"] != HIGHLIGHT_CATEGORY],
                "highlight": next((s["start"] for s in sorted(segments, key=lambda s: -(s.get("votes") or 0))
                                   if s["category"] == HIGHLIGHT_CATEGORY), None),
            }
        except Exception as exc:  # noqa: BLE001
            replay["errors"].append(f"SponsorBlock: {type(exc).__name__}: {str(exc)[:200]}")
            log(f"Could not read SponsorBlock segments: {exc}")

    replay["peaks"] = find_peaks(replay["heatmap"], replay["chapters"], skip_segments(replay),
                                 duration=replay.get("duration"), max_peaks=max_peaks)
    return replay, info


def with_graph(replay: dict, info: dict | None, max_peaks: int = MAX_PEAKS) -> dict:
    """The replay record with the graph taken from another yt-dlp info dict (the video
    download's), for when the metadata-only call was refused. Unchanged if `info` has no
    graph either."""
    heatmap = compact_heatmap((info or {}).get("heatmap"))
    if len(heatmap) < 10:
        return replay
    updated = {**replay, "heatmap": heatmap, "chapters": compact_chapters(info.get("chapters")),
               "duration": info.get("duration") or replay.get("duration"),
               "views": info.get("view_count") or replay.get("views"), "available": True,
               "errors": [e for e in replay.get("errors") or [] if not e.startswith("YouTube")]}
    updated["peaks"] = find_peaks(updated["heatmap"], updated["chapters"], skip_segments(updated),
                                  duration=updated.get("duration"), max_peaks=max_peaks)
    return updated


def skip_segments(replay: dict | None) -> list[dict]:
    return list(((replay or {}).get("sponsor") or {}).get("segments") or [])


# --------------------------------------------------------------------------- #
# Peaks (pure)
# --------------------------------------------------------------------------- #

def _smooth(values: list[float]) -> list[float]:
    n = len(values)
    if n < 3:
        return list(values)
    return [(values[max(i - 1, 0)] + 2 * values[i] + values[min(i + 1, n - 1)]) / 4 for i in range(n)]


def adjusted_values(heatmap: list, chapters: list | None = None, skips: list | None = None) -> list[float]:
    """Replay intensity per segment with the graph's biases taken out (see the module
    docstring). Not smoothed."""
    values = [float(p[2]) for p in heatmap]
    n = len(values)
    if not n:
        return []
    # Chapter starts: a bump only in the segment where a chapter starts is navigation,
    # not interest; keep it only when the next segment stays as high.
    for chapter in chapters or []:
        t = float(chapter.get("start") or 0)
        if t <= 0:
            continue
        k = next((i for i, p in enumerate(heatmap) if p[0] <= t < p[1]), None)
        if k is None or k == 0:
            continue
        neighbours = [values[k - 1]] + ([values[k + 1]] if k + 1 < n else [])
        values[k] = min(values[k], max(neighbours))
    # Sponsor reads are skipped by viewers and never become clips.
    for skip in skips or []:
        for i, p in enumerate(heatmap):
            covered = min(p[1], skip["end"]) - max(p[0], skip["start"])
            if covered > 0.5 * (p[1] - p[0]):
                values[i] = min(values[i], statistics.median(values))
    return values


def find_peaks(heatmap: list, chapters: list | None = None, skips: list | None = None,
               duration: float | None = None, max_peaks: int = MAX_PEAKS, min_lift: float = PEAK_MIN_LIFT,
               merge_s: float = PEAK_MERGE_S) -> list[dict]:
    """Replay hotspots, strongest first: [{start, end, center, value, lift, rank}].
    `start`-`end` is the hot span (neighbouring segments within HOT_SHARE of the peak)."""
    if len(heatmap) < 10:
        return []
    raw = [float(p[2]) for p in heatmap]
    adjusted = adjusted_values(heatmap, chapters, skips)
    smooth = _smooth(adjusted)
    median = statistics.median(smooth)
    total = float(duration or heatmap[-1][1])
    n = len(heatmap)

    candidates = []
    for i in range(n):
        left = smooth[i - 1] if i > 0 else -1.0
        right = smooth[i + 1] if i + 1 < n else -1.0
        if smooth[i] < left or smooth[i] < right:
            continue
        center = (heatmap[i][0] + heatmap[i][1]) / 2
        if center < SKIP_START_S or center > total - SKIP_END_S:
            continue
        if smooth[i] - median < min_lift:
            continue
        candidates.append(i)

    peaks: list[dict] = []
    for i in sorted(candidates, key=lambda j: smooth[j], reverse=True):
        center = (heatmap[i][0] + heatmap[i][1]) / 2
        if any(abs(center - p["center"]) < merge_s for p in peaks):
            continue
        lo = hi = i
        while lo > 0 and adjusted[lo - 1] >= HOT_SHARE * adjusted[i]:
            lo -= 1
        while hi + 1 < n and adjusted[hi + 1] >= HOT_SHARE * adjusted[i]:
            hi += 1
        peaks.append({"start": heatmap[lo][0], "end": heatmap[hi][1], "center": round(center, 2),
                      "value": round(raw[i], 3), "lift": round(smooth[i] - median, 3)})
        if len(peaks) >= max_peaks:
            break
    return [dict(p, rank=k + 1) for k, p in enumerate(peaks)]


# --------------------------------------------------------------------------- #
# Scores (pure, used by moments.py in the container)
# --------------------------------------------------------------------------- #

def audience_score(start: float, end: float, heatmap: list, chapters: list | None = None,
                   skips: list | None = None) -> float | None:
    """0-100: how much viewers replay this span compared with the rest of the video.
    50 = the video's median, 100 = its most replayed segment, below 50 = parts viewers
    skip. None when there is no heatmap. Uses the bias-corrected values and the best
    segment the clip covers well (at least a third of it, or 10 s)."""
    if len(heatmap) < 10 or end <= start:
        return None
    values = adjusted_values(heatmap, chapters, skips)
    median = statistics.median(values)
    top = max(values)
    best = None
    for (s, e, _), value in zip(heatmap, values):
        covered = min(e, end) - max(s, start)
        if covered >= min(10.0, (e - s) / 3) or (covered > 0 and covered >= 0.8 * (end - start)):
            best = value if best is None else max(best, value)
    if best is None:
        return None
    if top - median < 1e-6:
        return 50.0
    if best >= median:
        share = 0.5 + 0.5 * (best - median) / (top - median)
    else:
        share = 0.5 * best / median if median > 0 else 0.5
    return round(100 * min(max(share, 0.0), 1.0), 1)


def blend(gemini_score: float, audience: float | None, weight: float = DEFAULT_WEIGHT) -> float:
    if audience is None:
        return round(gemini_score, 1)
    weight = min(max(weight, 0.0), 1.0)
    return round((1 - weight) * gemini_score + weight * audience, 1)


def sponsor_overlap(start: float, end: float, skips: list | None) -> float:
    return sum(max(0.0, min(end, s["end"]) - max(start, s["start"])) for s in skips or [])


def touches_sponsor(start: float, end: float, skips: list | None) -> bool:
    overlap = sponsor_overlap(start, end, skips)
    return overlap > SPONSOR_OVERLAP_S or overlap > SPONSOR_OVERLAP_SHARE * max(end - start, 1e-6)


def covering_peak(start: float, end: float, peaks: list | None) -> dict | None:
    """The strongest peak whose centre lies inside the clip."""
    inside = [p for p in peaks or [] if start <= p["center"] <= end]
    return min(inside, key=lambda p: p["rank"]) if inside else None


# --------------------------------------------------------------------------- #
# Context for Gemini ranking
# --------------------------------------------------------------------------- #

def ranking_context(replay: dict | None, weight: float = DEFAULT_WEIGHT) -> dict | None:
    """The part of job.json "replay" that process_job needs (it travels in `context`)."""
    if not replay:
        return None
    sponsor = replay.get("sponsor") or {}
    available = bool(replay.get("available"))
    context = {
        "heatmap": (replay.get("heatmap") or []) if available else [],
        "chapters": replay.get("chapters") or [],
        "peaks": (replay.get("peaks") or []) if available else [],
        "skips": sponsor.get("segments") or [],
        "highlight": sponsor.get("highlight"),
        "weight": weight,
    }
    if not (context["heatmap"] or context["skips"] or context["highlight"] is not None):
        return None
    return context


def hit_report(peaks: list, clip_details: list) -> dict:
    """How the clips line up with the hotspots (for --backfill-replay)."""
    clips = [c for c in clip_details or [] if c.get("start") is not None and c.get("end") is not None]
    hits = [c for c in clips if covering_peak(float(c["start"]), float(c["end"]), peaks)]
    top = min(peaks, key=lambda p: p["rank"]) if peaks else None
    return {
        "clips": len(clips),
        "clipsOnPeak": len(hits),
        "topPeakCovered": bool(top and any(float(c["start"]) <= top["center"] <= float(c["end"]) for c in clips)),
    }
