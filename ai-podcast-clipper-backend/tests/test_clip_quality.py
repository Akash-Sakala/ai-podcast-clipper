"""Clip selection: whole thoughts up to 2 minutes, the payoff check, one judge call per run,
score profiles and the rank_moments.py --compare report. All offline, with fake Gemini."""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import moments  # noqa: E402
import rank_moments  # noqa: E402


def quiet(*_):
    pass


def words_from(sentences, word_s=0.5, gap_s=0.3):
    """Timed words, one sentence after another (12 words = 6 s)."""
    words, t = [], 0.0
    for sentence in sentences:
        for token in sentence.split():
            words.append({"start": round(t, 3), "end": round(t + word_s * 0.9, 3), "word": token})
            t += word_s
        t += gap_s
    return words


def filler(i):
    return f"Filler{i} talk{i} goes{i} on{i} about{i} nothing{i} much{i} here{i} and{i} there{i} again{i} today{i}."


def transcript(n=60, special=None):
    """n sentences of filler; special = {index: sentence} replaces some of them."""
    special = special or {}
    return [special.get(i, filler(i)) for i in range(n)]


def make_words_long(n_sentences, sentence_s=6.0, words_per_sentence=12):
    words, t = [], 0.0
    step = sentence_s / words_per_sentence
    for s in range(n_sentences):
        for i in range(words_per_sentence):
            words.append({"start": round(t, 3), "end": round(t + step * 0.9, 3),
                          "word": f"w{s}_{i}" + ("." if i == words_per_sentence - 1 else "")})
            t += step
        t += 0.3
    return words


def proposal(lines, i, j, level=7, **extra):
    return {"start": lines[i]["start"], "end": lines[j]["end"], "title": f"clip {i}", "hook": "h",
            "reason": "r", "hotspot": "", "arc": "question_answer", "hook_score": level, "standalone": level,
            "payoff_score": level, "emotion": level, "value": level, "shareability": level, **extra}


def ids_in(user):
    return [int(line.split(":")[0].split()[1]) for line in user.splitlines() if line.startswith("id ")]


def verdict(i, level=7, complete=True, issue="none", **extra):
    return {"id": i, "hook": level, "standalone": level, "payoff": level, "emotion": level, "value": level,
            "shareability": level, "complete": complete, "issue": issue, "reason": "judged", **extra}


def run(words, generate, max_clips=2, min_clips=1, context=None):
    report = {}
    chosen = moments.select_moments(words, max_clips, None, "fake", context, generate=generate, log=quiet,
                                    report=report, min_clips=min_clips)
    return chosen, report


# --------------------------------------------------------------------------- #
# Cutting
# --------------------------------------------------------------------------- #

def test_proposal_longer_than_cap_is_not_truncated_and_is_dropped_without_a_fix():
    words = words_from(transcript(80))
    lines = moments.build_lines(words)
    cut = moments.cut_proposal(lines[10]["start"], lines[40]["end"], lines)      # about 190 s
    assert cut["tooLong"] and cut["last"] == 40 and cut["first"] == 10        # not shortened in code

    def generate(client, model, system, user, schema, report=None, log=print):
        if user.startswith("Candidates"):
            assert "too long" in user
            return []                                                         # the judge offers no fix
        return [proposal(lines, 10, 40, level=10), proposal(lines, 50, 57, level=4)]

    chosen, report = run(words, generate)
    assert [c["title"] for c in chosen] == ["clip 50"]
    assert report["tooLong"] == 1 and report["judge"]["droppedTooLong"] == 1 and report["judge"]["ran"]
    assert all(c["end"] - c["start"] <= moments.MAX_CLIP_S + moments.CLIP_GRACE_S + 1 for c in chosen)


