"""The end-credit card: drawn on the laptop (endcard.py), uploaded next to the footage, laid
over each clip's last 4.5 s by render.py (a gentle rise; the captions move up above it; the card
is dropped, never the clip, if that encode fails)."""

import io
import json
import pathlib
import shutil
import subprocess

import pytest

import endcard
import jobstore
import phases
import render
import settings

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

INFO = {"title": "How great founders build teams that last", "channel": "The Founders Podcast",
        "duration": 6125.0, "videoId": "abcdefghijk"}


def youtube_job(tmp_path, **fields) -> jobstore.Job:
    job_dir = tmp_path / "jobs" / "20261128-170000-eeeeee"
    job_dir.mkdir(parents=True)
    data = {"id": job_dir.name, "source": "youtube", "title": "t", "youtubeUrl": "https://youtu.be/abcdefghijk",
            "status": "processing", "clips": [],
            "meta": {"title": INFO["title"], "channel": INFO["channel"], "duration": 6125}, **fields}
    (job_dir / "job.json").write_text(json.dumps(data), encoding="utf-8")
    return jobstore.Job(job_dir)


def test_only_youtube_videos_with_the_switch_on_get_a_card(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "END_CARD", True)
    assert endcard.card_info(youtube_job(tmp_path)) == INFO
    monkeypatch.setattr(settings, "END_CARD", False)
    assert endcard.card_info(youtube_job(tmp_path / "b")) is None
    monkeypatch.setattr(settings, "END_CARD", True)
    assert endcard.card_info(youtube_job(tmp_path / "c", source="upload", youtubeUrl=None)) is None
    bare = endcard.card_info(youtube_job(tmp_path / "d", meta=None, title="Episode 12"))
    assert bare["title"] == "Episode 12" and bare["channel"] == "YouTube" and bare["duration"] is None


def test_timecodes():
    assert endcard.timecode(2052) == "34:12" and endcard.timecode(6125) == "1:42:05" and endcard.timecode(5) == "0:05"


def alpha_at(image, x, y):
    return image.getpixel((x, y))[3]


def amber_columns(image, y):
    return [x for x in range(image.width) if image.getpixel((x, y))[:3] == endcard.AMBER[:3]]


def test_the_card_sits_above_the_platform_text_and_marks_where_the_clip_starts():
    thumb = Image.new("RGB", (1280, 720), (40, 90, 160))
    card = endcard.draw_card(INFO, thumb, 2052.0)
    assert card.size == (1080, 1920) and card.mode == "RGBA"
    x0, y0, x1, y1 = endcard.CARD_BOX
    assert alpha_at(card, 10, 10) == 0 and alpha_at(card, 540, y1 + 5) == 0       # the clip shows around it
    assert alpha_at(card, x0 + 10, (y0 + y1) // 2) > 200                           # the card itself
    assert y1 <= 1920 - 450                                                        # clear of a long title
    assert card.getpixel((x0 + 100, y0 + 100))[:3] == (40, 90, 160)                # the real thumbnail
    bar_y = y0 + 282 + 52 + 5
    filled = amber_columns(card, bar_y)
    share = (max(filled) - (x0 + endcard.PAD)) / (x1 - x0 - 2 * endcard.PAD)
    assert abs(share - 2052 / 6125) < 0.03                                          # 34:12 of 1:42:05


def test_without_a_thumbnail_or_a_length_the_card_still_credits_the_channel():
    card = endcard.draw_card(dict(INFO, duration=None), None, 30.0)
    x0, y0, x1, _ = endcard.CARD_BOX
    assert not amber_columns(card, y0 + 282 + 52 + 5)                               # no bar
    assert alpha_at(card, x0 + 100, y0 + 100) > 200


def fake_opener(responses):
    def opener(url, timeout=None):
        name = url.rsplit("/", 1)[-1].split(".")[0]
        body = responses.get(name)
        if body is None:
            raise OSError("404")
        return io.BytesIO(body)
    return opener


def jpeg(size, colour=(200, 50, 50), bars=0):
    image = Image.new("RGB", size, colour)
    if bars:
        image.paste((0, 0, 0), (0, 0, size[0], bars))
        image.paste((0, 0, 0), (0, size[1] - bars, size[0], size[1]))
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


def test_the_largest_youtube_cover_is_used_and_4_3_ones_lose_their_bars(tmp_path):
    big = endcard.fetch_thumbnail("id", opener=fake_opener({"maxresdefault": jpeg((1280, 720))}))
    assert big.size == (1280, 720)
    hq = endcard.fetch_thumbnail("id", opener=fake_opener({"hqdefault": jpeg((480, 360), bars=45)}))
    assert hq.size == (480, 270) and hq.getpixel((240, 2))[0] > 150                # bars cropped away
    local = tmp_path / "thumbnail.jpg"
    local.write_bytes(jpeg((320, 180), (10, 200, 10)))
    assert endcard.fetch_thumbnail("id", local, opener=fake_opener({})).size == (320, 180)
    assert endcard.fetch_thumbnail(None, tmp_path / "missing.jpg") is None


def test_cards_are_made_per_clip_and_uploaded_next_to_the_footage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "END_CARD", True)
    job = youtube_job(tmp_path)
    moments = [{"start": 100.0, "end": 160.0}, {"start": 3000.0, "end": 3080.0}]
    sent = []
    folder = tmp_path / "cards"
    monkeypatch.setattr(endcard, "fetch_thumbnail", lambda *a, **k: Image.new("RGB", (1280, 720)))
    count = endcard.cards_on_modal(job, "volume", moments, folder, lambda j, v, files: sent.extend(
        (local.name, remote, local.exists()) for local, remote in files))
    assert count == 2
    assert sent == [("clip_0.png", f"/{job.id}/cards/clip_0.png", True),
                    ("clip_1.png", f"/{job.id}/cards/clip_1.png", True)]
    assert not folder.exists()                                                     # temp files removed


