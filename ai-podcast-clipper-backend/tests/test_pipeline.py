"""Offline tests for moment ranking, audio chunking, Parakeet word timing and the runner.

    cd ai-podcast-clipper-backend
    pip install pytest numpy pydantic
    python -m pytest tests -q
"""

import json
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import moments  # noqa: E402
import audio_utils  # noqa: E402
import parakeet_transcriber  # noqa: E402
import runner  # noqa: E402


def make_words(n_sentences=400, sentence_s=6.0, words_per_sentence=12):
    words, t = [], 0.0
    step = sentence_s / words_per_sentence
    for s in range(n_sentences):
        for i in range(words_per_sentence):
            text = f"w{s}_{i}" + ("." if i == words_per_sentence - 1 else "")
            words.append({"start": round(t, 3), "end": round(t + step * 0.9, 3), "word": text})
            t += step
        t += 0.3
    return words


# --------------------------------------------------------------------------- #
# Moment ranking
# --------------------------------------------------------------------------- #

def test_build_lines_splits_on_sentences():
    lines = moments.build_lines(make_words(3))
    assert len(lines) == 3
    assert lines[0]["text"].endswith(".")
    assert lines[1]["start"] > lines[0]["end"]


def test_snap_enforces_length_and_boundaries():
    lines = moments.build_lines(make_words(50))
    s, e = moments.snap_to_lines(13.0, 120.0, lines)          # far too long
    assert e - s <= moments.MAX_CLIP_S
    assert s in {l["start"] for l in lines} and e in {l["end"] for l in lines}
    s, e = moments.snap_to_lines(13.0, 20.0, lines)           # too short -> extended
    assert e - s >= moments.MIN_CLIP_S