def test_too_long_proposal_survives_with_a_valid_judge_correction():
    words = words_from(transcript(80))
    lines = moments.build_lines(words)

    def generate(client, model, system, user, schema, report=None, log=print):
        if user.startswith("Candidates"):
            out = []
            for i in ids_in(user):
                header = next(l for l in user.splitlines() if l.startswith(f"id {i}:"))
                if "too long" in header:   # start later: lines 25..40 is about 100 s
                    out.append(verdict(i, level=9, start=round(lines[25]["start"], 1)))
                else:
                    out.append(verdict(i, level=4))
            return out
        return [proposal(lines, 10, 40, level=10), proposal(lines, 50, 57, level=4)]

    chosen, report = run(words, generate)
    assert chosen[0]["title"] == "clip 10" and chosen[0]["adjusted"] == "judge: starts later"
    assert abs(chosen[0]["start"] - lines[25]["start"]) < 0.5 and chosen[0]["duration"] <= 125.5
    assert report["judge"]["droppedTooLong"] == 0 and report["judge"]["adjusted"] == 1


def test_proposal_ends_on_its_own_sentence_end():
    long_sentence = " ".join(f"long{k}" for k in range(39)) + " end."            # 40 words -> two lines
    words = words_from(transcript(30, {12: long_sentence}))
    lines = moments.build_lines(words)
    mid = next(i for i, l in enumerate(lines) if not l["ends_sentence"])
    cut = moments.cut_proposal(lines[5]["start"], lines[mid]["end"], lines)
    assert cut["last"] == mid + 1 and not cut["tooLong"]                       # finished, not cut earlier
    assert lines[mid + 1]["end"] <= cut["end"] <= lines[mid + 1]["end"] + moments.TAIL_S + 1e-6
    # A proposal that ends on a sentence end keeps exactly that end.
    cut = moments.cut_proposal(lines[5]["start"], lines[10]["end"], lines)
    assert cut["last"] == 10


ANSWER = "The answer is that compound interest quietly rewards patient investors over decades."
QUESTION = "So why do most people never get rich from investing in the stock market?"


def test_payoff_inside_after_the_end_and_not_found():
    words = words_from(transcript(60, {20: QUESTION, 27: ANSWER}))
    lines = moments.build_lines(words)
    # Inside: the answer is in the clip (quote differs a little in case and punctuation).
    inside = moments.normalize_candidates(
        [proposal(lines, 20, 28, payoff="the answer is that compound interest quietly rewards patient investors")],
        lines)[0]
    assert inside["payoffInside"] is True and inside["adjusted"] is None
    assert abs(inside["payoffAt"] - lines[27]["start"]) < 0.01
    # After the end: extended to the end of the payoff sentence.
    ext = moments.normalize_candidates([proposal(lines, 20, 25, payoff=ANSWER)], lines)[0]
    assert ext["payoffInside"] is True and ext["adjusted"] == "extended to payoff"
    assert lines[27]["end"] <= ext["end"] <= lines[27]["end"] + moments.TAIL_S + 1e-6
    # After the end, too far to fit: kept as is and marked.
    far = moments.normalize_candidates([proposal(lines, 20, 24, payoff=ANSWER)], lines, max_s=36, grace_s=0)[0]
    assert far["payoffInside"] is False and far["adjusted"] is None and moments.is_incomplete(far)
    # Not found at all: unknown, not a failure.
    missing = moments.normalize_candidates([proposal(lines, 20, 28, payoff="zebras dance under purple moons")],
                                           lines)[0]
    assert missing["payoffInside"] is None and not moments.is_incomplete(missing)


def test_find_payoff_spans_two_lines():
    long_answer = " ".join(f"part{k}" for k in range(35)) + " landed."
    lines = moments.build_lines(words_from(transcript(20, {8: long_answer})))
    quote = " ".join(f"part{k}" for k in range(25, 35)) + " landed"
    i, j = moments.find_payoff(quote, lines)
    assert lines[j]["text"].endswith("landed.")


# --------------------------------------------------------------------------- #
# Judge
# --------------------------------------------------------------------------- #

def test_incomplete_clip_ranks_below_a_complete_one():
    words = words_from(transcript(80))
    lines = moments.build_lines(words)

    def generate(client, model, system, user, schema, report=None, log=print):
        if user.startswith("Candidates"):
            return [verdict(0, level=10, complete=False, issue="ends_before_payoff"), verdict(1, level=3)]
        return [proposal(lines, 10, 17, level=9), proposal(lines, 40, 47, level=5)]

    chosen, report = run(words, generate)
    assert [c["title"] for c in chosen] == ["clip 40", "clip 10"]
    assert chosen[1]["complete"] is False and chosen[1]["issue"] == "ends_before_payoff"
    assert chosen[1]["score"] > chosen[0]["score"]          # higher score, still ranked below
    assert report["judge"]["complete"] == 1 and report["judge"]["incomplete"] == 1


