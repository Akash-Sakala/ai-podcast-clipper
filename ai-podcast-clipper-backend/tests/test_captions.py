"""Captions (one line of up to 5 words, sentence-aware, no lone words, at least 0.8 s on screen,
the spoken word in amber with a pop, 25% up from the bottom) and
loudness normalisation of each clip (render.py)."""

import json
import math
import pathlib
import shutil
import subprocess

import pytest

import render


def words_of(text: str, start: float = 0.0, step: float = 0.3, length: float = 0.25) -> list[dict]:
    return [{"start": start + step * i, "end": start + step * i + length, "word": w}
            for i, w in enumerate(text.split())]


def captions(text: str, **kwargs) -> list[str]:
    return [" ".join(w for _, _, w in g) for g in render.caption_groups(words_of(text), 0.0, 60.0, **kwargs)]


def lines_of(text: str, **kwargs) -> list[list[str]]:
    return [render.two_lines([w for *_, w in g]) for g in render.caption_groups(words_of(text), 0.0, 60.0, **kwargs)]


def test_a_caption_never_runs_across_a_sentence():
    assert captions("That blew my mind. Or did it really? Yes.") == ["That blew my mind.", "Or did it really?", "Yes."]


def test_captions_are_one_line_of_up_to_five_words():
    assert captions("I think the biggest risk is that nobody notices until it is far too late.") == [
        "I think the biggest", "risk is that nobody", "notices until", "it is far too late."]
    assert captions("a b c d e f g h") == ["a b c d", "e f g h"]                  # short words: 5 max, split evenly
    for caption in captions("And that was the moment I realized we had to completely change everything."):
        assert len(caption) <= render.SUBTITLE_LINE_CHARS
    assert all(len(lines) == 1 for lines in lines_of("I think the biggest risk is that nobody notices."))


def test_a_long_phrase_splits_evenly_and_whole_phrases_stay_together():
    assert captions("one two three four five six seven") == ["one two three four", "five six seven"]
    assert captions("And that was the moment I realized everything changed.") == [
        "And that was the", "moment I realized", "everything changed."]        # even by length, not 3+2+2+2
    assert captions("Or did it really, honestly, after all those years?") == [
        "Or did it really,", "honestly, after all", "those years?"]


def test_no_lone_word_where_a_neighbour_can_spare_one():
    assert captions("So we raised the money, right?") == ["So we raised", "the money, right?"]   # not 4 + 1
    assert captions("Honestly, the biggest problem was money.") == [
        "Honestly,", "the biggest", "problem was money."]                       # a phrase of its own stays
    assert captions("Yes. It was.") == ["Yes.", "It was."]                      # a one-word sentence stays


def test_a_long_word_gets_its_own_caption():
    assert captions("Absolutely incomprehensible stuff") == ["Absolutely", "incomprehensible", "stuff"]


def test_two_lines_break_where_the_lines_are_most_even():
    assert render.two_lines(["short"]) == ["short"]
    assert render.two_lines("the cost of ignoring this".split()) == ["the cost of", "ignoring this"]


def test_a_pause_starts_a_new_caption():
    words = words_of("before the pause") + words_of("after it", start=2.0)
    assert [" ".join(w for *_, w in g) for g in render.caption_groups(words, 0.0, 10.0)] == [
        "before the pause", "after it"]


def test_only_the_clips_words_count_in_clip_time():
    groups = render.caption_groups(words_of("a b c d e f", start=9.0, step=0.5), 10.0, 11.4)
    assert [w for g in groups for *_, w in g] == ["c", "d", "e"]
    assert groups[0][0][0] == pytest.approx(0.0)


def ass_events(path: pathlib.Path) -> list[tuple[str, str, str]]:
    events = []
    for line in path.read_text().splitlines():
        if line.startswith("Dialogue:"):
            parts = line.split(",", 9)
            events.append((parts[1], parts[2], parts[9]))
    return events


def test_one_event_per_word_shows_its_caption_with_that_word_in_amber(tmp_path):
    pytest.importorskip("pysubs2")
    path = tmp_path / "s.ass"
    assert render.write_subtitles(words_of("That blew my mind."), 0.0, 10.0, path) == 1
    events = ass_events(path)
    assert len(events) == 4
    first, second = events[0][2], events[1][2]
    assert first == render.SUBTITLE_ACTIVE + "That{\\r} blew my mind."             # one line
    assert second == "That " + render.SUBTITLE_ACTIVE + "blew{\\r} my mind."
    assert not any("\\N" in e[2] for e in events)
    assert "&H24A5F5&" in render.SUBTITLE_ACTIVE                 # #F5A524 in ASS's BGR order
    assert events[0][1] == events[1][0]                            # back to back: no flicker between words


def test_the_last_word_of_a_caption_stays_a_moment_unless_the_next_starts(tmp_path):
    pytest.importorskip("pysubs2")
    path = tmp_path / "s.ass"
    words = words_of("first caption.") + words_of("second", start=3.0)
    render.write_subtitles(words, 0.0, 10.0, path)
    events = ass_events(path)
    assert events[1][1] == "0:00:00.85"                  # "caption." ends at 0.55 + 0.3 s hold
    assert events[2][0] == "0:00:03.00"


def test_a_short_caption_stays_up_at_least_0_8_s_unless_the_next_starts(tmp_path):
    pytest.importorskip("pysubs2")
    path = tmp_path / "s.ass"
    words = words_of("Yes.") + words_of("And then", start=3.0) + words_of("No.", start=4.0) + \
        words_of("Right", start=4.4)
    render.write_subtitles(words, 0.0, 10.0, path)
    events = ass_events(path)
    assert (events[0][0], events[0][1]) == ("0:00:00.00", "0:00:00.80")     # not 0.55: at least 0.8 s
    assert events[3][1] == "0:00:04.40"                  # "No." is cut short by the next caption


