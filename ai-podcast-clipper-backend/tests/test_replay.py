"""Most replayed graph, SponsorBlock and their use in ranking (all offline)."""

import io
import json
import pathlib
import sys
import urllib.error

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from test_pipeline import make_words  # noqa: E402

import jobstore  # noqa: E402
import maintenance  # noqa: E402
import moments  # noqa: E402
import pipeline  # noqa: E402
import replay  # noqa: E402
import settings  # noqa: E402
import youtube  # noqa: E402

DURATION = 3000.0       # 100 segments of 30 s


def heatmap_with(values: dict[int, float], base: float = 0.3, n: int = 100, duration: float = DURATION):
    step = duration / n
    return [[round(i * step, 2), round((i + 1) * step, 2), values.get(i, base)] for i in range(n)]


def quiet(*_):
    pass


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def test_youtube_id_from_common_links():
    assert replay.youtube_id("https://youtu.be/BqSxjmvXzzY?si=abc") == "BqSxjmvXzzY"
    assert replay.youtube_id("https://www.youtube.com/watch?v=7ARBJQn6QkM&t=10") == "7ARBJQn6QkM"
    assert replay.youtube_id("https://www.youtube.com/live/7ARBJQn6QkM") == "7ARBJQn6QkM"
    assert replay.youtube_id("not a link") is None


def test_compact_heatmap_sorts_clamps_and_skips_bad_points():
    raw = [{"start_time": 30, "end_time": 60, "value": 1.2},
           {"start_time": 0, "end_time": 30, "value": 0.5},
           {"start_time": 60, "end_time": 60, "value": 0.5},
           {"start_time": "x"}]
    assert replay.compact_heatmap(raw) == [[0.0, 30.0, 0.5], [30.0, 60.0, 1.0]]


# --------------------------------------------------------------------------- #
# Peaks
# --------------------------------------------------------------------------- #

def test_peaks_ignore_opening_chapter_bumps_and_sponsor_reads():
    heatmap = heatmap_with({
        0: 1.0,                 # everyone starts here
        20: 0.95,               # sponsor read (SponsorBlock says so)
        30: 0.9,                # a chapter starts here and only this segment is high
        50: 0.85, 51: 0.8,      # a real hotspot
        80: 0.6,                # a smaller one
    })
    chapters = [{"start": 0, "end": 900, "title": "Intro"}, {"start": 905, "end": 3000, "title": "Topic"}]
    skips = [{"start": 595, "end": 640, "category": "sponsor"}]
    peaks = replay.find_peaks(heatmap, chapters, skips, duration=DURATION)

    centres = [p["center"] for p in peaks]
    assert 1515.0 in centres and 2415.0 in centres
    assert all(c > 100 for c in centres)                     # not the opening
    assert all(abs(c - 615) > 30 for c in centres)           # not the sponsor read
    assert all(abs(c - 915) > 30 for c in centres)           # not the chapter-start bump
    assert peaks[0]["center"] == 1515.0 and peaks[0]["rank"] == 1
    assert peaks[0]["start"] == 1500.0 and peaks[0]["end"] == 1560.0   # segments 50-51 are the hot span


def test_chapter_start_kept_when_interest_continues():
    heatmap = heatmap_with({30: 0.9, 31: 0.88})
    peaks = replay.find_peaks(heatmap, [{"start": 905, "end": 3000, "title": "Big story"}], duration=DURATION)
    assert peaks and 900 <= peaks[0]["center"] <= 960


def test_close_peaks_merge_and_flat_graph_has_none():
    peaks = replay.find_peaks(heatmap_with({40: 0.9, 42: 0.8}), duration=DURATION)
    assert len(peaks) == 1 and peaks[0]["value"] == 0.9
    assert replay.find_peaks(heatmap_with({}), duration=DURATION) == []
    assert replay.find_peaks(heatmap_with({40: 0.35}), duration=DURATION) == []   # too small a lift
    assert replay.find_peaks([], duration=DURATION) == []


# --------------------------------------------------------------------------- #
# Scores
# --------------------------------------------------------------------------- #

def test_audience_score_scale():
    heatmap = heatmap_with({50: 1.0, 10: 0.05})
    assert replay.audience_score(1490, 1540, heatmap) == 100.0       # the most replayed segment
    assert replay.audience_score(2100, 2150, heatmap) == 50.0        # an ordinary part
    assert replay.audience_score(300, 330, heatmap) < 50             # a part viewers skip
    assert replay.audience_score(0, 40, []) is None