def fake_generate_factory(windows_seen):
    """Fake Gemini: proposes candidates in chronological order with scores that
    peak late in the video - the old code would have taken the first five."""
    def generate(client, model, system, user, schema, **_):
        if "Candidates" in user.splitlines()[0]:          # re-rank call: keep scores
            ids = [int(line.split(":")[0].split()[1]) for line in user.splitlines() if line.startswith("id ")]
            return [{"id": i, "hook": 5, "standalone": 5, "payoff": 5, "emotion": 5, "value": 5,
                     "shareability": 5, "reason": "flat"} for i in ids]
        stamps = [line for line in user.splitlines() if line.startswith("[")]
        first = float(stamps[0][1:].split("-")[0])
        windows_seen.append(first)
        out = []
        for k in range(0, len(stamps) - 10, 12):
            start = float(stamps[k][1:].split("-")[0])
            end = float(stamps[k + 7][1:].split("]")[0].split("-")[1])
            quality = min(10, int(start // 200))           # later = better
            out.append({"start": start, "end": end, "title": f"t{start:.0f}", "hook": "h",
                        "hook_score": quality, "standalone": quality, "payoff": quality,
                        "emotion": quality, "value": quality, "shareability": quality, "reason": "r"})
        return out
    return generate


def test_select_moments_returns_best_not_first():
    words = make_words(300)   # ~32 minutes -> single window
    seen = []
    chosen = moments.select_moments(words, 5, client=None, model="fake", generate=fake_generate_factory(seen),
                                    log=lambda *_: None)
    assert len(chosen) == 5
    assert [c["rank"] for c in chosen] == [1, 2, 3, 4, 5]
    assert all(c["source"] == "gemini" for c in chosen)
    scores = [c["score"] for c in chosen]
    assert scores == sorted(scores, reverse=True)
    # the best clips come from the end of the video, not the start
    assert min(c["start"] for c in chosen) > 1000
    for a in chosen:
        assert moments.MIN_CLIP_TOLERANCE_S <= a["end"] - a["start"] <= moments.MAX_CLIP_S
        for b in chosen:
            assert a is b or not moments.overlaps(a, b)


def test_long_video_uses_windows_and_rerank():
    words = make_words(900)   # ~95 minutes -> several windows + re-rank
    seen = []
    chosen = moments.select_moments(words, 5, client=None, model="fake", generate=fake_generate_factory(seen),
                                    log=lambda *_: None)
    assert len(seen) >= 3
    assert len(chosen) == 5


def test_weighted_score_bounds():
    assert moments.weighted_score({k: 10 for k in moments.SCORE_KEYS}) == 100.0
    assert moments.weighted_score({k: -3 for k in moments.SCORE_KEYS}) == 0.0
    assert abs(sum(moments.SCORE_WEIGHTS.values()) - 1.0) < 1e-9


def test_parse_json_list_handles_fences():
    assert moments.parse_json_list('```json\n[{"start": 1, "end": 2}]\n```') == [{"start": 1, "end": 2}]
    assert moments.parse_json_list("not json") == []


def test_prompt_placeholders_filled():
    text = moments._load_prompt("viral_moments.md", n_candidates=10, min_s=30, max_s=60)
    assert "{" not in text.replace("{n_candidates}", "")
    assert "exactly 10 clip candidates" in text and "60 seconds" in text and "754.3" in text


# --------------------------------------------------------------------------- #
# Audio chunking and transcript helpers
# --------------------------------------------------------------------------- #

def speech_with_pauses(seconds=200, pause_every=7.0):
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(int(seconds * 16000)) * 3000).astype(np.int16)
    for t in np.arange(pause_every, seconds, pause_every):
        a = int(t * 16000)
        audio[a:a + 8000] = 0     # 0.5 s pause
    return audio


def test_split_on_pauses_limits_and_coverage():
    audio = speech_with_pauses()
    spans = audio_utils.split_on_pauses(audio)
    assert spans[0][0] == 0 and spans[-1][1] == len(audio)
    assert all(b[0] == a[1] for a, b in zip(spans, spans[1:]))
    assert all((e - s) / 16000 <= audio_utils.CHUNK_MAX_S + 1e-6 for s, e in spans)
    for _, end in spans[:-1]:            # cuts land inside the silent stretches
        assert np.abs(audio[end - 400:end + 400]).max() == 0


def test_split_on_pauses_short_and_empty():
    assert audio_utils.split_on_pauses(np.zeros(0, dtype=np.int16)) == []
    assert audio_utils.split_on_pauses(np.zeros(16000 * 5, dtype=np.int16)) == [(0, 16000 * 5)]


def test_merge_words_monotonic():
    merged = audio_utils.merge_words([{"start": 1.5, "end": 1.6, "word": "b"},
                                      {"start": 1.0, "end": 2.0, "word": "a"}])
    assert [w["word"] for w in merged] == ["a", "b"]
    assert merged[0]["end"] <= merged[1]["start"] and merged[1]["end"] >= merged[1]["start"]


def test_parakeet_tokens_to_words():
    tokens = [" Hel", "lo", " world", ".", " Next"]
    stamps = [0.0, 0.16, 0.48, 0.80, 2.40]
    words = parakeet_transcriber.tokens_to_words(tokens, stamps, offset_s=10.0, chunk_end_s=13.0)
    assert [w["word"] for w in words] == ["Hello", "world.", "Next"]
    assert words[0]["start"] == 10.0 and words[0]["end"] == 10.46          # capped at last token + 0.3
    assert words[1]["end"] == 11.1                                         # capped, not stretched to 12.4
    assert words[2]["end"] <= 13.0
    assert parakeet_transcriber.tokens_to_words([], [], 0.0, 1.0) == []


# --------------------------------------------------------------------------- #
# .env settings
# --------------------------------------------------------------------------- #

def test_env_file_parsing(tmp_path, monkeypatch):
    import settings

    env = tmp_path / ".env"
    env.write_text('# comment\nWHISTLE_WORKER_CPU=4\nRENDER_GPU="L4"\nWHISTLE_MAX_WORKERS=8 # inline\n'
                   "export WHISTLE_SECONDS_PER_WORKER='480'\nALREADY_SET=from_file\n", encoding="utf-8")
    for key in ("WHISTLE_WORKER_CPU", "RENDER_GPU", "WHISTLE_MAX_WORKERS", "WHISTLE_SECONDS_PER_WORKER"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ALREADY_SET", "from_shell")
    loaded = settings.load_env_file(env)
    assert loaded == {"WHISTLE_WORKER_CPU": "4", "RENDER_GPU": "L4", "WHISTLE_MAX_WORKERS": "8",
                      "WHISTLE_SECONDS_PER_WORKER": "480"}
    import os
    assert os.environ["ALREADY_SET"] == "from_shell"      # shell wins
    assert settings.get_int("WHISTLE_MAX_WORKERS", 16) == 8
    assert settings.load_env_file(tmp_path / "missing.env") == {}


# --------------------------------------------------------------------------- #
# Never zero clips
# --------------------------------------------------------------------------- #

def test_empty_gemini_answer_is_filled_from_transcript():
    words = make_words(300)
    report = {}
    chosen = moments.select_moments(words, 5, None, "fake", generate=lambda *a, **k: [],
                                    log=lambda *_: None, report=report, min_clips=3)
    assert len(chosen) == 3
    assert all(c["source"] == "fallback" for c in chosen)
    assert report["fallback"] == 3 and report["valid"] == 0
    for a in chosen:
        assert moments.MIN_CLIP_TOLERANCE_S <= a["end"] - a["start"] <= moments.MAX_CLIP_S
        for b in chosen:
            assert a is b or not moments.overlaps(a, b)


def test_gemini_error_is_reported_and_filled():
    def broken(*a, **k):
        raise RuntimeError("400 INVALID_ARGUMENT: model not found")
    report = {}
    chosen = moments.select_moments(make_words(300), 5, None, "fake", generate=broken,
                                    log=lambda *_: None, report=report, min_clips=5)
    assert len(chosen) == 5 and report["fallback"] == 5


def test_partial_gemini_answer_keeps_gemini_first():
    words = make_words(300)
    lines = moments.build_lines(words)

    def one(*a, **k):
        return [{"start": lines[50]["start"], "end": lines[57]["end"], "title": "t", "hook": "h",
                 "hook_score": 2, "standalone": 2, "payoff": 2, "emotion": 2, "value": 2,
                 "shareability": 2, "reason": "r"}]
    chosen = moments.select_moments(words, 5, None, "fake", generate=one, log=lambda *_: None, min_clips=3)
    assert len(chosen) == 3
    assert chosen[0]["source"] == "gemini" and chosen[0]["rank"] == 1   # even with a low score
    assert [c["source"] for c in chosen[1:]] == ["fallback", "fallback"]


class _Busy(Exception):
    code = 503


class _Bad(Exception):
    code = 400


def _chain(monkeypatch, models=("primary", "backup")):
    monkeypatch.setattr(moments, "GEMINI_MODELS", list(models))
    monkeypatch.setattr(moments, "GEMINI_BUSY_RETRIES", 1)
    monkeypatch.setattr(moments, "GEMINI_THINKING_LEVEL", "low")


def _run(fake, monkeypatch, report=None):
    monkeypatch.setattr(moments, "_call_gemini", fake)
    report = {} if report is None else report
    return moments._generate_json(None, "primary", "s", "u", object(), report=report,
                                  log=lambda *_: None, sleep=lambda *_: None), report


def test_busy_model_gets_one_retry_then_next_model(monkeypatch):
    _chain(monkeypatch)
    calls = []

    def fake(client, model, system, user, schema, timeout_s=None, thinking=None, deadline=None):
        calls.append((model, schema is not None))
        if model == "primary":
            raise _Busy("503 UNAVAILABLE high demand")
        return [{"start": 1, "end": 40}]

    out, report = _run(fake, monkeypatch)
    assert out == [{"start": 1, "end": 40}]
    assert calls == [("primary", True), ("primary", True), ("backup", True)]   # no plain-JSON on busy
    assert report["modelUsed"] == "backup" and len(report["errors"]) == 2


def test_format_problem_retries_as_plain_json_on_same_model(monkeypatch):
    _chain(monkeypatch)
    calls = []

    def fake(client, model, system, user, schema, timeout_s=None, thinking=None, deadline=None):
        calls.append((model, schema is not None))
        if schema is not None:
            raise moments.ResponseFormatError("not a list")
        return [{"start": 1, "end": 40}]

    out, _ = _run(fake, monkeypatch)
    assert out and calls == [("primary", True), ("primary", False)]


def test_empty_answer_retries_plain_then_moves_on(monkeypatch):
    _chain(monkeypatch)
    calls = []

    def fake(client, model, system, user, schema, timeout_s=None, thinking=None, deadline=None):
        calls.append((model, schema is not None))
        return [] if model == "primary" else [{"start": 1, "end": 40}]

    out, _ = _run(fake, monkeypatch)
    assert out and calls == [("primary", True), ("primary", False), ("backup", True)]


def test_unsupported_thinking_is_dropped(monkeypatch):
    _chain(monkeypatch)
    seen = []

    def fake(client, model, system, user, schema, timeout_s=None, thinking=None, deadline=None):
        seen.append(thinking)
        if thinking:
            raise _Bad("400 INVALID_ARGUMENT: thinking_level is not supported for this model")
        return [{"start": 1, "end": 40}]

    out, _ = _run(fake, monkeypatch)
    assert out and seen == ["low", None]


def test_unknown_model_skips_straight_to_next(monkeypatch):
    _chain(monkeypatch)
    calls = []

    class _NotFound(Exception):
        code = 404

    def fake(client, model, system, user, schema, timeout_s=None, thinking=None, deadline=None):
        calls.append(model)
        if model == "primary":
            raise _NotFound("404 models/primary is not found")
        return [{"start": 1, "end": 40}]

    out, _ = _run(fake, monkeypatch)
    assert out and calls == ["primary", "backup"]


def test_time_limit_triggers_transcript_fallback(monkeypatch):
    _chain(monkeypatch)
    monkeypatch.setattr(moments, "RANKING_TIME_LIMIT_S", 0.001)   # already expired

    def fake(*a, **k):
        raise AssertionError("must not call Gemini after the time limit")

    monkeypatch.setattr(moments, "_call_gemini", fake)
    report = {}
    chosen = moments.select_moments(make_words(300), 5, None, "primary", log=lambda *_: None,
                                    report=report, min_clips=3)
    assert report["timedOut"] is True and len(chosen) == 3
    assert all(c["source"] == "fallback" for c in chosen)
    assert "_deadline" not in report


def test_call_gemini_streams_and_parses(monkeypatch):
    class _Chunk:
        def __init__(self, text):
            self.text = text

    class _Models:
        def __init__(self):
            self.config = None

        def generate_content_stream(self, model, contents, config):
            self.config = config
            yield from (_Chunk('[{"start": 1.0, '), _Chunk('"end": 40.5}'), _Chunk("]"))

    class _Client:
        models = _Models()

    monkeypatch.setattr(moments, "GEMINI_TEMPERATURE", None)
    out = moments._call_gemini(_Client(), "m", "sys", "user", None, timeout_s=30, thinking="low")
    assert out == [{"start": 1.0, "end": 40.5}]
    cfg = _Client.models.config
    assert cfg.thinking_config.thinking_level.name == "LOW"
    assert cfg.automatic_function_calling.disable is True and cfg.temperature is None


def test_call_gemini_rejects_non_json(monkeypatch):
    class _Models:
        def generate_content_stream(self, model, contents, config):
            yield type("C", (), {"text": "Sorry, I cannot help with that."})()

    class _Client:
        models = _Models()

    with pytest.raises(moments.ResponseFormatError):
        moments._call_gemini(_Client(), "m", "s", "u", None)


def test_model_chain_dedupes(monkeypatch):
    monkeypatch.setattr(moments, "GEMINI_MODELS", ["a", "b", "a", "c"])
    assert moments.model_chain("b") == ["b", "a", "c"]
    assert moments.model_chain(None) == ["a", "b", "c"]


def test_format_lines_uses_tenths():
    assert moments.format_lines([{"start": 1.234, "end": 5.678, "text": "Hi."}]) == "[1.2-5.7] Hi."


def test_plain_json_object_wrapper_is_parsed():
    assert moments.parse_json_list('{"candidates": [{"start": 1, "end": 2}]}') == [{"start": 1, "end": 2}]


def test_prompt_never_allows_empty_list():
    text = moments._load_prompt("viral_moments.md", n_candidates=10, min_s=30, max_s=60)
    assert "exactly 10 clip candidates" in text
    assert "return an empty list" not in text.lower()


def test_ranking_message_explains_fallback():
    msg = runner.ranking_message(["clip_0.mp4"] * 3, [], {"fallback": 2, "valid": 1, "errors": ["x: boom"]})
    assert msg.startswith("3 clip(s) ready") and "2 picked from the transcript" in msg and "boom" in msg
    assert "Gemini found no usable moments" in runner.ranking_message([], [], {})


# --------------------------------------------------------------------------- #
# Re-rank & re-render
# --------------------------------------------------------------------------- #

def _rerank_setup(tmp_path, monkeypatch, new_clips):
    import json as _json
    import types as _types

    job_dir = tmp_path / "20261024-000000-abcdef"
    (job_dir / "clips").mkdir(parents=True)
    (job_dir / "clips" / "clip_0.mp4").write_bytes(b"old")
    (job_dir / "input.mp4").write_bytes(b"video")
    (job_dir / "transcript.json").write_text("[]")
    (job_dir / "moments.json").write_text("[]")
    (job_dir / "job.json").write_text(_json.dumps({
        "id": job_dir.name, "source": "youtube", "title": "t", "status": "done", "clips": ["clip_0.mp4"],
        "clipDetails": [{"name": "clip_0.mp4", "rank": 1}], "engine": {"transcriber": "parakeet"}}))

    class _Volume:
        def read_file_into_fileobj(self, path, f):
            f.write(b"new-" + path.encode())

        def remove_file(self, *a, **k):
            pass

    fake_modal = _types.SimpleNamespace(Volume=_types.SimpleNamespace(from_name=lambda *a, **k: _Volume()))
    monkeypatch.setitem(sys.modules, "modal", fake_modal)
    monkeypatch.setattr(runner, "upload_input", lambda *a, **k: None)
    monkeypatch.setattr(runner, "upload_transcript", lambda *a, **k: None)
    calls = {}

    def fake_gpu(job, max_clips, mode, timings):
        calls["mode"] = mode
        moments_out = [{"start": 10.0 * i, "end": 10.0 * i + 40, "rank": i + 1, "score": 80 - i, "title": f"m{i}"}
                       for i in range(len(new_clips))]
        return {"clips": new_clips, "moments": moments_out, "errors": [],
                "ranking": {"valid": len(new_clips), "fallback": 0, "errors": []}}

    monkeypatch.setattr(runner, "run_gpu", fake_gpu)
    return job_dir, calls


def test_rerank_replaces_clips_and_reuses_transcript(tmp_path, monkeypatch):
    import json as _json

    job_dir, calls = _rerank_setup(tmp_path, monkeypatch, ["clip_0.mp4", "clip_1.mp4"])
    assert runner.run_rerank(job_dir, 5, keep_remote=False) == 0
    job = _json.loads((job_dir / "job.json").read_text())
    assert calls["mode"] == "reuse"                         # Parakeet transcript, no re-transcription
    assert job["status"] == "done" and job["clips"] == ["clip_0.mp4", "clip_1.mp4"] and job["reranks"] == 1
    assert job["message"].startswith("Re-ranked: 2 clip(s) ready")
    assert (job_dir / "clips" / "clip_0.mp4").read_bytes().startswith(b"new-")
    assert not (job_dir / "clips_new").exists() and not (job_dir / "clips_previous").exists()
    assert (job_dir / "moments.previous.json").exists()


def test_rerank_keeps_old_clips_when_nothing_new(tmp_path, monkeypatch):
    import json as _json

    job_dir, _ = _rerank_setup(tmp_path, monkeypatch, [])
    assert runner.run_rerank(job_dir, 5, keep_remote=False) == 0
    job = _json.loads((job_dir / "job.json").read_text())
    assert job["clips"] == ["clip_0.mp4"] and job["clipDetails"] == [{"name": "clip_0.mp4", "rank": 1}]
    assert (job_dir / "clips" / "clip_0.mp4").read_bytes() == b"old"
    assert "kept the previous clips" in job["message"]


def test_rerank_of_legacy_whistle_job_refreshes_subtitles(tmp_path, monkeypatch):
    import json as _json

    job_dir, calls = _rerank_setup(tmp_path, monkeypatch, ["clip_0.mp4"])
    data = _json.loads((job_dir / "job.json").read_text())
    data["engine"] = {"transcriber": "whistle"}
    (job_dir / "job.json").write_text(_json.dumps(data))
    runner.run_rerank(job_dir, 5, keep_remote=False)
    assert calls["mode"] == "uploaded"


def test_run_gpu_mirrors_live_progress(tmp_path, monkeypatch):
    import json as _json
    import types as _types

    job_dir = tmp_path / "20261024-000000-aaaaaa"
    job_dir.mkdir()
    (job_dir / "job.json").write_text(_json.dumps({"id": job_dir.name, "source": "upload", "title": "t",
                                                   "status": "uploading", "clips": []}))
    job = runner.Job(job_dir)
    seen = []
    states = [{"stage": "transcribing"}, {"stage": "ranking"},
              {"stage": "rendering", "done": 2, "total": 5, "etaSeconds": 90}]

    class _Call:
        polls = 0

        def get(self, timeout=None):
            _Call.polls += 1
            if _Call.polls <= len(states):
                raise TimeoutError()
            return {"clips": [], "timings": {"render": 1.0}, "gpu": "A10"}

    class _Dict:
        def get(self, key):
            state = states[min(_Call.polls - 1, len(states) - 1)]
            seen.append(state["stage"])
            return state

        def pop(self, key):
            pass

    class _Cls:
        def __call__(self):
            return _types.SimpleNamespace(process_job=_types.SimpleNamespace(spawn=lambda *a: _Call()))

    fake_modal = _types.SimpleNamespace(
        Cls=_types.SimpleNamespace(from_name=lambda *a: _Cls()),
        Dict=_types.SimpleNamespace(from_name=lambda *a, **k: _Dict()),
        exception=_types.SimpleNamespace(TimeoutError=type("ModalTimeout", (Exception,), {})))
    monkeypatch.setitem(sys.modules, "modal", fake_modal)
    updates = []
    original = job.update
    monkeypatch.setattr(job, "update", lambda **f: (updates.append(f.get("progress")), original(**f)))

    timings = {}
    result = runner.run_gpu(job, 5, "parakeet", timings)
    assert result["gpu"] == "A10" and timings["gpu_render"] == 1.0
    stages = [u["stage"] for u in updates if u]
    assert stages[:1] == ["transcribing"] and "ranking" in stages and "rendering" in stages
    final = _json.loads((job_dir / "job.json").read_text())["progress"]
    assert final["stage"] == "rendering" and final["done"] == 2 and final["total"] == 5



def test_save_thumbnail_makes_16_9_cover(tmp_path):
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    import subprocess

    job_dir = tmp_path / "jobs" / "20261024-120000-abcdef"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"id": job_dir.name, "source": "upload",
                                                  "title": "talk.mp4"}), encoding="utf-8")
    # A vertical test video: the cover must still come out 16:9.
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=size=360x640:duration=3:rate=10", "-pix_fmt", "yuv420p",
                    str(job_dir / "input.mp4")], check=True)

    assert runner.backfill_thumbnails(tmp_path) == 0
    cover = job_dir / "thumbnail.jpg"
    assert cover.exists()
    width, height = None, None
    data = cover.read_bytes()
    for i in range(len(data) - 9):  # read the size from the JPEG SOF0/SOF2 marker
        if data[i] == 0xFF and data[i + 1] in (0xC0, 0xC2):
            height, width = int.from_bytes(data[i + 5:i + 7], "big"), int.from_bytes(data[i + 7:i + 9], "big")
            break
    assert (width, height) == (320, 180)