def _judge_one(lines, cand, judged, skips=None, hotspots=None, max_s=120.0):
    def generate(client, model, system, user, schema, report=None, log=print):
        return [dict(verdict(0), **judged)]
    out, counts = moments.judge_candidates(None, "fake", [cand], lines, 30.0, max_s, 5.0, skips or [],
                                           hotspots or [], None, generate, {}, quiet)
    return out[0], counts


def test_valid_judge_extension_is_applied_and_invalid_ones_ignored():
    lines = moments.build_lines(words_from(transcript(80)))
    cand = moments.normalize_candidates([proposal(lines, 20, 26)], lines)[0]
    # Valid: extend to the end of a listed "after" line.
    out, counts = _judge_one(lines, cand, {"end": round(lines[29]["end"], 1)})
    assert out["adjusted"] == "judge: ends later" and counts["adjusted"] == 1
    assert lines[29]["end"] <= out["end"] <= lines[29]["end"] + moments.TAIL_S + 1e-6
    # Not a listed time.
    out, counts = _judge_one(lines, cand, {"end": 12345.6})
    assert out["end"] == cand["end"] and out["adjusted"] is None and counts["ignoredCorrections"] == 1
    assert out["complete"] is True                       # the verdict still counts
    # A listed time, but beyond the cap.
    out, counts = _judge_one(lines, cand, {"end": round(lines[30]["end"], 1)}, max_s=60.0)
    assert out["end"] == cand["end"] and counts["ignoredCorrections"] == 1
    # Into a SponsorBlock range.
    skips = [{"start": lines[28]["start"], "end": lines[29]["end"], "category": "sponsor"}]
    out, counts = _judge_one(lines, cand, {"end": round(lines[29]["end"], 1)}, skips=skips)
    assert out["end"] == cand["end"] and counts["ignoredCorrections"] == 1
    # The listing shows the lines before and after with their times.
    listing, spans = moments.judge_listing([cand], lines, {})
    assert "before:" in listing and "after:" in listing and f"[{lines[31]['start']:.1f}-" in listing
    assert spans[0] == (20, 26, 17, 32)


def test_hotspot_clip_keeps_its_centre_after_a_correction():
    lines = moments.build_lines(words_from(transcript(80)))
    centre = lines[22]["start"] + 1
    spot = {"id": "H1", "center": centre, "start": centre - 5, "end": centre + 5, "rank": 1, "kind": "replay"}
    cand = moments.peak_candidate(spot, lines, 30.0, 120.0)
    assert cand["start"] <= centre <= cand["end"]
    first = moments.line_span(lines, cand["start"], cand["end"])[0]
    # Starting after the centre would lose the hotspot: ignored.
    out, counts = _judge_one(lines, cand, {"start": round(lines[23]["start"], 1)}, hotspots=[spot])
    assert out["start"] == cand["start"] and counts["ignoredCorrections"] == 1
    # Starting one line earlier keeps it: applied.
    out, counts = _judge_one(lines, cand, {"start": round(lines[first - 1]["start"], 1)}, hotspots=[spot])
    assert out["start"] < cand["start"] <= centre and counts["adjusted"] == 1


def test_judge_failure_falls_back_to_proposal_scores():
    words = words_from(transcript(80))
    lines = moments.build_lines(words)

    def generate(client, model, system, user, schema, report=None, log=print):
        if user.startswith("Candidates"):
            raise RuntimeError("503 UNAVAILABLE")
        return [proposal(lines, 10, 17, level=4), proposal(lines, 40, 47, level=8), proposal(lines, 2, 50)]

    chosen, report = run(words, generate)
    assert [c["title"] for c in chosen] == ["clip 40", "clip 10"]
    assert report["judge"]["ran"] is False and report["judge"]["failed"] is True
    assert report["judge"]["droppedTooLong"] == 1


