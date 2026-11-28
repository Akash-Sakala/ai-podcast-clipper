"""
The end-credit card: the last seconds of every clip from a YouTube video show where it
came from - the episode's YouTube thumbnail, its channel and title, and a bar showing
where in the episode the clip starts ("this clip starts at 34:12 of 1:42:05").

Laptop only (Pillow and the bundled Geist font; no redeploy for design changes). One
transparent 1080x1920 PNG per clip, the card drawn where it ends up; render.py lays it
over the clip's last END_CARD_SECONDS (4.5) inside the clip's one encode, rising gently into
place while the speaker keeps talking, and lifts the captions to just above it. Upload: /<job>/cards/
clip_N.png, next to the footage; a clip without its card renders exactly as before.
Settings: the app's "Credit card at the end" switch (settings.END_CARD, .env END_CARD).
"""

from __future__ import annotations

import io
import pathlib
import shutil
import urllib.request

import settings
from jobstore import Job, log
from replay import youtube_id

FONT_FILE = pathlib.Path(__file__).resolve().parent / "fonts" / "Geist-Variable.ttf"
W, H = 1080, 1920
# The card: inside the side margins, its bottom above the 450 px a long Reels/Shorts
# title can cover, the band the captions use (they move up above the card while it shows).
CARD_BOX = (60, 1030, 1020, 1452)
PAD = 32
THUMB_SIZE = (384, 216)
AMBER = (245, 165, 36, 255)
CARD_FILL = (14, 14, 18, 236)
THUMBNAIL_URLS = ("maxresdefault", "sddefault", "hqdefault")
FETCH_TIMEOUT_S = 10


