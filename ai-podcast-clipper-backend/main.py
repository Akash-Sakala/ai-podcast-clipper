"""
AI Podcast Clipper - Modal backend (English).

Deploy once with `modal deploy main.py`. The local runner (runner.py) puts the
video into the `ai-podcast-clipper-jobs` Modal Volume and calls `process_job`:

    download_video (CPU)                                 YouTube videos: yt-dlp straight into the volume
                                                         (an uploaded MP4 is sent by runner.py instead)
    process_job (CPU)    Transcriber.transcribe (GPU)    Parakeet TDT 0.6B v2, whole video
                    ->   moments.select_moments          Gemini ranks the moments (no GPU waiting)
                    ->   Renderer.render x N (GPU)       one container per clip, all at once:
                                                         LR-ASD speaker crop, subtitles, one encode

transcript="parakeet"  full run (above).
transcript="reuse"     re-rank & re-render: reuse the Parakeet transcript of an earlier run.
transcript="uploaded"  legacy jobs from the removed Whistle engine: reuse their transcript,
                       re-transcribe each chosen clip with Parakeet for accurate subtitles.

Volume layout (per job):
    /<job_id>/input.mp4          downloaded by download_video, or uploaded by runner.py
    /<job_id>/transcript.json    word-level transcript (Parakeet)
    /<job_id>/moments.json       ranked clip moments chosen by Gemini (best first)
    /<job_id>/clips/clip_N.mp4   finished vertical clips with subtitles (clip_0 = best)

runner.py removes /<job_id> as soon as the clips are on the laptop: no video stays in the cloud.
"""

import json
import os
import pathlib
import shutil
import subprocess
import time

import modal

import costs
import moments as moment_selection
import settings

APP_NAME = "ai-podcast-clipper"
DEFAULT_MAX_CLIPS = 5
GPU = settings.gpu_spec()                    # .env CLIPPER_GPU, read at deploy time
PARAKEET_DIR = "/opt/parakeet-tdt-0.6b-v2"
TRANSCRIBER_SCALEDOWN_S = 10
RENDERER_SCALEDOWN_S = 5
RENDER_MEMORY_MB = 4096
DOWNLOAD_CPU = 2.0
DOWNLOAD_MEMORY_MB = 2048
DOWNLOAD_SCALEDOWN_S = 5
POLL_SECONDS = 2

image = (modal.Image.from_registry(
    "nvidia/cuda:12.4.0-devel-ubuntu22.04", add_python="3.12")
    .apt_install(["ffmpeg", "libgl1-mesa-glx", "wget"])
    .pip_install_from_requirements("requirements.txt")
    .run_commands(["mkdir -p /usr/share/fonts/truetype/custom",
                   "wget -O /usr/share/fonts/truetype/custom/Anton-Regular.ttf https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
                   "fc-cache -f -v"])
    # Bake Parakeet v2 (fp32 ONNX, ~2.5 GB) into the image; add HF_TOKEN to the
    # secret if Hugging Face rate-limits the build (HTTP 429).
    .add_local_file("download_parakeet.py", "/opt/download_parakeet.py", copy=True)
    .run_commands(f"python /opt/download_parakeet.py {PARAKEET_DIR}",
                  secrets=[modal.Secret.from_name("ai-podcast-clipper-secret")])
    .env({
        "PARAKEET_MODEL_DIR": PARAKEET_DIR,
        "ASD_DIR": "/asd",
        # cuDNN 9 / cuBLAS 12.8 from the nvidia-* wheels that torch installs (used by ONNX Runtime too)
        "LD_LIBRARY_PATH": "/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:"
                           "/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:"
                           "/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
    })
    .add_local_dir("asd", "/asd", copy=True)
    .add_local_python_source("moments", "settings", "audio_utils", "parakeet_transcriber", "render", "costs")
    .add_local_dir("prompts", "/root/prompts"))

