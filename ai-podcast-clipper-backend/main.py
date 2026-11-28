"""
AI Podcast Clipper - Modal backend (English).

Deploy once with `modal deploy main.py`. The local runner (runner.py) works in two phases:

    transcribe_and_rank (CPU)   phase 1, as soon as the audio is on the volume:
        Transcriber.transcribe (GPU)     Parakeet TDT 0.6B v2, the whole audio
        moments.select_moments           Gemini ranks the moments (no GPU waiting)
        moments.write_post_text          one more Gemini call: title, caption, hashtags per clip
    render_clips (CPU)          phase 2, once the laptop has uploaded each chosen clip's footage:
        Renderer.render x N (GPU)        one container per clip, all at once:
                                         LR-ASD speaker crop, subtitles, one encode

transcript="parakeet"  transcribe <media> (audio.m4a / audio.flac, or the whole input.mp4).
transcript="reuse"     re-rank & re-render: reuse the transcript.json the runner uploaded.
transcript="uploaded"  legacy jobs from the removed Whistle engine: reuse their transcript,
                       re-transcribe each chosen clip with Parakeet from input.mp4 for
                       accurate subtitles (returned as "clipWords" for render_clips).

Volume layout (per job):
    /<job_id>/audio.m4a          the video's audio (stream copy), or audio.flac: phase 1 input
    /<job_id>/input.mp4          the whole video: only when the runner falls back to uploading it
    /<job_id>/transcript.json    word-level transcript (Parakeet, or uploaded for re-rank)
    /<job_id>/moments.json       ranked clip moments chosen by Gemini (best first)
    /<job_id>/footage/clip_N.mp4 the footage of clip N (stream copy with the whole video's
                                 timestamps, see segments.py): phase 2 input
    /<job_id>/clips/clip_N.mp4   finished vertical clips with subtitles (clip_0 = best)

Contract with runner.py (keep it when changing these functions):
- Each Renderer writes its clip to /<job_id>/clips/clip_<index>.mp4 (index = rank, 0 = best)
  and commits the volume before it returns. Only if that write fails does it return the
  bytes, and render_clips writes them.
- The progress Dict entry <job_id> is {"stage", ...}: "transcribing" and "ranking" in phase 1;
  while rendering it also has "ready": [clip names already committed], so the runner
  downloads each clip while the others still render. Per-clip entries "<job_id>:clip<N>"
  hold a fraction (0-1).
- transcribe_and_rank returns {"moments", "ranking", "timings", "transcription", "subtitles",
  "clipWords", "gpu", "usages", "orchestratorSeconds", "orchestrator"}; render_clips returns
  {"clips", "errors", "timings", "clipTimings", "gpu", "cost", "usages", "orchestratorSeconds",
  "orchestrator"}. "usages" (one per container call: gpu, cpu, memoryGiB, seconds,
  enterSeconds, stage) are priced on the laptop (costs.estimate).

runner.py removes /<job_id> as soon as the clips are on the laptop: no video or audio stays
in the cloud. Volumes v1 is assumed (Modal's default when the volume was created): one
writer per file, commit in the writer, reload in the reader.
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
import segments
import settings
from clipper_names import APP_NAME, JOBS_VOLUME, PROGRESS_DICT, SECRET_NAME

DEFAULT_MAX_CLIPS = 5
GPU = settings.gpu_spec()                    # .env CLIPPER_GPU, read at deploy time
PARAKEET_DIR = "/opt/parakeet-tdt-0.6b-v2"
TRANSCRIBER_SCALEDOWN_S = 10
RENDERER_SCALEDOWN_S = 5
RENDER_MEMORY_MB = 4096
POLL_SECONDS = 2
# Resources requested for the CPU job and the Transcriber: Modal's defaults, spelled out so
# the cost estimate prices what is requested (it bills max(request, use); each container
# also reports what it used, see UsageTracker).
PROCESS_JOB_CPU = costs.MODAL_DEFAULT_CPU
PROCESS_JOB_MEMORY_MB = costs.MODAL_DEFAULT_MEMORY_MB
TRANSCRIBER_CPU = costs.MODAL_DEFAULT_CPU
TRANSCRIBER_MEMORY_MB = costs.MODAL_DEFAULT_MEMORY_MB

# Image pins (see requirements.txt for the Python packages).
CUDA_BASE = "nvidia/cuda:12.4.0-runtime-ubuntu22.04"   # the devel image is this plus 2.5 GB of compilers/headers
HF_HUB = "huggingface_hub==1.16.1"                      # same pin as requirements.txt
FONT_URL = ("https://raw.githubusercontent.com/google/fonts/29b4ec6df2522a8aee279414b78a07910b065874/"
            "ofl/anton/Anton-Regular.ttf")
FONT_SHA256 = "a4ba3a92350ebb031da0cb47630ac49eb265082ca1bc0450442f4a83ab947cab"
FONT_PATH = "/usr/share/fonts/truetype/custom/Anton-Regular.ttf"

if modal.is_local():
    # `modal deploy` / `modal run`: refuse to build an image whose renders would all fail.
    import download_weights

    download_weights.check_before_deploy()

# Rarely changing, heavy layers first; the code last (mounted, not baked in).
image = (modal.Image.from_registry(CUDA_BASE, add_python="3.12")
    .apt_install(["ffmpeg", "fontconfig", "wget"])
    # Same FFmpeg as before (Ubuntu 22.04: 4.4.x, NVENC through the driver Modal mounts).
    .run_commands("ffmpeg -version | head -n 1 | grep -q 'ffmpeg version 4\\.4' "
                  "|| (ffmpeg -version | head -n 1; echo 'Expected FFmpeg 4.4.x'; exit 1)")
    .run_commands(["mkdir -p /usr/share/fonts/truetype/custom",
                   f"wget -q -O {FONT_PATH} {FONT_URL}",
                   f"echo '{FONT_SHA256}  {FONT_PATH}' | sha256sum -c -",
                   "fc-cache -f"])
    # Bake Parakeet v2 (fp32 ONNX, ~2.5 GB) into the image; add HF_TOKEN to the
    # secret if Hugging Face rate-limits the build (HTTP 429).
    .pip_install(HF_HUB)
    .add_local_file("download_parakeet.py", "/opt/download_parakeet.py", copy=True)
    .run_commands(f"python /opt/download_parakeet.py {PARAKEET_DIR}",
                  secrets=[modal.Secret.from_name(SECRET_NAME)])
    .pip_install_from_requirements("requirements.txt")
    .env({
        "PARAKEET_MODEL_DIR": PARAKEET_DIR,
        "ASD_DIR": "/asd",
        # cuDNN 9 / cuBLAS 12.8 from the nvidia-* wheels that torch installs (used by ONNX Runtime too)
        "LD_LIBRARY_PATH": "/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:"
                           "/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:"
                           "/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
    })
    .add_local_dir("asd", "/asd", copy=True, ignore=["**/__pycache__", "**/*.pyc"])
    .add_local_python_source("moments", "replay", "settings", "audio_utils", "parakeet_transcriber", "render",
                             "costs", "clipper_names", "segments")
    .add_local_dir("prompts", "/root/prompts"))

# Heavy ML libraries only exist inside the containers, so they are imported inside
# the functions that use them: your laptop only needs `pip install modal` to deploy.
app = modal.App(APP_NAME, image=image)

# Exchange folder between your laptop and the containers.
jobs_volume = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)
secret = modal.Secret.from_name(SECRET_NAME)
# Ranking settings from .env (MIN_CLIPS, CLIP_MIN/MAX_SECONDS, GEMINI_MODELS, ...) and
# CLIPPER_APP, so the containers use the same names as the deployment.
settings_secret = modal.Secret.from_dict(settings.CONTAINER_SETTINGS)

JOBS_PATH = pathlib.Path("/jobs")
# Live progress for runner.py / the web app: {job_id: {"stage", "done", "total", "fraction",
# "etaSeconds", "ready"}} and, while rendering, {"<job_id>:clip<N>": fraction done} from each
# render container.
progress_dict = modal.Dict.from_name(PROGRESS_DICT, create_if_missing=True)


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


def cpu_seconds() -> float:
    """CPU time used by this process and the children it waited for (FFmpeg)."""
    t = os.times()
    return t.user + t.system + t.children_user + t.children_system


def peak_memory_gib() -> float | None:
    """Largest resident memory of this process or one of its children so far (Linux)."""
    try:
        import resource

        kib = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        return kib / 1024 / 1024
    except Exception:  # noqa: BLE001
        return None


def measured(request_cpu: float, request_gib: float, seconds: float, cpu_used: float) -> dict:
    """What Modal bills: the larger of the request and the use (average cores, peak memory)."""
    cores = cpu_used / seconds if seconds > 0 else 0.0
    peak = peak_memory_gib() or 0.0
    return {"cpu": round(max(request_cpu, cores), 3), "memoryGiB": round(max(request_gib, peak), 3)}


class UsageTracker:
    """What a GPU container reports for the cost estimate (see costs.py)."""

    def __init__(self, enter_started: float, cpu: float, memory_gib: float, scaledown: float):
        self.enter_seconds = time.time() - enter_started
        self.calls = 0
        self.request = (cpu, memory_gib)
        self.base = {"container": os.environ.get("MODAL_TASK_ID"), "gpu": gpu_name(), "cpu": cpu,
                     "memoryGiB": memory_gib, "scaledown": scaledown}

    @staticmethod
    def begin() -> tuple[float, float]:
        return time.time(), cpu_seconds()

    def report(self, started) -> dict:
        """started: begin()'s value (or a plain start time: then the request is reported)."""
        self.calls += 1
        start_time, start_cpu = started if isinstance(started, tuple) else (started, None)
        seconds = time.time() - start_time
        usage = {**self.base, "seconds": round(seconds, 2),
                 "enterSeconds": round(self.enter_seconds, 2) if self.calls == 1 else 0.0}
        if start_cpu is not None:
            usage.update(measured(*self.request, seconds, cpu_seconds() - start_cpu))
        return usage


