"""
Render one vertical clip: cut -> find the speaker (LR-ASD) -> 9:16 frames -> one encode.

Used by the Renderer class in main.py (one clip per GPU container) and by
benchmark_gpu.py. It replaces the old per-clip chain, which cut the clip by
decoding the whole video up to it, ran Columbia_test.py in a new Python process
(JPG frame dump, XVID face videos, an unused debug video), and then encoded the
clip three times. The speaker decisions use the same algorithm and parameters
(see asd/fast_asd.py); the clip is now encoded once, on the GPU's NVENC encoder
when it has one, and keeps the original audio (it used to be 16 kHz mono).

    segment.mkv   the clip at 25 fps, near-lossless (decoded three times below)
    pass 0        tiny thumbnails -> flash frames (1-3 frames of another shot), shown as the
                  frame before them in every pass below (see flash_frames)
    pass 1        scene cuts + S3FD faces on batches of frames
    pass 2        face crops of each track -> LR-ASD scores -> who to follow, per frame,
                  then steadied: no crop change shorter than SPEAKER_MIN_HOLD_SECONDS
                  inside a source shot (see steady_faces)
    pass 3        9:16 frames piped into FFmpeg: subtitles, audio fade, encode
"""

from __future__ import annotations

import math
import os
import pathlib
import shutil
import subprocess
import sys
import time

import numpy as np

ASD_DIR = pathlib.Path(os.environ.get("ASD_DIR") or pathlib.Path(__file__).resolve().parent / "asd")
if str(ASD_DIR) not in sys.path:
    sys.path.insert(0, str(ASD_DIR))

import fast_asd  # noqa: E402

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")
TARGET_W, TARGET_H = 1080, 1920
FPS = fast_asd.FPS
DETECT_BATCH = int(os.environ.get("DETECT_BATCH", "16"))
SMOOTH_FRAMES = 30            # the crop follows the speaker averaged over +-30 frames
BLUR_KERNEL = 121             # background blur when no face is found
SUBTITLE_MAX_WORDS = 5

# Video encoder settings. The segment is an intermediate, so it is near-lossless;
# the final clip is one encode at about the quality the old three encodes ended with.
ENCODERS = {
    "nvenc": {
        "segment": ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "constqp", "-qp", "14"],
        "final": ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", "21",
                  "-b:v", "0", "-pix_fmt", "yuv420p"],
    },
    "x264": {
        "segment": ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "14"],
        "final": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"],
    },
}
# Share of a clip's work per step, for live progress.
STEP_WEIGHTS = {"cut": 0.05, "detect": 0.45, "score": 0.15, "render": 0.35}


class RenderError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# FFmpeg helpers
# --------------------------------------------------------------------------- #

def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderError(f"{pathlib.Path(cmd[0]).name} failed: {result.stderr.strip()[-600:]}")


def probe_encoder(prefer: str = "nvenc") -> str:
    """'nvenc' when this machine can encode H.264 on the GPU, else 'x264'."""
    if prefer != "nvenc":
        return "x264"
    test = [FFMPEG, "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:r=25:d=0.4",
            *ENCODERS["nvenc"]["final"], "-f", "null", "-"]
    try:
        run(test)
        return "nvenc"
    except (RenderError, OSError) as exc:
        print(f"NVENC not available, using libx264 ({exc})")
        return "x264"


def video_size(path: pathlib.Path) -> tuple[int, int]:
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    width, height = out.splitlines()[0].split("x")[:2]
    return int(width), int(height)


def cut_segment(source: pathlib.Path, start: float, duration: float, out: pathlib.Path, encoder: str) -> None:
    """The clip at 25 fps. -ss before -i seeks straight to the clip (still frame-accurate,
    because the video is re-encoded) instead of decoding everything before it."""
    run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
         "-map", "0:v:0", "-map", "0:a:0?", "-r", str(FPS), *ENCODERS[encoder]["segment"],
         "-c:a", "flac", str(out)])


def read_audio_16k(path: pathlib.Path) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16)