def test_a_card_problem_never_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "END_CARD", True)
    job = youtube_job(tmp_path)
    monkeypatch.setattr(endcard, "fetch_thumbnail", lambda *a, **k: None)

    def broken(*a):
        raise RuntimeError("upload failed")

    assert endcard.cards_on_modal(job, "v", [{"start": 1.0}], tmp_path / "c", broken) == 0
    monkeypatch.setattr(settings, "END_CARD", False)
    assert endcard.cards_on_modal(job, "v", [{"start": 1.0}], tmp_path / "c", broken) == 0   # off: no upload


def test_render_on_modal_sends_the_cards_before_rendering(tmp_path, monkeypatch):
    job = youtube_job(tmp_path)
    order = []
    monkeypatch.setattr(phases.endcard, "cards_on_modal", lambda *a, **k: order.append("cards") or 2)
    monkeypatch.setattr(phases, "spawn", lambda *a, **k: order.append("render") or "call")
    monkeypatch.setattr(phases, "wait_for", lambda call, job, on_ready=None: {"timings": {}})
    monkeypatch.setattr(phases, "finished", lambda job: None)
    timings: dict = {}
    phases.render_on_modal(job, {phases.RENDER_FUNCTION: object()}, {"moments": [{"start": 1.0}]}, None, timings,
                           None, volume="volume")
    assert order == ["cards", "render"] and "endCards" in timings
    order.clear()
    phases.render_on_modal(job, {phases.RENDER_FUNCTION: object()}, {"moments": [{"start": 1.0}]}, None, {}, None)
    assert order == ["render"]                                                     # no volume: as before


# --------------------------------------------------------------------------- #
# render.py
# --------------------------------------------------------------------------- #

def test_the_card_takes_the_last_four_and_a_half_seconds_never_the_first_half():
    assert render.end_card_start(60.0) == 55.5
    assert render.end_card_start(6.0) == 3.0


def test_the_card_fades_in_and_rises_gently_over_the_captions():
    graph = render.end_card_filter("subtitles_card.ass", 55.5)
    assert graph.startswith("[0:v]ass=subtitles_card.ass[base];")
    assert "fade=t=in:st=55.50:d=0.5:alpha=1" in graph
    assert "if(lt(t,55.50+0.8),240*pow(1-(t-55.50)/0.8,3),0)" in graph               # 0.8 s cubic ease-out
    assert "enable='gte(t,55.50)'" in graph and "eof_action=pass" in graph and graph.endswith("[v]")


