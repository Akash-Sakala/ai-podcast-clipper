"""The two-phase pipeline's media work, checked with the real FFmpeg (skipped without it):

* clip footage (cutter.make_footage) renders exactly like the whole video: render.py's real
  cut step (the 25 fps intermediate and its audio) gives the same decoded frames (MD5 per
  frame, count included, burned-in frame numbers) and the same audio samples, for long and
  open GOPs, B-frames, 25/30/60 fps, a variable frame rate, 44.1/48 kHz AAC, a non-zero start
  time, audio that starts before the video, a yt-dlp style merge, and clips on, just after
  and long after a keyframe, at the very start and at the end of the video;
* the laptop's packet check refuses footage whose timestamps are off by even one audio sample;
* the audio copied out for transcription decodes to the very samples the Transcriber got
  from the whole video;
* the arithmetic and parsing in segments.py.

    python -m pytest tests/test_clip_footage.py -q
"""

import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import segments  # noqa: E402

DURATION = 12          # seconds of each synthetic source
CLIP = 4.0             # seconds of each clip


def have_ffmpeg() -> bool:
    try:
        import imageio_ffmpeg

        return bool(imageio_ffmpeg.get_ffmpeg_exe()) and shutil.which("ffprobe") is not None
    except Exception:  # noqa: BLE001
        return False


needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="needs ffmpeg (imageio-ffmpeg) and ffprobe")


def render_side_major() -> int | None:
    """Major version of the FFmpeg that plays the render container here (render.FFMPEG: FFMPEG_BIN
    or the one on PATH)."""
    exe = shutil.which(os.environ.get("FFMPEG_BIN", "ffmpeg"))
    if not exe:
        return None
    try:
        first = subprocess.run([exe, "-version"], capture_output=True, text=True).stdout.split("\n")[0]
        return int(first.split("version ")[1].lstrip("nN").split(".")[0])
    except (IndexError, ValueError, OSError):
        return None


# Modal renders with Ubuntu 22.04's FFmpeg 4.4 (main.py refuses to build the image with another).
# The exact-match tests below hold for a render side of 4.4 and 6.1; newer ones (seen with 9.0)
# seek the audio of MP4 footage differently, which says nothing about Modal. On such a machine,
# point FFMPEG_BIN at an FFmpeg 4.4-6.x to run them.
RENDER_MAJOR = render_side_major()
like_modal = pytest.mark.skipif(RENDER_MAJOR is None or RENDER_MAJOR > 6,
                                reason=f"the render side must be FFmpeg 4.4-6.x like Modal's, found {RENDER_MAJOR}")


# --------------------------------------------------------------------------- #
# segments.py
# --------------------------------------------------------------------------- #

def test_seek_text_and_microseconds_are_what_ffmpeg_parses():
    assert segments.seek_text(7.3337) == "7.334" and segments.seek_microseconds(7.3337) == 7_334_000
    for start in (0.0, 0.05, 3600.0005, 1234.5675, 59.9999):
        assert segments.seek_microseconds(start) == round(float(segments.seek_text(start)) * 1000) * 1000
    for us in (0, 1, 999_999, 1_000_000, 61_234_567, -21_333):
        text = segments.microseconds_text(us)
        sign = -1 if text.startswith("-") else 1
        whole, frac = text.lstrip("-").split(".")
        assert len(frac) == 6 and sign * (int(whole) * 1_000_000 + int(frac)) == us


def test_footage_target_adds_the_video_start_time():
    assert segments.footage_target(12.5, 0) == 12_500_000
    assert segments.footage_target(12.5, 1_213_000) == 13_713_000
    assert segments.input_seek_args(12.5) == ["-ss", "12.500"]                     # the whole video, as always
    assert segments.input_seek_args(12.5, 13_713_000) == ["-seek_timestamp", "1", "-ss", "13.713000"]


def test_movie_timescale_is_a_common_multiple_that_fits():
    assert segments.movie_timescale([15360, 44100]) == 11_289_600
    assert segments.movie_timescale([90000, 48000]) == 720_000
    assert segments.movie_timescale([12800, 44100]) % 12800 == 0
    assert segments.movie_timescale([]) is None
    assert segments.movie_timescale([2**31 - 1, 2**31 - 19]) is None             # too big for MP4