def test_blend_keeps_gemini_score_without_a_graph():
    assert replay.blend(70.0, None, 0.4) == 70.0
    assert replay.blend(70.0, 100.0, 0.4) == 82.0
    assert replay.blend(70.0, 100.0, 0.0) == 70.0


def test_touches_sponsor_tolerates_a_brush():
    skips = [{"start": 100.0, "end": 160.0, "category": "sponsor"}]
    assert replay.touches_sponsor(120, 170, skips)
    assert not replay.touches_sponsor(158, 210, skips)                # 2 s overlap
    assert not replay.touches_sponsor(200, 250, skips)


# --------------------------------------------------------------------------- #
# SponsorBlock
# --------------------------------------------------------------------------- #

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_sponsorblock_uses_hash_prefix_and_picks_the_video():
    seen = {}
    body = [
        {"videoID": "otherVideo1", "segments": [{"segment": [1, 2], "category": "sponsor"}]},
        {"videoID": "7ARBJQn6QkM", "segments": [
            {"segment": [0, 28.4], "category": "preview", "votes": 3},
            {"segment": [1814, 1814], "category": "poi_highlight", "votes": 9},
            {"segment": [5, 9], "category": "music_offtopic"},
        ]},
    ]

    def opener(request, timeout):
        seen["url"] = request.full_url
        return FakeResponse(json.dumps(body).encode())

    segments = replay.fetch_sponsorblock("7ARBJQn6QkM", opener=opener)
    assert "/api/skipSegments/" in seen["url"] and "7ARBJQn6QkM" not in seen["url"]
    assert [s["category"] for s in segments] == ["preview", "poi_highlight"]


def test_sponsorblock_404_means_no_segments():
    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    assert replay.fetch_sponsorblock("BqSxjmvXzzY", opener=opener) == []


def test_fetch_signals_records_graph_peaks_and_highlight():
    info = {"id": "abcdefghijk", "duration": DURATION, "view_count": 50000,
            "heatmap": [{"start_time": s, "end_time": e, "value": v} for s, e, v in heatmap_with({50: 0.9})],
            "chapters": [{"start_time": 0, "end_time": 3000, "title": "All"}]}
    sponsors = [{"start": 10, "end": 40, "category": "intro", "votes": 1},
                {"start": 900, "end": 900, "category": "poi_highlight", "votes": 1},
                {"start": 1200, "end": 1200, "category": "poi_highlight", "votes": 5}]
    record, got = replay.fetch_signals("https://youtu.be/abcdefghijk", fetch_info=lambda url, browser: info,
                                       fetch_sponsors=lambda vid: sponsors, log=quiet)
    assert got is info and record["available"] and len(record["heatmap"]) == 100
    assert record["peaks"][0]["center"] == 1515.0
    assert record["sponsor"] == {"segments": [sponsors[0]], "highlight": 1200}   # most votes wins
    assert record["errors"] == []


def test_fetch_signals_never_raises():
    def blocked(url, browser):
        raise RuntimeError("Sign in to confirm you're not a bot")

    def down(video_id):
        raise OSError("timed out")

    record, info = replay.fetch_signals("https://youtu.be/abcdefghijk", fetch_info=blocked, fetch_sponsors=down,
                                        log=quiet)
    assert info is None and not record["available"] and record["sponsor"] is None
    assert len(record["errors"]) == 2 and "not a bot" in record["errors"][0]


def test_ranking_context_only_when_there_is_something():
    assert replay.ranking_context(None) is None
    assert replay.ranking_context({"available": False, "heatmap": [], "sponsor": None}) is None
    context = replay.ranking_context({"available": False, "heatmap": [[0, 1, 1]], "peaks": [{"center": 0}],
                                      "sponsor": {"segments": [{"start": 1, "end": 2}], "highlight": None}}, 0.3)
    assert context["heatmap"] == [] and context["peaks"] == [] and context["skips"] and context["weight"] == 0.3


# --------------------------------------------------------------------------- #
# Ranking with the graph
# --------------------------------------------------------------------------- #

def line_at(words, t):
    lines = moments.build_lines(words)
    return min(lines, key=lambda l: abs(l["start"] - t))


def scores(level):
    return {"hook_score": level, "standalone": level, "payoff": level, "emotion": level, "value": level,
            "shareability": level}