# --------------------------------------------------------------------------- #
# Transcription (GPU)
# --------------------------------------------------------------------------- #

@app.cls(gpu=GPU, timeout=1800, retries=0, scaledown_window=TRANSCRIBER_SCALEDOWN_S, cpu=TRANSCRIBER_CPU,
         memory=TRANSCRIBER_MEMORY_MB, secrets=[secret, settings_secret], volumes={str(JOBS_PATH): jobs_volume})
class Transcriber:
    @modal.enter()
    def load(self):
        import parakeet_transcriber

        enter_started = time.time()
        # Raises if ONNX Runtime is not actually running Parakeet on CUDA (no silent CPU fallback).
        self.parakeet = parakeet_transcriber.ParakeetEngine(parakeet_transcriber.default_model_dir(), device="cuda")
        self.usage = UsageTracker(enter_started, cpu=TRANSCRIBER_CPU, memory_gib=TRANSCRIBER_MEMORY_MB / 1024,
                                  scaledown=TRANSCRIBER_SCALEDOWN_S)

    @modal.method()
    def transcribe(self, job_id: str, media: str = "input.mp4") -> dict:
        """Transcribe /<job_id>/<media> (the audio, or the whole video) into transcript.json."""
        started = UsageTracker.begin()
        jobs_volume.reload()
        job_dir = JOBS_PATH / job_id
        audio = read_audio(job_dir / media)
        words = self.parakeet.transcribe(audio)
        (job_dir / "transcript.json").write_text(json.dumps(words))
        jobs_volume.commit()
        took = time.time() - started[0]
        audio_s = len(audio) / 16000
        return {"words": words, "seconds": round(took, 1),
                "stats": {"audioSeconds": round(audio_s, 1), "words": len(words),
                          "realTimeFactor": round(audio_s / max(took, 1e-6), 1)},
                "usage": self.usage.report(started)}

    @modal.method()
    def transcribe_ranges(self, job_id: str, ranges: list) -> dict:
        """Legacy Whistle jobs: Parakeet words for each (start, end) clip range."""
        started = UsageTracker.begin()
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