@pytest.mark.parametrize("n_sentences", [300, 900])   # ~32 min (1 window) and ~95 min (several)
def test_exactly_windows_plus_one_calls(n_sentences):
    words = make_words_long(n_sentences)
    windows = len(moments.split_windows(moments.build_lines(words)))
    calls = []

    def generate(client, model, system, user, schema, report=None, log=print):
        calls.append(user.splitlines()[0])
        if user.startswith("Candidates"):
            return [verdict(i) for i in ids_in(user)]
        stamps = [l for l in user.splitlines() if l.startswith("[")]
        start = float(stamps[20][1:].split("-")[0])
        end = float(stamps[28][1:].split("]")[0].split("-")[1])
        return [{"start": start, "end": end, "title": "t", "hook": "h", "reason": "r", "hotspot": "",
                 "hook_score": 5, "standalone": 5, "payoff_score": 5, "emotion": 5, "value": 5,
                 "shareability": 5}]

    run(words, generate, max_clips=3)
    assert len(calls) == windows + 1 and sum(c.startswith("Candidates") for c in calls) == 1
    assert (windows == 1) == (n_sentences == 300)
    assert rank_moments.plan(words)["calls"] == windows + 1


def test_calls_are_counted_in_generate_json(monkeypatch):
    monkeypatch.setattr(moments, "GEMINI_MODELS", ["primary", "backup"])
    monkeypatch.setattr(moments, "GEMINI_BUSY_RETRIES", 1)

    class Busy(Exception):
        code = 503
    attempts = []

    def fake(client, m, *a, **k):
        attempts.append(m)
        if len(attempts) < 3:
            raise Busy("busy")
        return [{"start": 1, "end": 40}]
    monkeypatch.setattr(moments, "_call_gemini", fake)
    report = {}
    moments._generate_json(None, "primary", "s", "u", object(), report=report, log=quiet, sleep=lambda *_: None)
    assert report["calls"] == 3 and len(report["callLog"]) == 1


# --------------------------------------------------------------------------- #
# Scores and report
# --------------------------------------------------------------------------- #

def test_score_profiles(monkeypatch):
    for weights in moments.SCORE_PROFILES.values():
        assert abs(sum(weights.values()) - 1.0) < 1e-9 and tuple(weights) == moments.SCORE_KEYS
    hooky = {"hook": 10, "standalone": 8, "payoff": 4, "emotion": 9, "value": 3, "shareability": 6}
    deep = {"hook": 3, "standalone": 6, "payoff": 9, "emotion": 3, "value": 9, "shareability": 4}
    assert moments.weighted_score(hooky, "classic") > moments.weighted_score(deep, "classic")
    assert moments.weighted_score(deep, "value") > moments.weighted_score(hooky, "value")
    assert moments.weighted_score(deep) == moments.weighted_score(deep, "value")       # default profile
    monkeypatch.setattr(moments, "SCORE_PROFILE", "classic")
    assert moments.weighted_score(deep) == moments.weighted_score(deep, "classic")
    assert moments.score_profile({}) == "value" and moments.score_profile({"SCORE_PROFILE": "Classic"}) == "classic"
    assert moments.score_profile({"SCORE_PROFILE": "nonsense"}) == "value"


def test_report_and_moment_keys():
    words = words_from(transcript(80, {20: QUESTION, 27: ANSWER}))
    lines = moments.build_lines(words)

    def generate(client, model, system, user, schema, report=None, log=print):
        if user.startswith("Candidates"):
            return [verdict(i) for i in ids_in(user)]
        return [proposal(lines, 20, 28, payoff=ANSWER), proposal(lines, 50, 56)]

    chosen, report = run(words, generate)
    for key in ("model", "models", "errors", "returned", "valid", "fallback", "timedOut", "chosen", "seconds",
                "calls", "judge", "profile", "tooLong"):
        assert key in report
    assert set(report["judge"]) >= {"ran", "complete", "incomplete", "adjusted", "droppedTooLong"}
    assert report["profile"] == "value" and "_deadline" not in report
    first = chosen[0]
    for key in ("arc", "payoff", "complete", "issue", "adjusted"):
        assert key in first
    assert first["arc"] == "question_answer" and first["payoff"] == ANSWER and first["complete"] is True
    assert "tooLong" not in first and "wasTooLong" not in first
    import pipeline
    details = pipeline.clip_details(["clip_0.mp4"], chosen)
    assert details[0]["complete"] is True and details[0]["issue"] == "none"


