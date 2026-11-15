"""
Clip footage: the arithmetic shared by the laptop (cutter.py cuts it) and the render
containers (render.py renders from it). Standard library only; bundled into the Modal image.

Instead of the whole video, the laptop uploads one piece per chosen clip. The render must
come out exactly as if it had read the whole video, so each piece is made to reproduce
what render.cut_segment's `-ss <start> -i input.mp4` reads (checked frame by frame and
sample by sample in tests/test_clip_footage.py):

  * the piece is a stream copy taken with the very same input seek (`-ss <start>`, the same
    "%.3f" text), so it begins with exactly the packets that seek starts decoding from:
    the keyframe before the clip and the audio packet before that keyframe. Starting the
    audio anywhere else could change it: AAC decoders carry state from packet to packet
    (noise substitution draws from a running random generator);
  * `-copyts` keeps every packet's timestamp, and the movie timescale is a common multiple
    of the streams' time bases, so the MP4 edit lists that place the piece in time are exact
    (with MP4's default timescale of 1000 they round to a millisecond and shift the audio
    by up to 22 samples);
  * the render reads the piece with `-seek_timestamp 1 -ss <start + the video's start time>`:
    the same absolute target the old command computed, in microseconds, so FFmpeg shifts and
    trims the decoded frames exactly as before. The seek inside the piece lands on its first
    packets (render.check_footage_seek verifies that before every render).
"""

from __future__ import annotations

import decimal
import math
import re

TAIL_SECONDS = 2.0              # footage kept after the clip's end (decoder look-ahead, audio overlap)

MAX_TIMESCALE = 2**31 - 1       # MP4 movie timescale field (32-bit; FFmpeg takes an int)

FOOTAGE_DIR = "footage"         # /<job id>/footage/clip_<n>.mp4 on the volume


def seek_text(start: float) -> str:
    """The input seek of render.cut_segment for a clip starting at `start` seconds."""
    return f"{start:.3f}"


def seek_microseconds(start: float) -> int:
    """seek_text(start) as FFmpeg parses it (microseconds, exact)."""
    return int(decimal.Decimal(seek_text(start)) * 1_000_000)


def microseconds_text(us: int) -> str:
    """Microseconds as the seconds text FFmpeg parses back to the same value."""
    sign = "-" if us < 0 else ""
    us = abs(int(us))
    return f"{sign}{us // 1_000_000}.{us % 1_000_000:06d}"


def footage_target(start: float, source_start_us: int) -> int:
    """Absolute timestamp (microseconds) that `-ss <start> -i <whole video>` decodes from:
    FFmpeg adds the file's start time to the seek."""
    return seek_microseconds(start) + int(source_start_us)


def input_seek_args(start: float, target_us: int | None = None) -> list[str]:
    """FFmpeg input options that seek to a clip: the whole video (target_us None, as always)
    or clip footage, whose packets keep the whole video's timestamps."""
    if target_us is None:
        return ["-ss", seek_text(start)]
    return ["-seek_timestamp", "1", "-ss", microseconds_text(target_us)]


def movie_timescale(time_base_denominators: list[int]) -> int | None:
    """A movie timescale in which every stream's timestamps are whole numbers (their least
    common multiple), or None when there is none MP4 can store."""
    dens = [int(d) for d in time_base_denominators if int(d) > 0]
    if not dens:
        return None
    scale = math.lcm(*dens)
    return scale if scale <= MAX_TIMESCALE else None


def footage_name(index: int) -> str:
    return f"clip_{index}.mp4"


def footage_remote(job_id: str, index: int) -> str:
    return f"/{job_id}/{FOOTAGE_DIR}/{footage_name(index)}"


# --------------------------------------------------------------------------- #
# Reading FFmpeg's own output (the laptop has ffmpeg but not always ffprobe)
# --------------------------------------------------------------------------- #

_START = re.compile(r"Duration: (?P<duration>N/A|[\d:.]+), start: (?P<sign>-?)(?P<s>\d+)\.(?P<us>\d{6})")
_INPUT = re.compile(r"^Input #0, (?P<formats>[^,].*?), from ", re.M)
_STREAM = re.compile(r"^\s*Stream #0:(?P<index>\d+)\S*: (?P<kind>Video|Audio|Data|Subtitle|Attachment): "
                     r"(?P<codec>[\w-]+)", re.M)
_TIME_BASE = re.compile(r"^#tb (?P<stream>\d+): (?P<num>\d+)/(?P<den>\d+)\s*$", re.M)


def parse_banner(stderr: str) -> dict:
    """What `ffmpeg -i <file>` prints about its input: {"formats", "startUs", "durationS",
    "video": [codec, ...], "audio": [codec, ...]}. Missing facts are None or empty."""
    info: dict = {"formats": None, "startUs": None, "durationS": None, "video": [], "audio": []}
    stderr = re.split(r"^(?:Output #\d|Stream mapping:)", stderr, maxsplit=1, flags=re.M)[0]   # the input only
    match = _INPUT.search(stderr)
    if match:
        info["formats"] = match.group("formats")
    match = _START.search(stderr)
    if match:
        us = int(match.group("s")) * 1_000_000 + int(match.group("us"))
        info["startUs"] = -us if match.group("sign") else us
        if match.group("duration") != "N/A":
            h, m, s = match.group("duration").split(":")
            info["durationS"] = int(h) * 3600 + int(m) * 60 + float(s)
    for stream in _STREAM.finditer(stderr):
        kind = stream.group("kind").lower()
        if kind in ("video", "audio"):
            info[kind].append(stream.group("codec"))
    return info


def parse_time_bases(framemd5: str) -> dict[int, tuple[int, int]]:
    """{output stream: (num, den)} from the header of FFmpeg's framemd5 output."""
    return {int(m.group("stream")): (int(m.group("num")), int(m.group("den")))
            for m in _TIME_BASE.finditer(framemd5)}


def parse_packets(framemd5: str) -> list[tuple[int, int, int, int, int, str]]:
    """(stream, dts, pts, duration, size, md5) per packet line of framemd5 output."""
    packets = []
    for line in framemd5.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            packets.append((int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4]), parts[5]))
        except ValueError:
            continue
    return packets


# Same idea as cutter._packets/_first and render.first_packets; kept apart on purpose (cutter.py runs on
# the laptop, render.py in the containers, this module is the part both bundle).
def first_packets(packets: list[tuple], per_stream: int) -> dict[int, list[tuple]]:
    """The first `per_stream` packets of each stream, in file order."""
    out: dict[int, list[tuple]] = {}
    for packet in packets:
        bucket = out.setdefault(packet[0], [])
        if len(bucket) < per_stream:
            bucket.append(packet)
    return out