def make_context(heatmap, skips=(), highlight=None, weight=0.4):
    peaks = replay.find_peaks(heatmap, [], list(skips), duration=DURATION)
    return {"title": "Talk", "replay": {"heatmap": heatmap, "chapters": [], "peaks": peaks, "skips": list(skips),
                                        "highlight": highlight, "weight": weight}}


def test_hotspot_gemini_skipped_is_cut_in_code_and_rescored():
    words = make_words(400)                                  # ~2520 s
    heatmap = heatmap_with({60: 1.0}, duration=2520.0)       # centre ~1524 s
    calls = []

    def generate(client, model, system, user, schema, report=None, log=print):
        calls.append(user)
        if user.startswith("Candidates"):                    # the re-scoring call
            n = user.count("\nid ") + 1
            return [{"id": i, "hook": 6, "standalone": 6, "payoff": 6, "emotion": 6, "value": 6,
                     "shareability": 6, "reason": "ok"} for i in range(n)]
        picks = []
        for t in (300, 700, 2000):
            line = line_at(words, t)
            picks.append({"start": line["start"], "end": line["start"] + 45, "title": f"at {t}", "hook": "h",
                          "reason": "r", "hotspot": "", **scores(7)})
        return picks

    context = make_context(heatmap)
    report = {}
    chosen = moments.select_moments(words, 4, None, "fake", context, generate=generate, report=report, log=quiet)

    assert "Replay hotspots" in calls[0] and "H1: centre" in calls[0]
    assert "1.0" not in calls[0].split("Transcript")[0].split("H1:")[1].split("\n")[0].split("replayed")[0]
    assert any(c.startswith("Candidates") for c in calls)
    on_peak = [c for c in chosen if c.get("peak")]
    assert len(on_peak) == 1 and on_peak[0]["source"] == "gemini" and on_peak[0]["peak"]["id"] == "H1"
    assert on_peak[0]["audience"] == 100.0 and on_peak[0]["rank"] == 1        # 60 x 0.6 + 100 x 0.4 = 76
    assert report["replay"]["cutInCode"] == 1 and report["replay"]["chosenOnHotspot"] == 1


def test_strong_gemini_pick_beats_a_weak_hotspot_and_blend_is_recorded():
    words = make_words(400)
    heatmap = heatmap_with({60: 1.0}, duration=2520.0)
    peak_line = line_at(words, 1500)
    great = line_at(words, 400)

    def generate(client, model, system, user, schema, report=None, log=print):
        return [
            {"start": peak_line["start"], "end": peak_line["start"] + 45, "title": "peak", "hook": "h", "reason": "r",
             "hotspot": "H1", **scores(3)},
            {"start": great["start"], "end": great["start"] + 45, "title": "great", "hook": "h", "reason": "r",
             "hotspot": "", **scores(10)},
        ]

    chosen = moments.select_moments(words, 2, None, "fake", make_context(heatmap), generate=generate, log=quiet,
                                    min_clips=1)
    assert [c["title"] for c in chosen] == ["great", "peak"]
    assert chosen[0]["gemini"] == 100.0 and chosen[0]["audience"] == 50.0 and chosen[0]["score"] == 80.0
    assert chosen[1]["gemini"] == 30.0 and chosen[1]["score"] == 58.0 and chosen[1]["peak"]["id"] == "H1"


def test_sponsor_reads_never_become_clips():
    words = make_words(400)
    sponsor_line = line_at(words, 600)
    other = line_at(words, 1200)
    skips = [{"start": sponsor_line["start"] - 5, "end": sponsor_line["start"] + 60, "category": "sponsor"}]

    def generate(client, model, system, user, schema, report=None, log=print):
        assert "Leave out" in user and "(sponsor)" in user
        return [{"start": sponsor_line["start"], "end": sponsor_line["start"] + 45, "title": "ad", "hook": "h",
                 "reason": "r", "hotspot": "", **scores(10)},
                {"start": other["start"], "end": other["start"] + 45, "title": "talk", "hook": "h", "reason": "r",
                 "hotspot": "", **scores(5)}]

    report = {}
    chosen = moments.select_moments(words, 1, None, "fake", make_context([], skips), generate=generate,
                                    report=report, log=quiet, min_clips=1)
    assert [c["title"] for c in chosen] == ["talk"]
    assert report["replay"]["droppedForSponsor"] == 1