def check_render_gpu(encoder: str, gpu: str | None) -> None:
    """Startup check of a render container: torch sees the GPU, and FFmpeg encodes on NVENC
    unless this GPU type has no NVENC. Raises rather than rendering slowly on the CPU."""
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() is False in the render container: CUDA is not usable")
    label = costs.gpu_label(gpu) or "?"
    if encoder != "nvenc" and label not in settings.NO_NVENC_GPUS:
        raise RuntimeError(f"NVENC is not working on this {gpu or 'GPU'}: FFmpeg could not encode with "
                           "h264_nvenc (see the log above)")


def save_clip_to_volume(job_id: str, name: str, clip: pathlib.Path) -> None:
    """Write a finished clip to /<job_id>/clips/<name> and commit, so the laptop can download
    it right away. Written under <name>.part first: a background commit never publishes
    half a clip under the real name."""
    folder = JOBS_PATH / job_id / "clips"
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / f"{name}.part"
    shutil.copyfile(clip, part)
    os.replace(part, folder / name)
    jobs_volume.commit()


class RenderWorker:
    """Models and encoder of one render container (also used by benchmark_gpu.py)."""

    def __init__(self):
        import render

        enter_started = time.time()
        self.models = render.fast_asd.Models("cuda")
        self.encoder = render.probe_encoder("nvenc")
        gpu = gpu_name()
        check_render_gpu(self.encoder, gpu)
        print(f"Renderer ready in {time.time() - enter_started:.1f}s on {gpu or '?'} (encoder: {self.encoder})")
        self.usage = UsageTracker(enter_started, cpu=float(settings.RENDER_CPU),
                                  memory_gib=RENDER_MEMORY_MB / 1024, scaledown=RENDERER_SCALEDOWN_S)

    def render(self, job_id: str, index: int, moment: dict, words: list, output: str = "volume",
               source: str = "input.mp4", target_us: int | None = None) -> dict:
        """Render clip_<index> from /<job_id>/<source>: the whole video, or the clip's footage
        with target_us (segments.py). output: "volume" writes it to the jobs volume (bytes
        returned only if that fails), "bytes" returns it as "data", "none" keeps neither (benchmark)."""
        import render

        started = UsageTracker.begin()
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
            card = JOBS_PATH / job_id / "cards" / f"clip_{index}.png"     # endcard.py, uploaded by the laptop
            result = render.render_clip(self.models, JOBS_PATH / job_id / source, float(moment["start"]),
                                        float(moment["end"]), words, out_path, work_dir, self.encoder, progress,
                                        target_us=target_us, card=card if card.is_file() else None)
            print(f"Clip {index}: {result['timings']} ({result['frames']} frames, {result['tracks']} face "
                  f"tracks, {result['encoder']}, crop switches {result['switches']})")
            out = {"index": index, "name": name, "timings": result["timings"], "encoder": result["encoder"],
                   "switches": result["switches"], "endCard": result.get("endCard")}
            if output == "bytes":
                out["data"] = out_path.read_bytes()
            elif output == "volume":
                try:
                    save_clip_to_volume(job_id, name, out_path)
                    out["written"] = True
                except Exception as exc:  # noqa: BLE001 - render_clips writes it instead
                    print(f"Could not write clip {index} to the volume ({exc!r}); returning it instead")
                    out["data"] = out_path.read_bytes()
            out["usage"] = self.usage.report(started)
            return out
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
    def render(self, job_id: str, index: int, moment: dict, words: list, source: str = "input.mp4",
               target_us: int | None = None) -> dict:
        return self.worker.render(job_id, index, moment, words, source=source, target_us=target_us)