BANNER = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'C:\\Users\\a b\\talk (1).mp4':
  Duration: 00:30:02.04, start: -0.021333, bitrate: 2916 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1920x1080, 30 fps, 15360 tbn (default)
  Stream #0:1[0x2](eng): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 128 kb/s (default)
  Stream #0:2[0x3](eng): Data: none (tmcd / 0x64636D74)
Stream mapping:
  Stream #0:1 -> #0:1 (copy)
Output #0, framemd5, to 'pipe:':
  Stream #0:1: Audio: aac (LC), 44100 Hz, stereo, fltp, 128 kb/s
"""


def test_parse_banner_and_time_bases():
    info = segments.parse_banner(BANNER)
    assert info["formats"] == "mov,mp4,m4a,3gp,3g2,mj2" and info["startUs"] == -21_333
    assert info["durationS"] == pytest.approx(1802.04) and info["video"] == ["h264"] and info["audio"] == ["aac"]
    empty = segments.parse_banner("")
    assert empty["startUs"] is None and empty["video"] == [] and empty["formats"] is None
    header = "#format: frame checksums\n#tb 0: 1/15360\n#media_type 0: video\n#tb 1: 1/44100\n"
    assert segments.parse_time_bases(header) == {0: (1, 15360), 1: (1, 44100)}
    lines = "0,  -1024,  0,  512,  14616, c92d\n1, 0, 0, 1024, 290, 6810\n#x\nbad line\n"
    assert segments.parse_packets(lines) == [(0, -1024, 0, 512, 14616, "c92d"), (1, 0, 0, 1024, 290, "6810")]


# --------------------------------------------------------------------------- #
# Synthetic sources
# --------------------------------------------------------------------------- #

def ff(*args):
    import imageio_ffmpeg

    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *args], check=True,
                   capture_output=True)


def picture(fps, size="192x108", extra=""):
    # testsrc2 moves every frame; the burned-in frame number makes an off-by-one frame visible.
    return ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate={fps}:duration={DURATION},"
            f"drawtext=text='%{{n}}':fontsize=28:fontcolor=white:x=6:y=6{extra}"]


def sound(rate, channels=1):
    # A chirp plus noise: every sample differs, and the noise makes FFmpeg's AAC encoder
    # use tools whose decoding depends on where decoding started.
    return ["-f", "lavfi", "-i", f"aevalsrc='0.3*sin(2*PI*(200+40*t)*t)+0.05*random(0)':s={rate}:d={DURATION}",
            "-ac", str(channels)]


def x264(*params):
    return ["-c:v", "libx264", "-pix_fmt", "yuv420p", *params]


SOURCES = {
    "gop_longer_than_video_bframes_30fps_48k": lambda: [*picture(30), *sound(48000), *x264("-g", "600", "-bf", "3"),
                                                        "-c:a", "aac"],
    "open_gop_bframes_60fps_44k": lambda: [*picture(60), *sound(44100),
                                           *x264("-x264-params", "keyint=120:open-gop=1:bframes=3"), "-c:a", "aac"],
    "short_gop_no_bframes_25fps_44k": lambda: [*picture(25), *sound(44100), *x264("-g", "30", "-bf", "0"),
                                               "-c:a", "aac"],
    "variable_frame_rate_48k": lambda: [*picture(30, extra=",setpts='(N/30+0.012*sin(N*1.7))/TB'"), *sound(48000),
                                        "-fps_mode", "passthrough", "-video_track_timescale", "90000",
                                        *x264("-g", "90", "-bf", "2"), "-c:a", "aac"],
    "nonzero_start_time": lambda: [*picture(30), *sound(48000), *x264("-g", "60", "-bf", "3"), "-c:a", "aac",
                                   "-output_ts_offset", "1.234567"],
    "stereo_128k_long_gop_30fps": lambda: [*picture(30, "320x180"), *sound(44100, 2),
                                           *x264("-g", "150", "-bf", "2"), "-c:a", "aac", "-b:a", "128k"],
}


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    if not have_ffmpeg():
        pytest.skip("needs ffmpeg (imageio-ffmpeg) and ffprobe")
    folder = tmp_path_factory.mktemp("sources")
    out = {}
    for name, args in SOURCES.items():
        out[name] = folder / f"{name}.mp4"
        ff(*args(), "-map", "0:v", "-map", "1:a", str(out[name]))
    # Audio starts 0.4 s before the video.
    both = folder / "_both.mp4"
    ff(*picture(30), *sound(44100), *x264("-g", "90", "-bf", "3"), "-c:a", "aac", "-map", "0:v", "-map", "1:a",
       str(both))
    out["video_starts_after_audio"] = folder / "video_starts_after_audio.mp4"
    ff("-itsoffset", "0.4", "-i", str(both), "-i", str(both), "-map", "0:v", "-map", "1:a", "-c", "copy",
       str(out["video_starts_after_audio"]))
    # yt-dlp: fragmented DASH video and audio downloaded separately, merged by stream copy.
    dash_v, dash_a = folder / "_v.mp4", folder / "_a.m4a"
    frag = ["-movflags", "frag_keyframe+empty_moov+default_base_moof"]
    ff(*picture(30, "320x180"), *x264("-g", "60", "-bf", "2"), *frag, "-an", str(dash_v))
    ff(*sound(44100, 2), "-c:a", "aac", "-b:a", "128k", *frag, str(dash_a))
    out["youtube_style_merge"] = folder / "youtube_style_merge.mp4"
    ff("-i", str(dash_v), "-i", str(dash_a), "-map", "0:v:0", "-map", "1:a:0", "-c", "copy",
       str(out["youtube_style_merge"]))
    return out


def keyframes(video: pathlib.Path) -> list[float]:
    """Keyframe times relative to the file's start (what render's -ss counts from)."""
    import cutter

    start = cutter.probe(video)["startUs"] / 1e6
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                          "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True, check=True).stdout
    return sorted(float(x.strip(",")) - start for x in out.split() if x.strip(","))


def clip_starts(video: pathlib.Path) -> dict[str, float]:
    kfs = keyframes(video)
    inner = [k for k in kfs if 1.5 < k < DURATION - CLIP - 1.5] or [kfs[0]]
    k = inner[0]
    following = [x for x in kfs if x > k + 0.5]
    before_next = min(following[0] - 0.05 if following else DURATION, DURATION - CLIP - 0.1)
    return {"on_keyframe": k, "just_after_keyframe": k + 0.034, "long_after_keyframe": before_next,
            "at_zero": 0.0, "near_start": 0.05, "at_the_end": DURATION - CLIP - 0.02}


def decoded(path: pathlib.Path, stream: str) -> list[str]:
    """Per decoded frame of render's intermediate: "pts, duration, size, md5" (framemd5)."""
    import imageio_ffmpeg

    out = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-i", str(path), "-map",
                          f"0:{stream}:0", "-f", "framemd5", "-"], capture_output=True, text=True, check=True).stdout
    return [" ".join(line.split()[1:]) for line in out.splitlines() if not line.startswith("#")]


