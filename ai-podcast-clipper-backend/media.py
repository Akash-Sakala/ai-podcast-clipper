"""Laptop-side media helpers: video covers, clip posters, the upload fingerprint, deleting the
local video, and the flag that starts console programs without a window."""

import hashlib
import os
import pathlib
import subprocess
import time

from jobstore import Job, log

FINGERPRINT_CHUNK = 1024 * 1024                  # same as the web app's lib/fingerprint.ts


def no_window() -> dict:
    """subprocess keyword arguments that start a console program (ffmpeg, python -m modal)
    without a console window on Windows. The web app runs the runner with pythonw.exe,
    which has no console, so each console program would otherwise flash its own window.
    Every child process the runner starts passes this; yt-dlp and imageio-ffmpeg hide
    their own (STARTF_USESHOWWINDOW with SW_HIDE). Empty elsewhere."""
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def save_frame(video: pathlib.Path, target: pathlib.Path, at_seconds: float, video_filter: str) -> bool:
    """Write one JPEG frame of `video` to `target`. Never raises: a missing image
    only means the web app shows a plain placeholder."""
    import imageio_ffmpeg

    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
               "-ss", f"{at_seconds:.2f}", "-i", str(video), "-frames:v", "1",
               "-vf", video_filter, "-q:v", "4", str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60, **no_window())
        return target.exists()
    except Exception as exc:  # noqa: BLE001
        log(f"Could not save {target.name}: {exc}")
        return False


def save_thumbnail(job: Job, size: str = "320:180", video: pathlib.Path | None = None) -> bool:
    """Save a 16:9 frame from about 10% into input.mp4 (or `video`) as thumbnail.jpg (the cover in
    the web app's video list). YouTube jobs get YouTube's own cover from the web app."""
    import imageio_ffmpeg

    video = video or job.dir / "input.mp4"
    if not video.exists():
        return False
    try:
        reader = imageio_ffmpeg.read_frames(str(video))
        duration = float(next(reader).get("duration") or 0)
        reader.close()
    except Exception:  # noqa: BLE001 - fall back to the first frame
        duration = 0
    width, height = size.split(":")
    return save_frame(video, job.dir / "thumbnail.jpg", duration * 0.1,
                      f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}")


def save_clip_poster(clip: pathlib.Path) -> bool:
    """clip_N.mp4 -> clip_N.jpg: the still the clip card shows before the video loads."""
    return save_frame(clip, clip.with_suffix(".jpg"), 0.5, "scale=540:-2")


def fingerprint_ranges(size: int) -> list[tuple[int, int]]:
    if size <= 3 * FINGERPRINT_CHUNK:
        return [(0, size)]
    middle = size // 2 - FINGERPRINT_CHUNK // 2
    return [(0, FINGERPRINT_CHUNK), (middle, middle + FINGERPRINT_CHUNK), (size - FINGERPRINT_CHUNK, size)]


def file_fingerprint(path: pathlib.Path) -> str | None:
    """The web app's upload fingerprint (lib/fingerprint.ts): SHA-256 of "<size>:" plus
    1 MiB from the start, middle and end. Lets a re-added file be recognised after its
    copy in the job folder is gone."""
    try:
        size = path.stat().st_size
        digest = hashlib.sha256(f"{size}:".encode())
        with open(path, "rb") as f:
            for start, end in fingerprint_ranges(size):
                f.seek(start)
                digest.update(f.read(end - start))
        return digest.hexdigest()
    except OSError:
        return None


def discard_local_video(job: Job) -> float:
    """Delete input.mp4 (and any partial download) from the job folder once the clips
    are saved. Returns the MB freed. Never raises."""
    freed = 0
    for path in job.dir.glob("input.*"):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            for attempt in range(5):
                try:
                    path.unlink()
                    break
                except PermissionError:  # Windows: the web app or a player may have it open
                    if attempt == 4:
                        raise
                    time.sleep(0.5 * (attempt + 1))
            freed += size
        except OSError as exc:
            log(f"Could not delete {path.name}: {exc}")
    return freed / 1024 / 1024