# YouTube downloads run on a small CPU-only image: no GPU waits on the download, and
# bumping YTDLP_VERSION rebuilds only this image. ffmpeg merges the video and audio
# streams; Deno is the JavaScript runtime yt-dlp needs for YouTube.
download_image = (modal.Image.debian_slim(python_version="3.12")
                  .apt_install(["ffmpeg"])
                  .pip_install(f"yt-dlp[default]=={settings.YTDLP_VERSION}", f"deno=={settings.DENO_VERSION}")
                  .add_local_python_source("moments", "settings", "costs", "downloader"))

# Heavy ML libraries only exist inside the containers, so they are imported inside
# the functions that use them: your laptop only needs `pip install modal` to deploy.
app = modal.App(APP_NAME, image=image)

# Exchange folder between your laptop and the containers.
jobs_volume = modal.Volume.from_name("ai-podcast-clipper-jobs", create_if_missing=True)
secret = modal.Secret.from_name("ai-podcast-clipper-secret")
# Ranking settings from .env (MIN_CLIPS, CLIP_MIN/MAX_SECONDS, GEMINI_MODELS, ...).
settings_secret = modal.Secret.from_dict(settings.CONTAINER_SETTINGS)

JOBS_PATH = pathlib.Path("/jobs")
# Live progress for runner.py / the web app: {job_id: {"stage", "done", "total", "fraction", "etaSeconds"}}
# and, while rendering, {"<job_id>:clip<N>": fraction done} from each render container.
progress_dict = modal.Dict.from_name("ai-podcast-clipper-progress", create_if_missing=True)


def report_progress(key: str, value) -> None:
    try:
        progress_dict.put(key, value)
    except Exception as exc:  # noqa: BLE001 - progress is best effort
        print(f"Could not report progress: {exc!r}")


def gpu_name() -> str | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        return out.splitlines()[0] if out else None
    except Exception:  # noqa: BLE001
        return None


def read_audio(media_path: pathlib.Path):
    """Decode any media file to 16 kHz mono float32 samples with ffmpeg."""
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(media_path), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


class UsageTracker:
    """What a GPU container reports for the cost estimate (see costs.py)."""

    def __init__(self, enter_started: float, cpu: float, memory_gib: float, scaledown: float):
        self.enter_seconds = time.time() - enter_started
        self.calls = 0
        self.base = {"container": os.environ.get("MODAL_TASK_ID"), "gpu": gpu_name(), "cpu": cpu,
                     "memoryGiB": memory_gib, "scaledown": scaledown}

    def report(self, started: float) -> dict:
        self.calls += 1
        return {**self.base, "seconds": round(time.time() - started, 2),
                "enterSeconds": round(self.enter_seconds, 2) if self.calls == 1 else 0.0}


# --------------------------------------------------------------------------- #
# YouTube download (CPU), straight into the jobs volume
# --------------------------------------------------------------------------- #

@app.function(image=download_image, cpu=DOWNLOAD_CPU, memory=DOWNLOAD_MEMORY_MB, timeout=1800, retries=0,
              scaledown_window=DOWNLOAD_SCALEDOWN_S, volumes={str(JOBS_PATH): jobs_volume})