# --------------------------------------------------------------------------- #
# The job (CPU functions): phase 1 transcribe -> rank, phase 2 render in parallel
# --------------------------------------------------------------------------- #

def clip_words(words: list, start: float, end: float) -> list:
    return [w for w in words if w.get("end") is not None and w.get("start") is not None
            and w["end"] > start and w["start"] < end]


def clip_timing(result: dict) -> dict:
    """One clipTimings entry: the call's time, its container's start-up, the render steps."""
    usage = result.get("usage") or {}
    entry = {"clip": result.get("name"), "seconds": usage.get("seconds"), "enterSeconds": usage.get("enterSeconds")}
    if result.get("error"):
        entry["error"] = True
    else:
        entry["steps"] = result.get("timings")
        entry["encoder"] = result.get("encoder")
        entry["endCard"] = result.get("endCard")
    return entry


def collect_clip(job_id: str, result: dict) -> bool:
    """A finished render: True when its clip is on the volume (written by the renderer, or
    here from the bytes it returned because its own write failed)."""
    if result.get("error"):
        return False
    if result.get("data") is not None:
        clips_dir = JOBS_PATH / job_id / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        (clips_dir / result["name"]).write_bytes(result.pop("data"))
        jobs_volume.commit()
        return True
    return bool(result.get("written"))


def render_source(footage: dict | None) -> tuple[str, int | None]:
    """(file in the job folder, seek target) a clip renders from: its footage, or the whole video."""
    if not footage:
        return "input.mp4", None
    return f"{segments.FOOTAGE_DIR}/{segments.footage_name(int(footage['index']))}", int(footage["targetUs"])