def test_encode_with_a_card_maps_the_filter_graph(tmp_path, monkeypatch):
    seen = {}

    class FakeProc:
        stdin = io.BytesIO()

        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        seen["cmd"], seen["cwd"] = cmd, kwargs.get("cwd")
        return FakeProc()

    monkeypatch.setattr(render.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(render, "iter_clean_frames", lambda *a, **k: iter(()))
    card = tmp_path / "card dir" / "clip_0.png"
    card.parent.mkdir()
    card.write_bytes(b"png")
    render.encode_pass(tmp_path / "s.mkv", 640, 360, [], tmp_path / "subtitles_card.ass", 30.0, tmp_path / "o.mp4",
                       "x264", lambda i: None, card=card)
    cmd = seen["cmd"]
    assert "-vf" not in cmd and cmd[cmd.index("-filter_complex") + 1] == render.end_card_filter(
        "subtitles_card.ass", 25.5)
    assert cmd[cmd.index("-map") + 1] == "[v]" and "1:a:0?" in cmd
    image = cmd.index(str(card.resolve()))
    assert cmd[image - 1] == "-i" and cmd[image - 7:image - 5] == ["-loop", "1"]   # a still, looped


def test_captions_move_above_the_card_when_it_appears(tmp_path):
    pytest.importorskip("pysubs2")
    words = [{"start": 0.4 * i, "end": 0.4 * i + 0.3, "word": f"w{i}"} for i in range(25)]
    path = tmp_path / "s.ass"
    render.write_subtitles(words, 0.0, 10.0, path, lift_at=7.1)
    events = [line.split(",", 9) for line in path.read_text().splitlines() if line.startswith("Dialogue:")]
    lift = "{\\an2\\pos(540,1012)}"
    before = [e for e in events if e[2] <= "0:00:07.10"]
    after = [e for e in events if e[1] >= "0:00:07.10"]
    assert before and after and len(before) + len(after) == len(events)            # split at the card
    assert not any(e[9].startswith(lift) for e in before) and all(e[9].startswith(lift) for e in after)
    assert max(e[2] for e in events) > "0:00:09.00"                                 # the last words stay captioned


def test_a_failing_card_drops_the_card_not_the_clip():
    tried = []

    def encode(enc, chain, use_card):
        tried.append((enc, "loudnorm" in chain, use_card))
        if use_card or enc == "nvenc":
            raise render.RenderError("overlay failed")

    measured = {"input_i": -20.0, "input_tp": -3.0, "input_lra": 5.0, "input_thresh": -30.0, "target_offset": 0.1}
    enc, _ = render.encode_with_fallbacks(encode, "nvenc", 30.0, measured, card=True)
    assert tried == [("nvenc", True, True), ("nvenc", False, True), ("nvenc", False, False), ("x264", False, False)]
    assert enc == "x264"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_real_encode_shows_the_card_at_the_end(tmp_path):
    pytest.importorskip("pysubs2")
    import numpy as np

    segment = tmp_path / "segment.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=white:s=1080x1920:d=2:r=25",
                    "-f", "lavfi", "-i", "sine=f=330:d=2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(segment)], check=True)
    card = tmp_path / "card.png"
    endcard.draw_card(INFO, None, 100.0).save(card)
    subtitles = tmp_path / "subtitles.ass"
    render.write_subtitles([], 0.0, 2.0, subtitles)
    white = np.full((1920, 1080, 3), 255, dtype=np.uint8)
    frames = iter([white] * 50)
    out = tmp_path / "clip.mp4"
    original = render.iter_clean_frames, render.vertical_frame
    render.iter_clean_frames, render.vertical_frame = (lambda *a, **k: frames), (lambda frame, face: frame)
    try:
        render.encode_pass(segment, 1080, 1920, [None] * 50, subtitles, 2.0, out, "x264", lambda i: None,
                           card=card, card_start=1.0)
    finally:
        render.iter_clean_frames, render.vertical_frame = original

    def pixel(at):
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(at), "-i", str(out), "-frames:v", "1",
                              "-vf", "crop=2:2:200:1240", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        return raw[0]

    assert pixel(0.5) > 200 and pixel(1.8) < 60                  # white clip, then the dark card over it
    assert pathlib.Path(out).stat().st_size > 0