def card_info(job: Job) -> dict | None:
    """What the card shows for this job: None unless it is a YouTube video and the card is on."""
    data = job.data
    if not settings.END_CARD or data.get("source") != "youtube" or not data.get("youtubeUrl"):
        return None
    meta = data.get("meta") or {}
    duration = meta.get("duration")
    return {
        "title": (meta.get("title") or data.get("title") or "").strip(),
        "channel": (meta.get("channel") or "YouTube").strip(),
        "duration": float(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
        "videoId": youtube_id(data.get("youtubeUrl")),
    }


def timecode(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _cover_16_9(image):
    """The middle 16:9 of an image (hqdefault and sddefault are 4:3 with black bars)."""
    w, h = image.size
    target = w * 9 / 16
    if h > target:
        top = (h - target) / 2
        return image.crop((0, int(top), w, int(top + target)))
    left = (w - h * 16 / 9) / 2
    return image.crop((int(left), 0, int(w - left), h))


def fetch_thumbnail(video_id: str | None, fallback: pathlib.Path | None = None, opener=None):
    """YouTube's own cover of the episode, largest first; else the job's thumbnail.jpg; else None."""
    from PIL import Image

    opener = opener or urllib.request.urlopen
    for name in THUMBNAIL_URLS if video_id else ():
        try:
            with opener(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", timeout=FETCH_TIMEOUT_S) as response:
                image = Image.open(io.BytesIO(response.read())).convert("RGB")
            if image.width >= 320:
                return _cover_16_9(image)
        except Exception:  # noqa: BLE001 - a missing size (404) or no network: try the next
            continue
    if fallback is not None and fallback.is_file():
        try:
            return _cover_16_9(Image.open(fallback).convert("RGB"))
        except Exception:  # noqa: BLE001
            return None
    return None


def _font(size: int, weight: str):
    from PIL import ImageFont

    font = ImageFont.truetype(str(FONT_FILE), size)
    try:
        font.set_variation_by_name(weight)
    except Exception:  # noqa: BLE001 - a FreeType without variable fonts: the default weight
        pass
    return font


def _fit(draw, text: str, font, width: float) -> str:
    """One line of at most `width` pixels, with an ellipsis when it had to be cut."""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip(" ,.;:-") + "…"


def _wrap(draw, text: str, font, width: float, max_lines: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for i, word in enumerate(words):
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
            continue
        lines.append(line)
        line = word
        if len(lines) == max_lines - 1:
            line = " ".join(words[i:])
            break
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    lines[-1] = _fit(draw, lines[-1], font, width)
    return lines


def _youtube_mark(draw, x: float, y: float, height: float = 34) -> None:
    draw.rounded_rectangle((x, y, x + height * 1.4, y + height), height * 0.28, fill=(255, 0, 0, 255))
    draw.polygon([(x + height * 0.55, y + height * 0.28), (x + height * 0.55, y + height * 0.72),
                  (x + height * 0.97, y + height * 0.5)], fill=(255, 255, 255, 255))


def _rounded(image, radius: int):
    from PIL import Image, ImageDraw

    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.size[0] - 1, image.size[1] - 1), radius, fill=255)
    out = image.convert("RGBA")
    out.putalpha(mask)
    return out


def draw_card(info: dict, thumbnail, clip_start: float):
    """The card for one clip as a transparent 1080x1920 RGBA image."""
    from PIL import Image, ImageDraw

    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    x0, y0, x1, y1 = CARD_BOX
    draw.rounded_rectangle(CARD_BOX, 40, fill=CARD_FILL)
    text_x = x0 + PAD
    if thumbnail is not None:
        canvas.alpha_composite(_rounded(thumbnail.resize(THUMB_SIZE, Image.LANCZOS), 18), (x0 + PAD, y0 + PAD))
        text_x = x0 + PAD + THUMB_SIZE[0] + PAD
    text_w = x1 - PAD - text_x

    channel_font, title_font, label_font = _font(30, "SemiBold"), _font(38, "Bold"), _font(28, "Medium")
    _youtube_mark(draw, text_x, y0 + 40)
    draw.text((text_x + 60, y0 + 38), _fit(draw, info["channel"], channel_font, text_w - 60), font=channel_font,
              fill=(230, 230, 235, 255))
    lines = _wrap(draw, info["title"] or "Full episode", title_font, text_w, 3 if thumbnail is not None else 2)
    y = y0 + 92
    for line in lines:
        draw.text((text_x, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += 48

    bar_x0, bar_x1, label_y = x0 + PAD, x1 - PAD, y0 + 282
    duration = info.get("duration")
    if duration and 0 <= clip_start < duration:
        label = f"Full episode on YouTube  ·  this clip starts at {timecode(clip_start)} of {timecode(duration)}"
    else:
        label = "Watch the full episode on YouTube"
    draw.text((bar_x0, label_y), _fit(draw, label, label_font, bar_x1 - bar_x0), font=label_font,
              fill=(210, 210, 215, 255))
    if duration and 0 <= clip_start < duration:
        bar_y = label_y + 52
        draw.rounded_rectangle((bar_x0, bar_y, bar_x1, bar_y + 10), 5, fill=(255, 255, 255, 70))
        at = bar_x0 + (bar_x1 - bar_x0) * clip_start / duration
        draw.rounded_rectangle((bar_x0, bar_y, max(bar_x0 + 10, at), bar_y + 10), 5, fill=AMBER)
        draw.ellipse((at - 13, bar_y - 8, at + 13, bar_y + 18), fill=AMBER)
    return canvas


def remote_card(job_id: str, index: int) -> str:
    return f"/{job_id}/cards/clip_{index}.png"


def make_cards(job: Job, moments: list, folder: pathlib.Path, thumbnail=None) -> list[tuple[pathlib.Path, str]]:
    """clip_N.png for every moment in `folder`, as (local path, volume path) pairs; [] when off."""
    info = card_info(job)
    if info is None or not moments:
        return []
    if thumbnail is None:
        thumbnail = fetch_thumbnail(info["videoId"], job.dir / "thumbnail.jpg")
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for index, moment in enumerate(moments):
        path = folder / f"clip_{index}.png"
        draw_card(info, thumbnail, float(moment.get("start") or 0)).save(path, optimize=True)
        out.append((path, remote_card(job.id, index)))
    return out


def cards_on_modal(job: Job, volume, moments: list, folder: pathlib.Path, upload) -> int:
    """Draw and upload the clips' end cards; returns how many. Never raises: without its card
    a clip simply renders as before. `upload(job, volume, files)` sends (local, remote) pairs."""
    try:
        files = make_cards(job, moments, folder)
        if files:
            upload(job, volume, files)
            log(f"End cards: {len(files)} uploaded")
        return len(files)
    except Exception as exc:  # noqa: BLE001
        log(f"End cards skipped: {type(exc).__name__}: {exc}")
        return 0
    finally:
        shutil.rmtree(folder, ignore_errors=True)