def test_without_replay_context_ranking_is_unchanged():
    words = make_words(300)
    calls = []

    def generate(client, model, system, user, schema, report=None, log=print):
        calls.append(user)
        line = line_at(words, 500)
        return [{"start": line["start"], "end": line["start"] + 45, "title": "t", "hook": "h", "reason": "r",
                 "hotspot": "", **scores(7)}]

    report = {}
    chosen = moments.select_moments(words, 1, None, "fake", {"title": "x"}, generate=generate, report=report,
                                    log=quiet, min_clips=1)
    # One proposal call plus the judge call; neither mentions replay or SponsorBlock.
    assert len(calls) == 2 and calls[1].startswith("Candidates")
    assert all("Replay hotspots" not in c and "Leave out" not in c for c in calls)
    assert chosen[0]["score"] == 70.0 and chosen[0]["audience"] is None and chosen[0]["peak"] is None
    assert "replay" not in report


def test_hotspot_clip_stays_below_gemini_picks_when_rescoring_fails():
    words = make_words(400)
    heatmap = heatmap_with({60: 1.0}, duration=2520.0)

    def generate(client, model, system, user, schema, report=None, log=print):
        if user.startswith("Candidates"):
            raise RuntimeError("503 UNAVAILABLE")
        line = line_at(words, 300)
        return [{"start": line["start"], "end": line["start"] + 45, "title": "gemini", "hook": "h", "reason": "r",
                 "hotspot": "", **scores(2)}]

    chosen = moments.select_moments(words, 3, None, "fake", make_context(heatmap), generate=generate, log=quiet,
                                    min_clips=3)
    assert chosen[0]["title"] == "gemini"
    assert chosen[1]["source"] == "replay" and chosen[1]["peak"]["id"] == "H1"
    assert chosen[2]["source"] == "fallback"


def test_sponsorblock_highlight_becomes_a_hotspot():
    spots = moments.build_hotspots({"peaks": [{"start": 900, "end": 930, "center": 915, "value": 0.9, "lift": 0.5,
                                               "rank": 1}],
                                    "highlight": 1800.0, "skips": []})
    assert [s["id"] for s in spots] == ["H1", "H2"] and spots[1]["kind"] == "highlight"
    near = moments.build_hotspots({"peaks": [{"start": 900, "end": 930, "center": 915, "value": 0.9, "lift": 0.5,
                                              "rank": 1}], "highlight": 950.0, "skips": []})
    assert len(near) == 1


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #

def make_job(tmp_path, **fields):
    job_dir = tmp_path / "jobs" / fields.get("id", "20261030-100000-aaaaaa")
    job_dir.mkdir(parents=True)
    data = {"id": job_dir.name, "source": "youtube", "title": "https://youtu.be/abcdefghijk",
            "youtubeUrl": "https://youtu.be/abcdefghijk", "status": "queued", "clips": [], **fields}
    (job_dir / "job.json").write_text(json.dumps(data), encoding="utf-8")
    return job_dir


def test_fetch_replay_stores_record_and_title(tmp_path, monkeypatch):
    info = {"id": "abcdefghijk", "title": "A great talk", "channel": "Chan", "duration": DURATION,
            "heatmap": [{"start_time": s, "end_time": e, "value": v} for s, e, v in heatmap_with({50: 0.9})]}
    seen = {}

    def fetch(url, browser, **kwargs):
        seen.update(kwargs)
        return info

    monkeypatch.setattr(replay, "fetch_youtube", fetch)
    monkeypatch.setattr(replay, "fetch_sponsorblock", lambda vid: [])
    monkeypatch.setattr(settings, "MOST_REPLAYED", True)
    monkeypatch.setattr(settings, "SPONSORBLOCK", True)
    job = jobstore.Job(make_job(tmp_path))
    timings = {}
    youtube.fetch_replay(job, timings)

    saved = json.loads((job.dir / "job.json").read_text())
    assert seen == {"cookies_file": None, "ipv4": settings.YOUTUBE_IPV4}
    assert saved["title"] == "A great talk" and saved["replay"]["available"]
    assert saved["replay"]["sponsor"] == {"segments": [], "highlight": None} and "replay" in timings
    context = pipeline.gemini_context(job)
    assert context["replay"]["peaks"][0]["center"] == 1515.0 and context["replay"]["weight"] == settings.REPLAY_WEIGHT