def download_video(job_id: str, url: str) -> dict:
    """Download a YouTube video to /<job_id>/input.mp4 in the jobs volume. Never raises:
    {"ok": False, "error", "blocked"} tells runner.py to download on the laptop instead."""
    import downloader

    started = time.time()
    usage = {"container": os.environ.get("MODAL_TASK_ID"), "gpu": None, "cpuOnly": True, "cpu": DOWNLOAD_CPU,
             "memoryGiB": DOWNLOAD_MEMORY_MB / 1024, "scaledown": DOWNLOAD_SCALEDOWN_S, "enterSeconds": 0.0}
    work_dir = pathlib.Path("/tmp") / job_id
    last = [0.0]

    def progress(fraction: float) -> None:
        if time.time() - last[0] >= 1.5:
            last[0] = time.time()
            report_progress(job_id, {"stage": "downloading", "where": "cloud", "fraction": round(fraction, 3)})

    def retry(attempt: int, attempts: int, exc: Exception) -> None:
        print(f"Download attempt {attempt} failed ({exc}); retrying")
        report_progress(job_id, {"stage": "downloading", "where": "cloud", "retry": attempt})

    report_progress(job_id, {"stage": "downloading", "where": "cloud"})
    try:
        info = downloader.download(url, work_dir, on_progress=progress, on_retry=retry)
        downloaded = time.time() - started
        target = JOBS_PATH / job_id / "input.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work_dir / "input.mp4", target)
        jobs_volume.commit()
        size_mb = target.stat().st_size / 1024 / 1024
        print(f"Downloaded {url} ({size_mb:.0f} MB) in {downloaded:.0f}s, saved in {time.time() - started:.0f}s")
        return {"ok": True, "meta": downloader.video_meta(info), "sizeMB": round(size_mb, 1),
                "seconds": round(time.time() - started, 1),
                "usage": {**usage, "seconds": round(time.time() - started, 2)}}
    except Exception as exc:  # noqa: BLE001 - the laptop takes over
        message = f"{type(exc).__name__}: {exc}"
        print(f"Download failed: {message}")
        return {"ok": False, "error": message[:2000], "blocked": downloader.is_blocked(message),
                "seconds": round(time.time() - started, 1),
                "usage": {**usage, "seconds": round(time.time() - started, 2)}}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Transcription (GPU)
# --------------------------------------------------------------------------- #

@app.cls(gpu=GPU, timeout=1800, retries=0, scaledown_window=TRANSCRIBER_SCALEDOWN_S,
         secrets=[secret], volumes={str(JOBS_PATH): jobs_volume})
class Transcriber:
    @modal.enter()
    def load(self):
        import parakeet_transcriber

        enter_started = time.time()
        self.parakeet = parakeet_transcriber.ParakeetEngine(parakeet_transcriber.default_model_dir(), device="cuda")
        self.usage = UsageTracker(enter_started, cpu=0.125, memory_gib=0.125, scaledown=TRANSCRIBER_SCALEDOWN_S)

    @modal.method()
    def transcribe(self, job_id: str) -> dict:
        started = time.time()
        jobs_volume.reload()
        job_dir = JOBS_PATH / job_id
        audio = read_audio(job_dir / "input.mp4")
        words = self.parakeet.transcribe(audio)
        (job_dir / "transcript.json").write_text(json.dumps(words))
        jobs_volume.commit()
        took = time.time() - started
        audio_s = len(audio) / 16000
        return {"words": words, "seconds": round(took, 1),
                "stats": {"audioSeconds": round(audio_s, 1), "words": len(words),
                          "realTimeFactor": round(audio_s / max(took, 1e-6), 1)},
                "usage": self.usage.report(started)}

    @modal.method()
    def transcribe_ranges(self, job_id: str, ranges: list) -> dict:
        """Legacy Whistle jobs: Parakeet words for each (start, end) clip range."""
        started = time.time()
        jobs_volume.reload()
        audio = read_audio(JOBS_PATH / job_id / "input.mp4")
        out = []
        for start, end in ranges:
            piece = audio[int(start * 16000):int(end * 16000)]
            try:
                out.append(self.parakeet.transcribe(piece, offset_s=start))
            except Exception as exc:  # noqa: BLE001 - fall back to the uploaded transcript
                print(f"Re-transcribing {start}-{end} failed: {exc!r}")
                out.append(None)
        return {"words": out, "usage": self.usage.report(started)}


# --------------------------------------------------------------------------- #
# Rendering (GPU, one container per clip)
# --------------------------------------------------------------------------- #

