"""Post text for each chosen clip (moments.write_post_text): one Gemini call after ranking,
checked in code, never failing the job; carried into job.json clipDetails."""

import moments
import pipeline


def words_of(text: str, start: float = 0.0, step: float = 0.4) -> list[dict]:
    return [{"start": start + step * i, "end": start + step * i + 0.3, "word": w} for i, w in enumerate(text.split())]


WORDS = words_of("The biggest mistake founders make is hiring too fast. "
                 "You should hire slowly and fire quickly. That is the whole lesson.")
CHOSEN = [{"start": 0.0, "end": 8.0, "title": "Hiring too fast", "reason": "Why hiring slowly wins."},
          {"start": 8.0, "end": 10.0, "title": "The lesson", "reason": "A short rule."}]


def fake_generate(answer, seen=None, fail=None):
    def generate(client, model, system, user, schema, report=None, log=print):
        if seen is not None:
            seen.append({"system": system, "user": user, "deadline": report.get("_deadline")})
        report["calls"] = report.get("calls", 0) + 1
        report.setdefault("usage", []).append({"model": model, "input": 900, "output": 120, "ok": fail is None})
        report["modelUsed"] = model
        if fail:
            report.setdefault("errors", []).append("boom")
            raise fail
        return answer
    return generate


def test_clean_post_keeps_titles_captions_and_hashtags_in_shape():
    post = moments.clean_post({
        "title": '  "Why   you should hire slowly"  ',
        "caption": "Hiring fast feels like progress. It is not. Would you wait? #startups #hiring",
        "hashtags": ["#Startups", "hiring", "startups", "founder life", "", "x" * 31, "podcast", "growth", "ceo"],
    })
    assert post["title"] == "Why you should hire slowly"
    assert post["caption"] == "Hiring fast feels like progress. It is not. Would you wait?"
    assert post["hashtags"] == ["#Startups", "#hiring", "#founderlife", "#podcast", "#growth"]   # 5 at most


def test_clean_post_cuts_long_text_on_words_and_sentences():
    post = moments.clean_post({"title": "word " * 40, "caption": ("One sentence here. " * 30).strip(),
                               "hashtags": "podcast, business"})
    assert len(post["title"]) <= moments.POST_TITLE_MAX and not post["title"].endswith(" ")
    assert len(post["caption"]) <= moments.POST_CAPTION_MAX and post["caption"].endswith(".")
    assert post["hashtags"] == ["#podcast", "#business"]
    assert moments.clean_post({"title": " ", "caption": "", "hashtags": ["a"]}) is None


def test_every_chosen_clip_gets_post_text_from_one_call():
    seen: list = []
    chosen = [dict(m) for m in CHOSEN]
    report = {"calls": 3, "usage": [{"model": "m", "input": 10, "output": 1, "ok": True}]}
    answer = [{"id": 1, "title": "One rule", "caption": "Short and true.", "hashtags": ["lesson"]},
              {"id": 0, "title": "Stop hiring fast", "caption": "Slow is fast.", "hashtags": ["hiring", "podcast"]},
              {"id": 7, "title": "nope", "caption": "", "hashtags": []}, {"title": "no id"}]
    written = moments.write_post_text(chosen, WORDS, object(), "gemini-x", {"title": "Ep 12", "channel": "Show",
                                      "description": "long text"}, generate=fake_generate(answer, seen),
                                      report=report, log=lambda *_: None)
    assert written == 2
    assert chosen[0]["post"] == {"title": "Stop hiring fast", "caption": "Slow is fast.",
                                 "hashtags": ["#hiring", "#podcast"]}
    assert chosen[1]["post"]["title"] == "One rule"
    assert len(seen) == 1
    assert "Title: Ep 12" in seen[0]["user"] and "Channel: Show" in seen[0]["user"]
    assert "long text" not in seen[0]["user"]                                  # no description: fewer tokens
    assert "Clip 0\nWorking title: Hiring too fast" in seen[0]["user"]
    assert "hire slowly and fire quickly" not in seen[0]["user"].split("Clip 1")[1]   # each clip: its own text
    assert "at most 100 characters" in seen[0]["system"] and "{" not in seen[0]["system"]
    assert seen[0]["deadline"] is not None                                     # its own time limit
    assert report["calls"] == 4 and len(report["usage"]) == 2                  # priced with the ranking calls
    assert report["postText"]["written"] == 2 and report["postText"]["model"] == "gemini-x"
    assert "modelUsed" not in report                                           # the ranking's model stays


def test_a_failed_call_leaves_the_clips_without_post_text():
    chosen = [dict(m) for m in CHOSEN]
    report: dict = {}
    written = moments.write_post_text(chosen, WORDS, object(), "m", None,
                                      generate=fake_generate([], fail=RuntimeError("busy")), report=report,
                                      log=lambda *_: None)
    assert written == 0 and all("post" not in m for m in chosen)
    assert report["postText"]["failed"] is True and report["calls"] == 1 and report["usage"]   # still billed
    assert report["errors"] == ["post text: boom"]


def test_no_client_or_no_clips_makes_no_call():
    report: dict = {}
    assert moments.write_post_text([dict(CHOSEN[0])], WORDS, None, "m", report=report) == 0
    assert report["postText"] == {"ran": False, "written": 0, "skipped": "no Gemini client"}
    assert moments.write_post_text([], WORDS, object(), "m", generate=fake_generate([]), report={}) == 0


def test_post_text_reaches_the_clip_details():
    post = {"title": "T", "caption": "C", "hashtags": ["#a"]}
    details = pipeline.clip_details(["clip_0.mp4", "clip_1.mp4"], [dict(CHOSEN[0], post=post), dict(CHOSEN[1])])
    assert details[0]["post"] == post and details[1]["post"] is None