def render_cut(source, start, out, target_us=None):
    import render

    render.cut_segment(source, start, CLIP, out, "x264", target_us)


@needs_ffmpeg
@like_modal
@pytest.mark.parametrize("name", [*SOURCES, "video_starts_after_audio", "youtube_style_merge"])
def test_clip_footage_renders_exactly_like_the_whole_video(name, sources, tmp_path):
    import cutter
    import render

    video = sources[name]
    starts = clip_starts(video)
    moments = [{"start": s, "end": s + CLIP} for s in starts.values()]
    pieces = cutter.make_footage(video, moments, tmp_path / "footage")       # cut and checked on the "laptop"
    for (case, start), piece in zip(starts.items(), pieces):
        assert piece["path"].stat().st_size <= video.stat().st_size   # a GOP longer than the video: all of it
        render.check_footage_seek(piece["path"], start, piece["targetUs"])   # the render container's check
        whole, part = tmp_path / f"{case}_whole.mkv", tmp_path / f"{case}_footage.mkv"
        render_cut(video, start, whole)
        render_cut(piece["path"], start, part, piece["targetUs"])
        frames = decoded(whole, "v")
        # The count depends on the FFmpeg version (4.4 gives 98-99 of 100 here, fewer when the audio
        # starts first), so only a sanity bound is absolute; the footage must match the whole video.
        assert len(frames) >= round(CLIP * 25 * 0.8), f"{name} {case}: the whole-video render is too short"
        assert len(decoded(part, "v")) == len(frames), f"{name} {case}: frame count differs"
        assert decoded(part, "v") == frames, f"{name} {case}: video frames differ"
        assert decoded(part, "a") == decoded(whole, "a"), f"{name} {case}: audio samples differ"