class RenderWorker:
    """Models and encoder of one render container (also used by benchmark_gpu.py)."""

    def __init__(self):
        import render

        enter_started = time.time()
        self.models = render.fast_asd.Models("cuda")
        self.encoder = render.probe_encoder("nvenc")
        print(f"Renderer ready in {time.time() - enter_started:.1f}s (encoder: {self.encoder})")
        self.usage = UsageTracker(enter_started, cpu=float(settings.RENDER_CPU),
                                  memory_gib=RENDER_MEMORY_MB / 1024, scaledown=RENDERER_SCALEDOWN_S)

    def render(self, job_id: str, index: int, moment: dict, words: list) -> dict:
        import render

        started = time.time()
        jobs_volume.reload()
        name = f"clip_{index}.mp4"
        work_dir = pathlib.Path("/tmp") / job_id / f"clip_{index}"
        out_path = work_dir / name
        key, last = f"{job_id}:clip{index}", [0.0]

        def progress(fraction: float) -> None:
            if time.time() - last[0] >= 1.5 or fraction >= 1:
                last[0] = time.time()
                report_progress(key, round(fraction, 3))

        try:
            result = render.render_clip(self.models, JOBS_PATH / job_id / "input.mp4", float(moment["start"]),
                                        float(moment["end"]), words, out_path, work_dir, self.encoder, progress)
            print(f"Clip {index}: {result['timings']} ({result['frames']} frames, {result['tracks']} face "
                  f"tracks, {result['encoder']}, crop switches {result['switches']})")
            return {"index": index, "name": name, "data": out_path.read_bytes(), "timings": result["timings"],
                    "encoder": result["encoder"], "switches": result["switches"],
                    "usage": self.usage.report(started)}
        except Exception as exc:  # noqa: BLE001 - one failing clip does not stop the others
            print(f"Clip {index} failed: {exc!r}")
            return {"index": index, "name": name, "error": f"clip_{index}: {exc}",
                    "usage": self.usage.report(started)}
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)


@app.cls(gpu=GPU, timeout=1200, retries=0, scaledown_window=RENDERER_SCALEDOWN_S,
         cpu=float(settings.RENDER_CPU), memory=RENDER_MEMORY_MB, max_containers=settings.RENDER_MAX_CONTAINERS,
         secrets=[settings_secret], volumes={str(JOBS_PATH): jobs_volume})
class Renderer:
    @modal.enter()
    def load(self):
        self.worker = RenderWorker()

    @modal.method()
    def render(self, job_id: str, index: int, moment: dict, words: list) -> dict:
        return self.worker.render(job_id, index, moment, words)


# --------------------------------------------------------------------------- #
# The job (CPU): transcribe -> rank -> render in parallel
# --------------------------------------------------------------------------- #

def clip_words(words: list, start: float, end: float) -> list:
    return [w for w in words if w.get("end") is not None and w.get("start") is not None
            and w["end"] > start and w["start"] < end]


def render_in_parallel(job_id: str, clip_moments: list, words_per_clip: list) -> tuple[list, list, list, list]:
    """Spawn one render per clip, report combined progress, collect results in clip order."""
    total = len(clip_moments)
    renderer = Renderer()
    calls = {i: renderer.render.spawn(job_id, i, moment, words_per_clip[i]) for i, moment in enumerate(clip_moments)}
    results: dict[int, dict] = {}
    fractions = {i: 0.0 for i in calls}
    started = time.time()
    while len(results) < total:
        for i, call in calls.items():
            if i in results:
                continue
            try:
                results[i] = call.get(timeout=0)
                fractions[i] = 1.0
            except (TimeoutError, modal.exception.TimeoutError):
                try:
                    fractions[i] = max(fractions[i], float(progress_dict.get(f"{job_id}:clip{i}") or 0))
                except Exception:  # noqa: BLE001
                    pass
            except Exception as exc:  # noqa: BLE001 - e.g. the container crashed
                results[i] = {"index": i, "name": f"clip_{i}.mp4", "error": f"clip_{i}: {exc}"}
                fractions[i] = 1.0
        fraction = sum(fractions.values()) / total
        elapsed = time.time() - started
        eta = round(elapsed / fraction * (1 - fraction)) if fraction >= 0.15 and len(results) < total else None
        report_progress(job_id, {"stage": "rendering", "done": len(results), "total": total,
                                 "fraction": round(fraction, 3), "etaSeconds": eta})
        if len(results) < total:
            time.sleep(POLL_SECONDS)

    clips, errors, usages, timings = [], [], [], []
    clips_dir = JOBS_PATH / job_id / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    for i in range(total):
        result = results[i]
        if result.get("usage"):
            usages.append(result["usage"])
        if result.get("error"):
            errors.append(result["error"])
            continue
        (clips_dir / result["name"]).write_bytes(result["data"])
        clips.append(result["name"])
        timings.append(result.get("timings"))
    jobs_volume.commit()
    for i in range(total):
        try:
            progress_dict.pop(f"{job_id}:clip{i}")
        except Exception:  # noqa: BLE001
            pass
    return clips, errors, usages, timings