def test_fetch_replay_skips_uploads_and_can_be_switched_off(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not be called")

    monkeypatch.setattr(replay, "fetch_signals", boom)
    upload = jobstore.Job(make_job(tmp_path, source="upload", youtubeUrl=None))
    youtube.fetch_replay(upload)
    monkeypatch.setattr(settings, "MOST_REPLAYED", False)
    monkeypatch.setattr(settings, "SPONSORBLOCK", False)
    youtube.fetch_replay(jobstore.Job(make_job(tmp_path, id="20261030-100000-bbbbbb")))
    assert "replay" not in upload.data


def test_clip_details_carry_audience_and_peak():
    details = pipeline.clip_details(["clip_0.mp4"], [{"rank": 1, "score": 80, "gemini": 70, "audience": 95.0,
                                                    "peak": {"id": "H1", "rank": 1}, "start": 1, "end": 40}])
    assert details[0]["audience"] == 95.0 and details[0]["peak"]["id"] == "H1" and details[0]["gemini"] == 70


def test_backfill_replay_reports_hits_and_keeps_updated_at(tmp_path):
    heatmap = heatmap_with({50: 0.9})
    clips = [{"name": "clip_0.mp4", "start": 1500, "end": 1545}, {"name": "clip_1.mp4", "start": 300, "end": 340}]
    make_job(tmp_path, clipDetails=clips, updatedAt="2026-10-25T00:00:00Z")
    make_job(tmp_path, id="20261030-100000-bbbbbb", source="upload", youtubeUrl=None)
    make_job(tmp_path, id="20261030-100000-cccccc", updatedAt="2026-10-25T00:00:00Z")
    lines, sleeps = [], []

    def fetch(url, **kwargs):
        available = url.endswith("abcdefghijk") and not fetch.calls
        fetch.calls += 1
        record = {"available": available, "heatmap": heatmap if available else [], "errors": [],
                  "peaks": replay.find_peaks(heatmap, duration=DURATION) if available else [],
                  "sponsor": {"segments": [], "highlight": None}}
        return record, None
    fetch.calls = 0

    runner_log = maintenance.log
    try:
        maintenance.log = lines.append
        maintenance.backfill_replay(tmp_path, fetch=fetch, sleep=sleeps.append)
    finally:
        maintenance.log = runner_log
    saved = json.loads((tmp_path / "jobs" / "20261030-100000-aaaaaa" / "job.json").read_text())
    assert saved["replay"]["available"] and saved["updatedAt"] == "2026-10-25T00:00:00Z"
    assert fetch.calls == 2 and sleeps == [3.0]
    assert "1 of 2 YouTube video(s) have a Most replayed graph" in lines[-1]
    assert "1 of 2 of their clips sat on a hotspot" in lines[-1]

    # Already fetched: no second network call without --force.
    maintenance.log = lines.append
    try:
        maintenance.backfill_replay(tmp_path, fetch=fetch, sleep=sleeps.append)
    finally:
        maintenance.log = runner_log
    assert fetch.calls == 2


@pytest.mark.parametrize("prompt", ["viral_moments.md"])
def test_prompt_explains_hotspots(prompt):
    text = (moments.PROMPTS_DIR / prompt).read_text(encoding="utf-8")
    assert "Replay hotspots" in text and "`hotspot`" in text and "do not raise a\nscore" in text


def test_graph_from_the_download_when_the_lookup_was_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MOST_REPLAYED", True)
    refused = {"available": False, "heatmap": [], "chapters": [], "peaks": [], "errors": ["YouTube: not a bot"],
               "sponsor": {"segments": [], "highlight": None}}
    job = jobstore.Job(make_job(tmp_path, replay=refused))
    info = {"duration": DURATION, "view_count": 5000,
            "heatmap": [{"start_time": s, "end_time": e, "value": v} for s, e, v in heatmap_with({50: 0.9})]}
    youtube.graph_from_download(job, {"duration": DURATION})       # no graph there either: unchanged
    assert job.data["replay"] is refused or job.data["replay"] == refused
    youtube.graph_from_download(job, info)
    saved = json.loads((job.dir / "job.json").read_text())["replay"]
    assert saved["available"] and saved["peaks"][0]["center"] == 1515.0 and saved["errors"] == []
    assert saved["sponsor"] == refused["sponsor"]


def test_graph_from_the_download_leaves_a_good_lookup_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MOST_REPLAYED", True)
    good = {"available": True, "heatmap": [[0, 1, 0.5]], "peaks": [], "errors": []}
    job = jobstore.Job(make_job(tmp_path, replay=good))
    youtube.graph_from_download(job, {"heatmap": [{"start_time": 0, "end_time": 1, "value": 1}] * 20})
    assert job.data["replay"] == good