def test_prompts_ask_for_arc_payoff_and_the_judge_fields():
    text = moments._load_prompt("viral_moments.md", n_candidates=8, min_s=30, max_s=120, grace_s=5)
    assert "120 seconds" in text and "`arc`" in text and "`payoff`" in text and "`payoff_score`" in text
    judge = moments._load_prompt("viral_moments_judge.md", min_s=30, max_s=120, grace_s=5, hard_max_s=125)
    assert "{" not in judge and "`complete`" in judge and "ends_before_payoff" in judge and "30-125" in judge


# --------------------------------------------------------------------------- #
# rank_moments.py --compare
# --------------------------------------------------------------------------- #

def make_job(tmp_path, words, old):
    job_dir = tmp_path / "data" / "jobs" / "20261115-000000-abc123"
    job_dir.mkdir(parents=True)
    (job_dir / "transcript.json").write_text(json.dumps(words), encoding="utf-8")
    (job_dir / "moments.json").write_text(json.dumps(old), encoding="utf-8")
    (job_dir / "job.json").write_text(json.dumps({"meta": {"title": "Money <talk> & co"}}), encoding="utf-8")
    return job_dir


def test_compare_dry_run_counts_calls(tmp_path, monkeypatch, capsys):
    short = make_job(tmp_path, make_words_long(300), [])
    long_dir = tmp_path / "data" / "jobs" / "long"
    long_dir.mkdir()
    (long_dir / "transcript.json").write_text(json.dumps(make_words_long(900)), encoding="utf-8")
    windows = len(moments.split_windows(moments.build_lines(make_words_long(900))))
    monkeypatch.setattr(sys, "argv", ["rank_moments.py", "--compare", str(short), str(long_dir), "--dry-run"])
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)                  # no key needed
    assert rank_moments.main() == 0
    out = capsys.readouterr().out
    assert "1 window(s), 2 Gemini call(s)" in out and f"{windows} window(s), {windows + 1} Gemini call(s)" in out
    assert f"Total: {windows + 3} Gemini call(s)" in out
    assert not (short / "eval").exists()


def test_compare_then_reuse_writes_html_without_calls(tmp_path, monkeypatch, capsys):
    words = words_from(transcript(80, {20: QUESTION, 27: ANSWER}))
    lines = moments.build_lines(words)
    old = [{"start": lines[18]["start"], "end": lines[20]["end"] + 0.3, "title": "Why <nobody> gets rich",
            "rank": 1, "score": 70, "reason": "r", "scores": {k: 7 for k in moments.SCORE_KEYS}}]
    job_dir = make_job(tmp_path, words, old)

    def generate(client, model, system, user, schema, report=None, log=print):
        if user.startswith("Candidates"):
            return [verdict(i) for i in ids_in(user)]
        return [proposal(lines, 20, 28, payoff=ANSWER)]

    assert rank_moments.compare_jobs([job_dir], None, generate=generate, model="fake", log=quiet) == 0
    assert (job_dir / "eval" / "moments.new.json").exists() and (job_dir / "eval" / "ranking.new.json").exists()

    def boom(*a, **k):
        raise AssertionError("--reuse must not call Gemini")
    monkeypatch.setattr(moments, "_generate_json", boom)
    out_file = tmp_path / "report.html"
    monkeypatch.setattr(sys, "argv", ["rank_moments.py", "--compare", str(job_dir), "--reuse", "--out", str(out_file)])
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert rank_moments.main() == 0
    page = out_file.read_text(encoding="utf-8")
    assert str(out_file) in capsys.readouterr().out
    assert "Money &lt;talk&gt; &amp; co" in page and "Why &lt;nobody&gt; gets rich" in page
    assert "<talk>" not in page and "prefers-color-scheme" in page and "http" not in page
    assert "class=\"first\"" in page and "payoff" in page and "after the cut" in page
    assert "looks unfinished" in page and "overlaps old #1" in page
    assert "value 70 (rank 1)" in page and "classic 70 (rank 1)" in page