@app.function(timeout=3600, retries=0, secrets=[secret, settings_secret], volumes={str(JOBS_PATH): jobs_volume})
def process_job(job_id: str, max_clips: int = DEFAULT_MAX_CLIPS, context: dict | None = None,
                transcript: str = "parakeet", extra_usages: list | None = None) -> dict:
    """extra_usages: containers that ran for this job before it (download_video), so the
    job's cost includes them."""
    job_started = time.time()
    jobs_volume.reload()
    job_dir = JOBS_PATH / job_id
    if not (job_dir / "input.mp4").exists():
        raise FileNotFoundError(f"{job_dir / 'input.mp4'} not found in the jobs volume")
    timings, stats, usages = {}, {}, list(extra_usages or [])

    started = time.time()
    if transcript in ("uploaded", "reuse"):
        words = json.loads((job_dir / "transcript.json").read_text())
        print(f"Using the saved transcript ({len(words)} words, mode {transcript})")
    else:
        report_progress(job_id, {"stage": "transcribing"})
        result = Transcriber().transcribe.remote(job_id)
        words, stats = result["words"], result["stats"]
        usages.append(result["usage"])
        timings["transcribe"] = round(time.time() - started, 1)

    started = time.time()
    report_progress(job_id, {"stage": "ranking"})
    ranking: dict = {}
    client = moment_selection.make_client(os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL") or moment_selection.GEMINI_MODELS[0]
    clip_moments = moment_selection.select_moments(words, max_clips, client, model, context, report=ranking)
    (job_dir / "moments.json").write_text(json.dumps(clip_moments, indent=2))
    jobs_volume.commit()
    timings["select"] = round(time.time() - started, 1)

    words_per_clip = [clip_words(words, float(m["start"]), float(m["end"])) for m in clip_moments]
    subtitles = "parakeet"
    if transcript == "uploaded" and clip_moments:
        refined = Transcriber().transcribe_ranges.remote(
            job_id, [(float(m["start"]), float(m["end"])) for m in clip_moments])
        usages.append(refined["usage"])
        better = refined["words"]
        words_per_clip = [b if b else w for b, w in zip(better, words_per_clip)]
        n_refined = sum(1 for b in better if b)
        subtitles = "parakeet" if n_refined == len(better) else ("mixed" if n_refined else "whistle")

    started = time.time()
    clips, errors, render_usages, clip_timings = ([], [], [], []) if not clip_moments else \
        render_in_parallel(job_id, clip_moments, words_per_clip)
    usages.extend(render_usages)
    timings["render"] = round(time.time() - started, 1)

    cost = costs.estimate(usages, orchestrator_seconds=time.time() - job_started)
    gpus = sorted(cost["gpus"]) or [costs.gpu_label(GPU if isinstance(GPU, str) else GPU[0])]
    print(f"Job {job_id}: {len(clips)} clips, timings {timings}, estimated cost ${cost['usd']:.3f}")
    return {"clips": clips, "moments": clip_moments, "errors": errors, "subtitles": subtitles,
            "timings": timings, "transcription": stats, "gpu": ", ".join(gpus), "ranking": ranking,
            "cost": cost, "clipTimings": clip_timings}