def test_captions_sit_25_percent_up_with_a_heavy_outline(tmp_path):
    pytest.importorskip("pysubs2")
    path = tmp_path / "s.ass"
    render.write_subtitles(words_of("hello there"), 0.0, 5.0, path)
    style = next(line for line in path.read_text().splitlines() if line.startswith("Style: Default"))
    fields = style.split(",")
    assert fields[1] == "Anton" and fields[2] == "100"
    assert fields[-2] == str(render.SUBTITLE_MARGIN_V) == "480"       # 25% of 1920 up from the bottom
    assert fields[-4] == fields[-3] == "100"                          # clear of the buttons on the right
    assert float(fields[16]) == 5.0                                   # outline


def test_braces_and_backslashes_in_words_cannot_become_ass_tags(tmp_path):
    pytest.importorskip("pysubs2")
    path = tmp_path / "s.ass"
    render.write_subtitles(words_of("{\\b1}bold"), 0.0, 5.0, path)
    text = ass_events(path)[0][2]
    assert "{\\b1}" not in text and "(/b1)bold" in text


def test_audio_filter_applies_the_measured_gain_then_the_fades():
    measured = {"input_i": -23.4, "input_tp": -6.1, "input_lra": 5.2, "input_thresh": -33.9, "target_offset": 0.3}
    chain = render.audio_filter(40.0, measured)
    assert chain.startswith("loudnorm=I=-14.0:TP=-1.5:LRA=11.0:measured_I=-23.40:measured_TP=-6.10")
    # 48 kHz stereo named explicitly: Modal's FFmpeg 4.4 could not link a bare aresample to the fade.
    assert ":linear=true,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,afade=t=in" in chain
    assert "aresample" not in chain
    assert render.audio_filter(40.0, None) == render.audio_fade(40.0)


def test_silence_or_a_failed_measurement_leaves_the_level_alone(monkeypatch):
    def run(stderr, code=0):
        return lambda *a, **k: subprocess.CompletedProcess(a, code, "", stderr)

    good = json.dumps({"input_i": "-20.1", "input_tp": "-3.0", "input_lra": "4.0",
                       "input_thresh": "-30.5", "target_offset": "0.2"})
    monkeypatch.setattr(render.subprocess, "run", run("noise " + good))
    assert render.measure_loudness(pathlib.Path("x.mkv"))["input_i"] == -20.1
    silent = good.replace('"-20.1"', '"-inf"')
    monkeypatch.setattr(render.subprocess, "run", run(silent))
    assert render.measure_loudness(pathlib.Path("x.mkv")) is None
    monkeypatch.setattr(render.subprocess, "run", run("no json here", 1))
    assert render.measure_loudness(pathlib.Path("x.mkv")) is None


def test_loudnorm_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("CLIP_LOUDNORM", "off")
    assert not render.loudness_enabled()
    monkeypatch.delenv("CLIP_LOUDNORM")
    assert render.loudness_enabled()


@pytest.mark.skipif(shutil.which(render.FFMPEG) is None, reason="needs ffmpeg")
def test_a_quiet_clip_comes_out_at_about_minus_14_lufs(tmp_path):
    quiet = tmp_path / "quiet.mkv"
    subprocess.run([render.FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i",
                    "sine=frequency=300:duration=12,volume=-30dB", "-c:a", "pcm_s16le", str(quiet)], check=True)
    measured = render.measure_loudness(quiet)
    assert measured is not None and measured["input_i"] < -30
    out = tmp_path / "out.m4a"
    subprocess.run([render.FFMPEG, "-loglevel", "error", "-i", str(quiet), "-af", render.audio_filter(12.0, measured),
                    "-c:a", "aac", "-b:a", "160k", str(out)], check=True)
    after = render.measure_loudness(out)
    assert after is not None and math.isclose(after["input_i"], -14.0, abs_tol=1.0)


MEASURED = {"input_i": -23.4, "input_tp": -6.1, "input_lra": 5.2, "input_thresh": -33.9, "target_offset": 0.3}


def run_fallbacks(fail_while, encoder="nvenc", loudness=MEASURED, error=render.RenderError):
    tried = []

    def encode(enc, chain):
        tried.append((enc, "loudnorm" in chain))
        if fail_while(enc, chain):
            raise error("encoding failed: Cannot select channel layout")

    return render.encode_with_fallbacks(encode, encoder, 30.0, loudness), tried


def test_a_failing_loudness_step_never_costs_the_clip():
    (enc, applied), tried = run_fallbacks(lambda enc, chain: "loudnorm" in chain)
    assert tried == [("nvenc", True), ("nvenc", False)]
    assert enc == "nvenc" and applied is None                 # rendered, at the video's own level


def test_nvenc_trouble_still_falls_back_to_libx264():
    (enc, applied), tried = run_fallbacks(lambda enc, chain: enc == "nvenc")
    assert tried == [("nvenc", True), ("nvenc", False), ("x264", False)] and enc == "x264"


def test_a_clean_encode_keeps_the_loudness():
    (enc, applied), tried = run_fallbacks(lambda enc, chain: False)
    assert tried == [("nvenc", True)] and applied is MEASURED


def test_a_broken_source_is_not_retried():
    with pytest.raises(render.DecodeError):
        run_fallbacks(lambda enc, chain: True, error=render.DecodeError)


def test_when_every_attempt_fails_the_last_error_is_raised():
    with pytest.raises(render.RenderError):
        run_fallbacks(lambda enc, chain: True, encoder="x264", loudness=None)