def test_backfill_makes_clip_posters(tmp_path):
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    import subprocess

    job_dir = tmp_path / "jobs" / "20261024-130000-abcdef"
    (job_dir / "clips").mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"id": job_dir.name, "source": "youtube",
                                                  "title": "x"}), encoding="utf-8")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc=size=1080x1920:duration=2:rate=10", "-pix_fmt", "yuv420p",
                    str(job_dir / "clips" / "clip_0.mp4")], check=True)

    runner.backfill_thumbnails(tmp_path)
    assert (job_dir / "clips" / "clip_0.jpg").exists()
    assert not (job_dir / "thumbnail.jpg").exists()  # YouTube covers come from the web app


def test_hide_child_windows_is_a_no_op_off_windows(monkeypatch):
    import subprocess

    original = subprocess.Popen.__init__
    monkeypatch.setattr(runner.os, "name", "posix")
    runner.hide_child_windows()
    assert subprocess.Popen.__init__ is original


def test_hide_child_windows_adds_create_no_window(monkeypatch):
    import subprocess

    seen = []
    monkeypatch.setattr(subprocess.Popen, "__init__", lambda self, *a, **kw: seen.append(kw))
    monkeypatch.setattr(runner.os, "name", "nt")
    runner.hide_child_windows()
    subprocess.Popen.__init__(object(), ["ffmpeg"])
    subprocess.Popen.__init__(object(), ["x"], creationflags=0x00000010)  # CREATE_NEW_CONSOLE kept
    assert seen[0]["creationflags"] == 0x08000000
    assert seen[1]["creationflags"] == 0x00000010
