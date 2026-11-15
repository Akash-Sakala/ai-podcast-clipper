"""
The laptop's FFmpeg work for the two-phase pipeline (pipeline.py):

    extract_audio   the video's audio, for transcription: stream-copied into audio.m4a
                    (AAC, decodes to exactly the same samples as the video), else 16 kHz
                    mono FLAC (what the Transcriber decodes to anyway)
    make_footage    one stream-copied piece per chosen clip (see segments.py for why the
                    render then comes out exactly as from the whole video), each checked
                    packet by packet before it is uploaded

Uses the FFmpeg that yt-dlp merges with (imageio-ffmpeg), never ffprobe. Every child
process starts without a window (media.no_window). Raises Unsupported when a video can't
be handled this way; the runner then uploads the whole video as before.
"""

from __future__ import annotations

import pathlib
import subprocess

import segments
from media import no_window

PROBE_TIMEOUT_S = 120
CUT_TIMEOUT_S = 600
CHECK_SECONDS = 3.0             # how much of each piece the packet check reads
CHECK_PACKETS = 12              # packets compared per stream
COVER_MARGIN_S = 1.0            # a piece must reach this far past the clip's end (or the video's end)


class Unsupported(RuntimeError):
    """This video can't be cut into clip footage; upload the whole video instead."""


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """FFmpeg with these arguments; stdout and stderr as text. Never raises for its exit code."""
    return subprocess.run([ffmpeg_exe(), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, **no_window())


def _check(result: subprocess.CompletedProcess, what: str) -> None:
    if result.returncode != 0:
        raise Unsupported(f"{what} failed: {(result.stderr or '').strip()[-300:]}")


# --------------------------------------------------------------------------- #
# What the video is
# --------------------------------------------------------------------------- #

def probe(video: pathlib.Path) -> dict:
    """parse_banner's facts plus "timeBases" {0: video, 1: audio} as FFmpeg reads the streams."""
    result = run_ffmpeg(["-hide_banner", "-t", "0.001", "-i", str(video), "-map", "0:v:0?", "-map", "0:a:0?",
                         "-c", "copy", "-f", "framemd5", "-"], PROBE_TIMEOUT_S)
    info = segments.parse_banner(result.stderr or "")
    info["timeBases"] = segments.parse_time_bases(result.stdout or "")
    return info


def footage_facts(video: pathlib.Path) -> dict:
    """{"startUs", "endS", "timescale", "videoTimeBase"}, or Unsupported with the reason.
    Only what was proven identical is allowed: an MP4/MOV with H.264 video and one AAC
    audio stream (other codecs may decode differently from another starting point)."""
    info = probe(video)
    if not info["formats"] or "mov" not in info["formats"].split(","):
        raise Unsupported(f"the container is {info['formats'] or 'unknown'}, not MP4/MOV")
    if not info["video"] or info["video"][0] != "h264":
        raise Unsupported(f"the video codec is {(info['video'] or ['none'])[0]}, not H.264")
    if info["audio"] != ["aac"]:
        raise Unsupported(f"the audio is {', '.join(info['audio']) or 'missing'}, not one AAC stream")
    if info["startUs"] is None:
        raise Unsupported("FFmpeg did not report the video's start time")
    bases = info["timeBases"]
    if set(bases) != {0, 1} or any(num != 1 for num, _ in bases.values()):
        raise Unsupported(f"unexpected stream time bases {bases}")
    timescale = segments.movie_timescale([den for _, den in bases.values()])
    if timescale is None:
        raise Unsupported(f"no MP4 timescale fits the time bases {bases}")
    end = info["startUs"] / 1e6 + info["durationS"] if info["durationS"] else None
    return {"startUs": info["startUs"], "endS": end, "timescale": timescale, "videoTimeBase": bases[0][1]}


# --------------------------------------------------------------------------- #
# Audio for transcription
# --------------------------------------------------------------------------- #

def extract_audio(video: pathlib.Path, out_dir: pathlib.Path) -> pathlib.Path:
    """The audio of `video` in out_dir: audio.m4a (AAC, stream copy) or audio.flac (other
    codecs: 16 kHz mono 16-bit, the samples main.read_audio would decode). Unsupported when
    the video has no audio or several audio streams (the transcriber picks one itself)."""
    info = probe(video)
    if not info["audio"]:
        raise Unsupported("the video has no audio stream")
    if len(info["audio"]) > 1:
        raise Unsupported(f"the video has {len(info['audio'])} audio streams")
    out_dir.mkdir(parents=True, exist_ok=True)
    if info["audio"][0] == "aac":
        target = out_dir / "audio.m4a"
        codec = ["-c:a", "copy"]
    else:
        target = out_dir / "audio.flac"
        codec = ["-ac", "1", "-ar", "16000", "-sample_fmt", "s16", "-c:a", "flac"]
    part = target.with_name(target.stem + ".part" + target.suffix)
    result = run_ffmpeg(["-y", "-loglevel", "error", "-i", str(video), "-vn", "-sn", "-dn", "-map", "0:a:0",
                         *codec, str(part)], CUT_TIMEOUT_S)
    _check(result, "Extracting the audio")
    part.replace(target)
    return target


# --------------------------------------------------------------------------- #
# Clip footage
# --------------------------------------------------------------------------- #

# Same idea as render.first_packets and segments.first_packets; kept apart on purpose (the laptop's
# FFmpeg runner and string tuples here, the container's FFmpeg and parsed tuples there).
def _packets(args: list[str]) -> list[tuple]:
    result = run_ffmpeg(["-loglevel", "error", *args, "-map", "0:v:0", "-map", "0:a:0", "-c", "copy",
                         "-copyts", "-f", "framemd5", "-"], PROBE_TIMEOUT_S)
    _check(result, "Reading packets")
    return [(line.split(",")[0].strip(), " ".join(line.split())) for line in (result.stdout or "").splitlines()
            if line and not line.startswith("#")]


def _first(lines: list[tuple], per_stream: int = CHECK_PACKETS) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for stream, text in lines:
        bucket = out.setdefault(stream, [])
        if len(bucket) < per_stream:
            bucket.append(text)
    return out


def last_video_seconds(lines: list[tuple], time_base_den: int) -> float | None:
    """The latest video presentation time in a packet listing (seconds), or None."""
    pts = [packet[2] for _, text in lines for packet in segments.parse_packets(text) if packet[0] == 0]
    return max(pts) / time_base_den if pts else None


def check_footage(video: pathlib.Path, footage: pathlib.Path, start: float, end: float, facts: dict) -> None:
    """Unsupported unless the piece starts with exactly the packets (timestamps, sizes, data)
    that the whole video's render seek starts from, the render's seek inside the piece lands
    on them too, and the piece reaches past the clip's end."""
    seek = segments.seek_text(start)
    target = segments.footage_target(start, facts["startUs"])
    window = f"{CHECK_SECONDS:.3f}"
    from_video = _first(_packets(["-ss", seek, "-t", window, "-i", str(video)]))
    whole_piece = _packets(["-i", str(footage)])
    from_piece = _first(whole_piece)
    sought = _first(_packets([*segments.input_seek_args(start, target), "-t", window, "-i", str(footage)]))
    if set(from_video) != {"0", "1"}:
        raise Unsupported(f"the seek at {seek}s gave no {'audio' if '1' not in from_video else 'video'} packets")
    if from_piece != from_video:
        raise Unsupported(f"the piece for {seek}s does not start with the packets the render reads")
    if sought != from_video:
        raise Unsupported(f"seeking inside the piece for {seek}s lands on other packets")
    reach = last_video_seconds(whole_piece, facts["videoTimeBase"])
    needed = target / 1e6 + (end - start) + COVER_MARGIN_S
    if facts.get("endS") is not None:
        needed = min(needed, facts["endS"] - COVER_MARGIN_S)
    if reach is None or reach < needed:
        raise Unsupported(f"the piece for {seek}s ends at {reach}s, before {needed:.3f}s")


def cut_footage(video: pathlib.Path, start: float, end: float, out: pathlib.Path, facts: dict) -> None:
    """Stream-copy the clip [start, end] plus its lead-in from the keyframe before it and
    TAIL_SECONDS after it, keeping every timestamp (see segments.py)."""
    part = out.with_name(out.stem + ".part.mp4")
    result = run_ffmpeg(["-y", "-loglevel", "error", "-ss", segments.seek_text(start),
                         "-t", f"{end - start + segments.TAIL_SECONDS:.3f}", "-i", str(video),
                         "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-copyts",
                         "-movie_timescale", str(facts["timescale"]), "-f", "mp4", str(part)], CUT_TIMEOUT_S)
    _check(result, f"Cutting the clip at {segments.seek_text(start)}s")
    part.replace(out)


def make_footage(video: pathlib.Path, moments: list[dict], out_dir: pathlib.Path) -> list[dict]:
    """One checked piece per moment (clip_<index>.mp4 in out_dir). Returns, per clip,
    {"index", "path", "targetUs", "bytes"}. Raises Unsupported (nothing to upload then)."""
    facts = footage_facts(video)
    out_dir.mkdir(parents=True, exist_ok=True)
    pieces = []
    for index, moment in enumerate(moments):
        start, end = float(moment["start"]), float(moment["end"])
        path = out_dir / segments.footage_name(index)
        cut_footage(video, start, end, path, facts)
        check_footage(video, path, start, end, facts)
        pieces.append({"index": index, "path": path, "targetUs": segments.footage_target(start, facts["startUs"]),
                       "bytes": path.stat().st_size})
    return pieces