def render_in_parallel(job_id: str, clip_moments: list, words_per_clip: list, renderer=None,
                       footage: list | None = None) -> tuple[list, list, list, list]:
    """Spawn one render per clip and report combined progress, with the clips already on the
    volume in "ready" and the renders' call ids in "calls" (the laptop keeps them, so it can
    cancel them even when this function was killed). footage: per clip {"index", "targetUs"}
    (None: the whole video). If this call is cancelled or fails while rendering, the renders
    still running are cancelled: a spawned call does not end with the call that spawned it.
    Returns clips, errors, usages and clipTimings in clip order."""
    total = len(clip_moments)
    renderer = renderer or Renderer()
    by_index = {int(f["index"]): f for f in footage or []}
    calls = {}
    results: dict[int, dict] = {}
    on_volume: dict[int, bool] = {}
    try:
        for i, moment in enumerate(clip_moments):
            source, target_us = render_source(by_index.get(i))
            calls[i] = renderer.render.spawn(job_id, i, moment, words_per_clip[i], source=source,
                                             target_us=target_us)
        call_ids = [cid for cid in (getattr(call, "object_id", None) for call in calls.values())
                    if isinstance(cid, str)]
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
                    continue
                except Exception as exc:  # noqa: BLE001 - e.g. the container crashed
                    results[i] = {"index": i, "name": f"clip_{i}.mp4", "error": f"clip_{i}: {exc}"}
                    fractions[i] = 1.0
                try:
                    on_volume[i] = collect_clip(job_id, results[i])
                except Exception as exc:  # noqa: BLE001
                    results[i] = {**results[i], "error": f"clip_{i}: could not save it ({exc})"}
                    results[i].pop("data", None)
                    on_volume[i] = False
            fraction = sum(fractions.values()) / total
            elapsed = time.time() - started
            eta = round(elapsed / fraction * (1 - fraction)) if fraction >= 0.15 and len(results) < total else None
            report_progress(job_id, {"stage": "rendering", "done": len(results), "total": total,
                                     "fraction": round(fraction, 3), "etaSeconds": eta, "calls": call_ids,
                                     "ready": [f"clip_{i}.mp4" for i in sorted(on_volume) if on_volume[i]]})
            if len(results) < total:
                time.sleep(POLL_SECONDS)
    except BaseException:   # cancelled (modal.exception.InputCancellation), or failed
        for i, call in calls.items():
            if i not in results:
                try:
                    call.cancel(terminate_containers=True)
                except Exception:  # noqa: BLE001 - the laptop cancels them too ("calls")
                    pass
        raise

    clips, errors, usages, timings = [], [], [], []
    for i in range(total):
        result = results[i]
        if result.get("usage"):
            usages.append(result["usage"])
        timings.append(clip_timing(result))
        if result.get("error") or not on_volume.get(i):
            errors.append(result.get("error") or f"clip_{i}: not saved")
            continue
        clips.append(result["name"])
    for i in range(total):
        try:
            progress_dict.pop(f"{job_id}:clip{i}")
        except Exception:  # noqa: BLE001
            pass
    return clips, errors, usages, timings


def orchestrator_usage(started: float, started_cpu: float) -> tuple[float, dict]:
    """(seconds, {cpu, memoryGiB}) of this CPU function's own container so far."""
    seconds = round(time.time() - started, 2)
    return seconds, measured(PROCESS_JOB_CPU, PROCESS_JOB_MEMORY_MB / 1024, seconds, cpu_seconds() - started_cpu)


def load_words(job_dir: pathlib.Path) -> list:
    return json.loads((job_dir / "transcript.json").read_text())


@app.function(timeout=3600, retries=0, cpu=PROCESS_JOB_CPU, memory=PROCESS_JOB_MEMORY_MB,
              secrets=[secret, settings_secret], volumes={str(JOBS_PATH): jobs_volume})