def iter_frames(path: pathlib.Path, width: int, height: int):
    """Decoded BGR frames, one at a time, straight from FFmpeg (nothing written to disk)."""
    size = width * height * 3
    proc = subprocess.Popen([FFMPEG, "-loglevel", "error", "-i", str(path), "-f", "rawvideo",
                             "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, bufsize=size * 2)
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait()


# --------------------------------------------------------------------------- #
# Flash frames: 1-3 frames of another shot inside the source video
# --------------------------------------------------------------------------- #

THUMB_W, THUMB_H = 64, 36


def flash_max_frames() -> int:
    """FLASH_FRAMES_MAX (default 3): the longest run of frames treated as a flash; 0 = off."""
    try:
        return max(0, int(float(os.environ.get("FLASH_FRAMES_MAX") or 3)))
    except ValueError:
        return 3


def frame_thumbs(path: pathlib.Path) -> np.ndarray:
    """A tiny grey thumbnail of every frame, for spotting cuts (one fast decode)."""
    raw = subprocess.run([FFMPEG, "-loglevel", "error", "-i", str(path), "-vf",
                          f"scale={THUMB_W}:{THUMB_H}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, THUMB_H, THUMB_W)


def picture_change(thumbs: np.ndarray, a: int, b: int) -> float:
    return float(np.abs(thumbs[a].astype(np.int16) - thumbs[b].astype(np.int16)).mean())


def cut_threshold(thumbs: np.ndarray, min_jump: float = 25.0) -> float:
    """A picture change above this between two frames is a hard cut."""
    if len(thumbs) < 2:
        return min_jump
    diffs = np.abs(np.diff(thumbs.astype(np.int16), axis=0)).mean(axis=(1, 2))
    return max(min_jump, 6.0 * float(np.median(diffs)))


def flash_frames(thumbs: np.ndarray, max_len: int = 3, min_jump: float = 25.0) -> dict[int, int]:
    """Frames to replace: {frame: frame shown instead}.

    Remote-podcast edits often contain a single frame (or two or three) of another shot
    between cuts, such as the side-by-side layout or a zoomed-out view. In a vertical clip
    that reads as a jump, whatever the crop does. A run of at most max_len frames with a
    hard cut on both sides is such a flash; it shows the last frame before it instead
    (the first frame after it when it opens the clip). A 40-120 ms freeze is not visible,
    and the audio is untouched. A hard cut is a picture change of more than
    max(min_jump, 6 x the clip's median change) on the 64x36 grey thumbnails."""
    n = len(thumbs)
    if n < 3 or max_len <= 0:
        return {}
    diffs = np.abs(np.diff(thumbs.astype(np.int16), axis=0)).mean(axis=(1, 2))
    threshold = cut_threshold(thumbs, min_jump)
    cuts = [i + 1 for i, d in enumerate(diffs) if d > threshold]
    bounds = [0, *cuts, n]
    replace: dict[int, int] = {}
    for a, b in zip(bounds, bounds[1:]):
        if b - a > max_len or (a == 0 and b == n):
            continue
        for f in range(a, b):
            replace[f] = b if a == 0 else a - 1
    resolved = {}
    for f in sorted(replace):                       # a flash next to a flash: follow the chain
        target, seen = replace[f], {f}
        while target in replace and target not in seen:
            seen.add(target)
            target = replace[target]
        resolved[f] = target
    return {f: t for f, t in resolved.items() if 0 <= t < n and t not in replace}


class _Frame:
    """A shot boundary moved to another frame (find_tracks reads only frame_num)."""

    def __init__(self, frame_num: int):
        self.frame_num = frame_num


def drop_flash_cuts(scenes: list, flashes: dict[int, int], thumbs: np.ndarray | None = None) -> list:
    """Source shots once the flash frames are replaced.

    A cut that lands on a flash (into it or out of it) is checked against the picture on
    both sides of the flash: when the shot after it continues the shot before it (A,
    flash, A) the cut disappears; when it is another shot (A, flash, C) the cut stays, at
    the first frame after the flash, so a real camera change is still a hard boundary.
    (PySceneDetect reports at most one cut for A, flash, C, at either end of the flash.)"""
    if not flashes:
        return list(scenes)
    threshold = cut_threshold(thumbs) if thumbs is not None else None
    merged: list = []
    for scene in scenes:
        cut = int(scene[0].frame_num)
        touches = cut in flashes or (cut - 1 in flashes)
        if not merged or not touches:
            merged.append(scene)
            continue
        after = cut
        while after in flashes:                      # first frame after the flash run
            after += 1
        before = flashes.get(cut, flashes.get(cut - 1))
        same_shot = thumbs is None or after >= len(thumbs) or \
            picture_change(thumbs, before, after) <= threshold
        if same_shot:
            merged[-1] = (merged[-1][0], scene[1])
        else:
            merged[-1] = (merged[-1][0], _Frame(after))
            merged.append((_Frame(after), scene[1]))
    return merged


def iter_clean_frames(path: pathlib.Path, width: int, height: int, replace: dict[int, int] | None = None):
    """iter_frames with flash frames replaced (see flash_frames). Frame count is unchanged."""
    if not replace:
        yield from iter_frames(path, width, height)
        return
    lookahead = max([t - f for f, t in replace.items()] + [0])
    keep = max([f - t for f, t in replace.items()] + [0]) + 1
    recent: dict[int, np.ndarray] = {}
    next_out = 0
    last = -1
    for i, frame in enumerate(iter_frames(path, width, height)):
        recent[i] = frame
        last = i
        while next_out <= i and replace.get(next_out, next_out) <= i:
            yield recent[replace.get(next_out, next_out)]
            next_out += 1
        for k in [k for k in recent if k < next_out - keep - lookahead]:
            del recent[k]
    while next_out <= last:                          # a target past the end: keep the frame itself
        source = replace.get(next_out, next_out)
        yield recent.get(source, recent[next_out])
        next_out += 1


# --------------------------------------------------------------------------- #
# Speaker choice and 9:16 frames
# --------------------------------------------------------------------------- #

def frame_candidates(tracks: list[dict], scores: list, n_frames: int) -> list[list[dict]]:
    """Every tracked face in every frame, with its speaking score averaged over +-30 frames."""
    faces = [[] for _ in range(n_frames)]
    for tidx, track in enumerate(tracks):
        score_array = scores[tidx]
        for fidx, frame in enumerate(track["track"]["frame"].tolist()):
            if frame >= n_frames:
                continue
            score_slice = score_array[max(fidx - SMOOTH_FRAMES, 0):min(fidx + SMOOTH_FRAMES, len(score_array))]
            avg_score = float(np.mean(score_slice) if len(score_slice) > 0 else 0)
            faces[frame].append({"track": tidx, "score": avg_score, "s": track["proc_track"]["s"][fidx],
                                 "x": track["proc_track"]["x"][fidx], "y": track["proc_track"]["y"][fidx]})
    return faces


def choose_faces(tracks: list[dict], scores: list, n_frames: int) -> list[dict | None]:
    """For each frame, the face to follow (or None), exactly as the old create_vertical_video:
    the face whose score averaged over +-30 frames is highest, if that score is >= 0."""
    chosen = []
    for current in frame_candidates(tracks, scores, n_frames):
        best = max(current, key=lambda face: face["score"]) if current else None
        chosen.append(best if best and best["score"] >= 0 else None)
    return chosen


# --------------------------------------------------------------------------- #
# Steady speaker switching
# --------------------------------------------------------------------------- #

def min_hold_frames() -> int:
    """SPEAKER_MIN_HOLD_SECONDS (default 0.5 s) in frames; 0 turns the hold off."""
    try:
        seconds = float(os.environ.get("SPEAKER_MIN_HOLD_SECONDS") or 0.5)
    except ValueError:
        seconds = 0.5
    return max(0, math.ceil(seconds * FPS - 1e-9))


def scene_ranges(scenes: list, n_frames: int) -> list[tuple[int, int]]:
    """Source shots as [start, end) frame ranges covering 0..n_frames (cuts are hard boundaries)."""
    cuts = sorted({int(s[0].frame_num) for s in scenes if 0 < int(s[0].frame_num) < n_frames})
    bounds = [0, *cuts, n_frames]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


def _runs(labels: list, start: int, end: int) -> list[list]:
    runs: list[list] = []
    for i in range(start, end):
        if runs and runs[-1][0] == labels[i]:
            runs[-1][2] = i + 1
        else:
            runs.append([labels[i], i, i + 1])
    return runs


def steady_faces(candidates: list[list[dict]], chosen: list[dict | None], ranges: list[tuple[int, int]],
                 min_hold: int) -> tuple[list[dict | None], dict]:
    """Remove crop changes shorter than min_hold frames inside each source shot.

    The per-frame choice (follow the face with the best speaking score, or show the whole
    frame) can flip for a few frames when two people score about the same, or drop to the
    whole frame for a frame where no face is tracked. Inside each shot, the shortest
    stretch below min_hold is absorbed into a neighbouring stretch, repeatedly:
    - a neighbour whose face is tracked over those frames keeps following that face (if
      both neighbours qualify, the one with the higher speaking score there wins);
    - the whole frame (no face) can always absorb a stretch;
    - otherwise the neighbour's crop is held where it was (the face was briefly lost).
    Source cuts are never crossed, so a cut to another camera still switches at once.
    Returns the new per-frame choice and counts for the log."""
    labels = [face["track"] if face else None for face in chosen]
    out = list(chosen)
    stats = {"shortStretches": 0, "framesChanged": 0}
    if min_hold <= 1:
        return out, stats
    by_track = [{c["track"]: c for c in frame} for frame in candidates]

    for start, end in ranges:
        while True:
            runs = _runs(labels, start, end)
            short = [r for r in runs if r[2] - r[1] < min_hold]
            if len(runs) < 2 or not short:
                break
            label, a, b = min(short, key=lambda r: (r[2] - r[1], r[1]))
            k = next(i for i, r in enumerate(runs) if r[1] == a)
            neighbours = [runs[i] for i in (k - 1, k + 1) if 0 <= i < len(runs)]
            options = []
            for nb in neighbours:
                target = nb[0]
                if target is None:
                    options.append((0.0, nb, "fit"))
                elif all(target in by_track[f] for f in range(a, b)):
                    score = float(np.mean([by_track[f][target]["score"] for f in range(a, b)]))
                    options.append((score, nb, "follow"))
            if options:
                _, nb, how = max(options, key=lambda o: o[0])
                for f in range(a, b):
                    out[f] = None if how == "fit" else by_track[f][nb[0]]
                    labels[f] = nb[0]
            else:
                # No neighbour can follow its face here: hold the closest crop still.
                nb = neighbours[0]
                edge = nb[2] - 1 if nb[2] <= a else nb[1]
                held = out[edge]
                for f in range(a, b):
                    out[f] = dict(held, held=True) if held else None
                    labels[f] = nb[0]
            stats["shortStretches"] += 1
            stats["framesChanged"] += b - a
    return out, stats


def count_switches(chosen: list[dict | None]) -> int:
    """How often the crop changes target: to another person, or between a person and the whole frame."""
    labels = [face.get("track") if face else None for face in chosen]
    return sum(1 for prev, cur in zip(labels, labels[1:]) if prev != cur)


def crop_frame(img: np.ndarray, face_x: float) -> np.ndarray:
    """Full-height 9:16 window centred on the speaker (the old 'crop' mode). The old code
    scaled the whole frame up to 1920 px high and then cut 1080 px out of it; this
    computes only the 1080 px it keeps, with the same pixel mapping."""
    import cv2

    height, width = img.shape[:2]
    scale = TARGET_H / height
    frame_width = int(round(width * scale))
    center_x = int(face_x * scale)
    top_x = max(min(center_x - TARGET_W // 2, frame_width - TARGET_W), 0)
    if scale < 1 or frame_width < TARGET_W:    # downscaling or a narrow source: the old way
        resized = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        out = resized[0:TARGET_H, top_x:top_x + TARGET_W]
        if out.shape[:2] != (TARGET_H, TARGET_W):
            out = cv2.resize(out, (TARGET_W, TARGET_H), interpolation=cv2.INTER_AREA)
        return np.ascontiguousarray(out)
    matrix = np.float32([[scale, 0, 0.5 * scale - 0.5 - top_x], [0, scale, 0.5 * scale - 0.5]])
    return cv2.warpAffine(img, matrix, (TARGET_W, TARGET_H), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def fit_frame(img: np.ndarray) -> np.ndarray:
    """The whole frame, fitted to the width over a blurred fill (the old 'resize' mode).
    The background is blurred at quarter size: with a 121 px kernel the result looks
    the same and costs a fraction of the time."""
    import cv2

    height, width = img.shape[:2]
    scale = TARGET_W / width
    resized_height = int(height * scale)
    resized = cv2.resize(img, (TARGET_W, resized_height), interpolation=cv2.INTER_AREA)

    scale_bg = max(TARGET_W / width, TARGET_H / height)
    bg_w, bg_h = int(width * scale_bg), int(height * scale_bg)
    q = 4
    small = cv2.resize(img, (max(1, round(bg_w / q)), max(1, round(bg_h / q))), interpolation=cv2.INTER_AREA)
    sigma = 0.3 * ((BLUR_KERNEL - 1) * 0.5 - 1) + 0.8     # OpenCV's sigma for a 121 px kernel
    small = cv2.GaussianBlur(small, (0, 0), sigma / q)
    crop_x, crop_y = (bg_w - TARGET_W) // 2, (bg_h - TARGET_H) // 2
    x0, y0 = crop_x / q, crop_y / q
    matrix = np.float32([[q, 0, -x0 * q + 0.5 * q - 0.5], [0, q, -y0 * q + 0.5 * q - 0.5]])
    background = cv2.warpAffine(small, matrix, (TARGET_W, TARGET_H), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REPLICATE)

    if resized_height > TARGET_H:              # taller than 9:16: keep the middle
        top = (resized_height - TARGET_H) // 2
        return np.ascontiguousarray(resized[top:top + TARGET_H])
    center_y = (TARGET_H - resized_height) // 2
    background[center_y:center_y + resized_height, :] = resized
    return background


def vertical_frame(img: np.ndarray, face: dict | None) -> np.ndarray:
    return crop_frame(img, face["x"]) if face else fit_frame(img)


# --------------------------------------------------------------------------- #
# Subtitles
# --------------------------------------------------------------------------- #

def write_subtitles(words: list, clip_start: float, clip_end: float, path: pathlib.Path,
                    max_words: int = SUBTITLE_MAX_WORDS) -> int:
    """Word-timed ASS subtitles, max 5 words per line, Anton 140 px (unchanged style)."""
    import pysubs2

    lines, current, line_start, line_end = [], [], None, None
    for segment in words:
        word = (segment.get("word") or "").strip()
        seg_start, seg_end = segment.get("start"), segment.get("end")
        if not word or seg_start is None or seg_end is None:
            continue
        if not (seg_end > clip_start and seg_start < clip_end):
            continue
        start_rel, end_rel = max(0.0, seg_start - clip_start), max(0.0, seg_end - clip_start)
        if end_rel <= 0:
            continue
        if not current:
            current, line_start, line_end = [word], start_rel, end_rel
        elif len(current) >= max_words:
            lines.append((line_start, line_end, " ".join(current)))
            current, line_start, line_end = [word], start_rel, end_rel
        else:
            current.append(word)
            line_end = end_rel
    if current:
        lines.append((line_start, line_end, " ".join(current)))

    subs = pysubs2.SSAFile()
    subs.info.update({"WrapStyle": 0, "ScaledBorderAndShadow": "yes", "PlayResX": TARGET_W,
                      "PlayResY": TARGET_H, "ScriptType": "v4.00+"})
    style = pysubs2.SSAStyle()
    style.fontname, style.fontsize = "Anton", 140
    style.primarycolor = pysubs2.Color(255, 255, 255)
    style.outline, style.shadow = 2.0, 2.0
    style.shadowcolor = pysubs2.Color(0, 0, 0, 128)
    style.alignment = 2
    style.marginl = style.marginr = style.marginv = 50
    style.spacing = 0.0
    subs.styles["Default"] = style
    for start, end, text in lines:
        subs.events.append(pysubs2.SSAEvent(start=pysubs2.make_time(s=start), end=pysubs2.make_time(s=end),
                                            text=text, style="Default"))
    subs.save(str(path))
    return len(lines)


def audio_fade(duration: float) -> str:
    """A short fade in and out, so a clip never starts or stops on a click."""
    duration = max(duration, 1.0)
    fade_out = min(0.35, duration / 4)
    return f"afade=t=in:st=0:d=0.12,afade=t=out:st={duration - fade_out:.2f}:d={fade_out:.2f}"


# --------------------------------------------------------------------------- #
# One clip
# --------------------------------------------------------------------------- #

def detect_pass(models, segment: pathlib.Path, width: int, height: int, conf_th: float, tick,
                replace: dict[int, int] | None = None) -> list[list[dict]]:
    faces, batch = [], []

    def flush():
        results = fast_asd.detect_faces_batch(models.s3fd, np.stack(batch), width, height, conf_th, models.device)
        for bboxes in results:
            faces.append(fast_asd.frame_detections(len(faces), bboxes))
        batch.clear()
        tick(len(faces))

    for frame in iter_clean_frames(segment, width, height, replace):
        batch.append(fast_asd.s3fd_input(frame))
        if len(batch) >= DETECT_BATCH:
            flush()
    if batch:
        flush()
    return faces


def crop_pass(segment: pathlib.Path, width: int, height: int, tracks: list[dict],
              replace: dict[int, int] | None = None) -> list[tuple[dict, np.ndarray]]:
    """Smoothed boxes and the grey face crops of every track, in one decode of the clip."""
    smoothed = [fast_asd.smooth_track(track) for track in tracks]
    wanted: dict[int, list[tuple[int, int]]] = {}
    for tidx, track in enumerate(tracks):
        for fidx, frame in enumerate(track["frame"].tolist()):
            wanted.setdefault(frame, []).append((tidx, fidx))
    crops = [[None] * len(track["frame"]) for track in tracks]
    for frame_index, frame in enumerate(iter_clean_frames(segment, width, height, replace)):
        for tidx, fidx in wanted.get(frame_index, ()):
            crops[tidx][fidx] = fast_asd.face_crop(frame, smoothed[tidx], fidx)
    out = []
    for track, dets, faces in zip(tracks, smoothed, crops):
        faces = [f for f in faces if f is not None]
        out.append(({"track": track, "proc_track": dets}, np.array(faces)))
    return out


def encode_pass(segment: pathlib.Path, width: int, height: int, chosen: list, subtitles: pathlib.Path,
                duration: float, out: pathlib.Path, encoder: str, tick, replace: dict[int, int] | None = None) -> None:
    cmd = [FFMPEG, "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{TARGET_W}x{TARGET_H}", "-r", str(FPS), "-i", "-",
           "-i", str(segment), "-map", "0:v:0", "-map", "1:a:0?",
           "-vf", f"ass={subtitles}", "-af", audio_fade(duration),
           *ENCODERS[encoder]["final"], "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    log_path = out.with_suffix(".ffmpeg.log")
    with open(log_path, "w") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
        try:
            for index, frame in enumerate(iter_clean_frames(segment, width, height, replace)):
                face = chosen[index] if index < len(chosen) else None
                proc.stdin.write(vertical_frame(frame, face).tobytes())
                if index % 50 == 0:
                    tick(index)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        code = proc.wait()
    if code != 0:
        raise RenderError(f"encoding failed ({encoder}): {log_path.read_text()[-600:]}")


def render_clip(models, source: pathlib.Path, start: float, end: float, words: list, out_path: pathlib.Path,
                work_dir: pathlib.Path, encoder: str = "nvenc", progress=None, conf_th: float = fast_asd.CONF_TH,
                debug_dir: pathlib.Path | None = None) -> dict:
    """Render source[start:end] as a 1080x1920 clip with subtitles at out_path.
    `progress(fraction)` is called as the work advances. Returns timings and counts."""
    work_dir.mkdir(parents=True, exist_ok=True)
    duration = end - start
    timings: dict[str, float] = {}
    done = {"cut": 0.0, "detect": 0.0, "score": 0.0, "render": 0.0}

    def report(step: str, fraction: float):
        done[step] = max(0.0, min(1.0, fraction))
        if progress:
            progress(sum(STEP_WEIGHTS[k] * v for k, v in done.items()))

    t = time.time()
    segment = work_dir / "segment.mkv"
    try:
        cut_segment(source, start, duration, segment, encoder)
    except RenderError:
        if encoder != "nvenc":
            raise
        encoder = "x264"
        cut_segment(source, start, duration, segment, encoder)
    width, height = video_size(segment)
    audio = read_audio_16k(segment)
    thumbs = frame_thumbs(segment)
    flashes = flash_frames(thumbs, flash_max_frames())
    expected_frames = max(1, int(round(duration * FPS)))
    timings["cut"] = time.time() - t
    report("cut", 1)

    t = time.time()
    scenes = drop_flash_cuts(fast_asd.scene_detect(str(segment)), flashes, thumbs)
    faces = detect_pass(models, segment, width, height, conf_th,
                        lambda n: report("detect", n / expected_frames), flashes)
    n_frames = len(faces)
    timings["detect"] = time.time() - t
    report("detect", 1)

    t = time.time()
    tracks = fast_asd.find_tracks(scenes, faces)
    cropped = crop_pass(segment, width, height, tracks, flashes)
    vid_tracks, scores = [], []
    for k, (vid_track, face_crops) in enumerate(cropped):
        vid_tracks.append(vid_track)
        scores.append(fast_asd.score_track(models, fast_asd.track_audio(audio, vid_track["track"]), face_crops))
        report("score", (k + 1) / max(1, len(cropped)))
    if debug_dir is not None:
        fast_asd.save_debug(debug_dir, vid_tracks, scores)
    raw_chosen = choose_faces(vid_tracks, scores, n_frames)
    chosen, steady = steady_faces(frame_candidates(vid_tracks, scores, n_frames), raw_chosen,
                                  scene_ranges(scenes, n_frames), min_hold_frames())
    timings["score"] = time.time() - t
    report("score", 1)

    t = time.time()
    subtitles = work_dir / "subtitles.ass"
    write_subtitles(words, start, end, subtitles)
    tmp_out = work_dir / "clip.mp4"
    tick = lambda i: report("render", i / max(1, n_frames))  # noqa: E731
    try:
        encode_pass(segment, width, height, chosen, subtitles, duration, tmp_out, encoder, tick, flashes)
    except RenderError:
        if encoder != "nvenc":
            raise
        print("NVENC encode failed, retrying with libx264")
        encoder = "x264"
        encode_pass(segment, width, height, chosen, subtitles, duration, tmp_out, encoder, tick, flashes)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_out), str(out_path))
    timings["render"] = time.time() - t
    report("render", 1)

    followed = sum(1 for face in chosen if face)
    return {"timings": {k: round(v, 2) for k, v in timings.items()}, "frames": n_frames, "tracks": len(vid_tracks),
            "speakerFrames": followed, "encoder": encoder, "chosen": chosen, "rawChosen": raw_chosen,
            "switches": {"before": count_switches(raw_chosen), "after": count_switches(chosen),
                         "sourceCuts": max(0, len(scene_ranges(scenes, n_frames)) - 1), **steady,
                         "flashFrames": len(flashes)}}


def chosen_summary(chosen: list) -> list:
    """Per-frame crop decision as (mode, centre x) - what parity checks compare."""
    return [("crop", round(float(face["x"]), 1)) if face else ("fit", None) for face in chosen]


def offset_samples(n_new: int, max_offset: int = 2, samples: int = 20) -> list[int]:
    """The new-clip frame indices frame_offset() compares."""
    return list(range(max_offset, n_new - max_offset, max(1, (n_new - 2 * max_offset) // samples)))


def frame_offset(get_old, n_old: int, new_frames: dict[int, np.ndarray], max_offset: int = 2) -> int:
    """How many frames the old clip leads by: old[i + offset] shows the same picture as new[i].
    The old cut (seek after decoding, then a 25 fps AVI) can start a frame earlier.
    `get_old(i)` returns old frame i; `new_frames` holds the new frames at offset_samples()."""
    best, best_diff = 0, None
    for offset in sorted(range(-max_offset, max_offset + 1), key=abs):
        diffs = [np.abs(get_old(i + offset).astype(np.int16) - frame.astype(np.int16)).mean()
                 for i, frame in new_frames.items() if 0 <= i + offset < n_old]
        if diffs and (best_diff is None or np.mean(diffs) < best_diff):
            best, best_diff = offset, float(np.mean(diffs))
    return best


def compare_decisions(old: list, new: list, source_height: int, offset: int = 0) -> dict:
    """Old vs new per-frame crop decisions (chosen_summary lists), old[i + offset] vs new[i].
    Crop centre shifts are in output pixels (the 9:16 clip is 1080 px wide)."""
    a, b = (old[offset:], new) if offset >= 0 else (old, new[-offset:])
    n = min(len(a), len(b))
    same = sum(1 for x, y in zip(a[:n], b[:n]) if x[0] == y[0])
    shifts = [abs(x[1] - y[1]) * TARGET_H / source_height for x, y in zip(a[:n], b[:n]) if x[0] == y[0] == "crop"]
    return {"frameOffset": offset, "framesCompared": n, "sameDecision": round(same / max(n, 1), 4),
            "meanShiftPx": round(sum(shifts) / len(shifts), 2) if shifts else 0.0,
            "maxShiftPx": round(max(shifts), 1) if shifts else 0.0,
            "framesShiftedOver10Px": sum(1 for s in shifts if s > 10)}


def differing_segments(old: list, new: list, source_height: int, offset: int = 0, min_shift_px: float = 10.0,
                       merge_gap: int = 5) -> list[dict]:
    """Stretches of frames where old[i + offset] and new[i] crop differently: one follows a
    speaker and the other shows the whole frame, or both follow a speaker more than
    min_shift_px (output pixels) apart, which means different people. Gaps of up to
    merge_gap frames are merged. Frame numbers are the new clip's."""
    a, b = (old[offset:], new) if offset >= 0 else (old, new[-offset:])
    start = -offset if offset < 0 else 0
    segments: list[dict] = []
    for i, (x, y) in enumerate(zip(a, b)):
        if x[0] != y[0]:
            kind = "old follows a speaker, new shows the whole frame" if x[0] == "crop" else \
                "old shows the whole frame, new follows a speaker"
        elif x[0] == "crop" and abs(x[1] - y[1]) * TARGET_H / source_height > min_shift_px:
            kind = "different person"
        else:
            continue
        frame = i + start
        last = segments[-1] if segments else None
        if last and last["kind"] == kind and frame - last["endFrame"] <= merge_gap + 1:
            last["endFrame"] = frame
        else:
            segments.append({"kind": kind, "startFrame": frame, "endFrame": frame})
    for seg in segments:
        mid = (seg["startFrame"] + seg["endFrame"]) // 2
        seg["midFrame"] = mid
        seg["frames"] = seg["endFrame"] - seg["startFrame"] + 1
        seg["old"], seg["new"] = a[mid - start], b[mid - start]
    return segments


__all__ = ["render_clip", "probe_encoder", "frame_thumbs", "flash_frames", "iter_clean_frames",
           "drop_flash_cuts", "choose_faces", "frame_candidates", "steady_faces", "scene_ranges",
           "count_switches", "min_hold_frames", "write_subtitles", "audio_fade", "chosen_summary",
           "frame_offset", "compare_decisions", "differing_segments", "RenderError"]