@needs_ffmpeg
def test_the_harness_sees_audio_that_depends_on_where_decoding_started(sources, tmp_path):
    """The control: AAC decoded from another starting point differs here, so footage that began
    at another packet than the whole video's seek would fail the test above."""
    import imageio_ffmpeg

    video = sources["short_gop_no_bframes_25fps_44k"]

    def pcm(seek):
        raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-ss", str(seek), "-i",
                              str(video), "-map", "0:a", "-f", "s32le", "-"], capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.int32)

    early, late = pcm(2.5), pcm(4.0)
    offset = int(1.5 * 44100)
    n = min(len(early) - offset, len(late))
    assert (early[offset:offset + n] != late[:n]).sum() > 1000


@needs_ffmpeg
def test_the_laptop_check_refuses_footage_off_by_an_audio_sample(sources, tmp_path):
    """MP4's default movie timescale (1/1000 s) rounds the edit list that places the footage
    in time: the audio moves by up to 22 samples. check_footage must notice and refuse."""
    import cutter

    video = sources["short_gop_no_bframes_25fps_44k"]
    start = clip_starts(video)["on_keyframe"]
    facts = cutter.footage_facts(video)
    piece = tmp_path / "rounded.mp4"
    cutter.cut_footage(video, start, start + CLIP, piece, {**facts, "timescale": 1000})
    with pytest.raises(cutter.Unsupported, match="does not start with the packets"):
        cutter.check_footage(video, piece, start, start + CLIP, facts)
    cutter.cut_footage(video, start, start + CLIP, piece, facts)
    cutter.check_footage(video, piece, start, start + CLIP, facts)              # the exact timescale passes


@needs_ffmpeg
def test_videos_that_are_not_cut_say_why(tmp_path):
    import cutter

    mpeg4 = tmp_path / "old_camera.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=3", "-f", "lavfi", "-i",
       "sine=frequency=300:duration=3", "-c:v", "mpeg4", "-c:a", "aac", str(mpeg4))
    with pytest.raises(cutter.Unsupported, match="not H.264"):
        cutter.make_footage(mpeg4, [{"start": 0.5, "end": 2.0}], tmp_path / "f")
    silent = tmp_path / "silent.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=3", *x264(), str(silent))
    with pytest.raises(cutter.Unsupported, match="not one AAC stream"):
        cutter.footage_facts(silent)
    with pytest.raises(cutter.Unsupported, match="no audio stream"):
        cutter.extract_audio(silent, tmp_path / "audio")
    mkv = tmp_path / "talk.mkv"
    ff("-i", str(mpeg4), "-c:v", "libx264", "-c:a", "copy", str(mkv))
    with pytest.raises(cutter.Unsupported, match="not MP4/MOV"):
        cutter.footage_facts(mkv)


# --------------------------------------------------------------------------- #
# Audio for transcription
# --------------------------------------------------------------------------- #

@needs_ffmpeg
@pytest.mark.parametrize("name", [*SOURCES, "video_starts_after_audio", "youtube_style_merge"])
def test_extracted_audio_gives_the_transcriber_the_same_samples(name, sources, tmp_path):
    pytest.importorskip("modal")
    import cutter
    import main

    video = sources[name]
    audio = cutter.extract_audio(video, tmp_path / "audio")
    assert audio.name == "audio.m4a" and audio.stat().st_size < video.stat().st_size
    whole, alone = main.read_audio(video), main.read_audio(audio)
    assert len(whole) > 16000 * (DURATION - 1) and np.array_equal(whole, alone)


@needs_ffmpeg
@like_modal
def test_other_audio_codecs_become_16k_flac_with_the_same_samples(tmp_path):
    pytest.importorskip("modal")
    import cutter
    import main

    video = tmp_path / "mp3.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=25:duration=4", "-f", "lavfi", "-i",
       "aevalsrc='0.3*sin(2*PI*300*t)':s=48000:d=4", *x264(), "-c:a", "libmp3lame", "-shortest", str(video))
    audio = cutter.extract_audio(video, tmp_path / "audio")
    assert audio.name == "audio.flac"
    assert np.array_equal(main.read_audio(video), main.read_audio(audio))