def transcribe_and_rank(job_id: str, max_clips: int = DEFAULT_MAX_CLIPS, context: dict | None = None,
                        transcript: str = "parakeet", media: str = "audio.m4a") -> dict:
    """Phase 1: transcribe /<job_id>/<media> (or reuse transcript.json), then rank the moments."""
    job_started, job_cpu = time.time(), cpu_seconds()
    jobs_volume.reload()
    job_dir = JOBS_PATH / job_id
    timings, stats, usages, gpus = {}, {}, [], []

    started = time.time()
    if transcript in ("uploaded", "reuse"):
        words = load_words(job_dir)
        print(f"Using the saved transcript ({len(words)} words, mode {transcript})")
    else:
        if not (job_dir / media).exists():
            raise FileNotFoundError(f"{job_dir / media} not found in the jobs volume")
        report_progress(job_id, {"stage": "transcribing"})
        result = Transcriber().transcribe.remote(job_id, media)
        words, stats = result["words"], result["stats"]
        usages.append({**result["usage"], "stage": "transcribe"})
        gpus.append(result["usage"].get("gpu"))
        timings["transcribe"] = round(time.time() - started, 1)

    started = time.time()
    report_progress(job_id, {"stage": "ranking"})
    ranking: dict = {}
    client = moment_selection.make_client(os.environ["GEMINI_API_KEY"])
    model = os.environ.get("GEMINI_MODEL") or moment_selection.GEMINI_MODELS[0]
    clip_moments = moment_selection.select_moments(words, max_clips, client, model, context, report=ranking)
    moment_selection.write_post_text(clip_moments, words, client, model, context, report=ranking)
    (job_dir / "moments.json").write_text(json.dumps(clip_moments, indent=2))
    jobs_volume.commit()
    timings["select"] = round(time.time() - started, 1)

    clip_words_list, subtitles = None, "parakeet"
    if transcript == "uploaded" and clip_moments:
        words_per_clip = [clip_words(words, float(m["start"]), float(m["end"])) for m in clip_moments]
        refined = Transcriber().transcribe_ranges.remote(
            job_id, [(float(m["start"]), float(m["end"])) for m in clip_moments])
        usages.append({**refined["usage"], "stage": "transcribe"})
        better = refined["words"]
        clip_words_list = [b if b else w for b, w in zip(better, words_per_clip)]
        n_refined = sum(1 for b in better if b)
        subtitles = "parakeet" if n_refined == len(better) else ("mixed" if n_refined else "whistle")

    seconds, orchestrator = orchestrator_usage(job_started, job_cpu)
    print(f"Job {job_id}: {len(clip_moments)} moments, timings {timings}")
    return {"moments": clip_moments, "ranking": ranking, "timings": timings, "transcription": stats,
            "subtitles": subtitles, "clipWords": clip_words_list,
            "gpu": ", ".join(sorted({costs.gpu_label(g) or g for g in gpus if g})) or None,
            "usages": usages, "orchestratorSeconds": seconds, "orchestrator": orchestrator}


@app.function(timeout=3600, retries=0, cpu=PROCESS_JOB_CPU, memory=PROCESS_JOB_MEMORY_MB,
              secrets=[settings_secret], volumes={str(JOBS_PATH): jobs_volume})
def render_clips(job_id: str, moments: list, footage: list | None = None, clip_words_list: list | None = None) -> dict:
    """Phase 2: render every moment at once, each from its footage (or the whole video)."""
    job_started, job_cpu = time.time(), cpu_seconds()
    jobs_volume.reload()
    job_dir = JOBS_PATH / job_id
    for i in range(len(moments)):
        source, _ = render_source(next((f for f in footage or [] if int(f["index"]) == i), None))
        if not (job_dir / source).exists():
            raise FileNotFoundError(f"{job_dir / source} not found in the jobs volume")
    if clip_words_list is None:
        words = load_words(job_dir)
        clip_words_list = [clip_words(words, float(m["start"]), float(m["end"])) for m in moments]

    started = time.time()
    clips, errors, render_usages, clip_timings = ([], [], [], []) if not moments else \
        render_in_parallel(job_id, moments, clip_words_list, footage=footage)
    usages = [{**u, "stage": "render"} for u in render_usages]
    timings = {"render": round(time.time() - started, 1)}

    seconds, orchestrator = orchestrator_usage(job_started, job_cpu)
    cost = costs.estimate(usages, orchestrator_seconds=seconds, orchestrator=orchestrator)
    gpus = sorted(cost["gpus"]) or [costs.gpu_label(GPU if isinstance(GPU, str) else GPU[0])]
    print(f"Job {job_id}: {len(clips)} clips, timings {timings}, estimated cost ${cost['usd']:.3f}")
    return {"clips": clips, "errors": errors, "timings": timings, "clipTimings": clip_timings,
            "gpu": ", ".join(gpus), "cost": cost,
            # The runner prices these with Modal's live rates (costs.estimate).
            "usages": usages, "orchestratorSeconds": seconds, "orchestrator": orchestrator}
