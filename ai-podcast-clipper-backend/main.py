"""
AI Podcast Clipper - Modal backend (English).

Deploy once with `modal deploy main.py`. The local runner (runner.py) uploads a
video into the `ai-podcast-clipper-jobs` Modal Volume and calls
`AiPodcastClipper.process_job`, which runs the whole cloud pipeline in one GPU
container (A10 by default, `CLIPPER_GPU` in .env):

  transcript="parakeet"  Parakeet TDT 0.6B v2 transcribes the full video on the
                         GPU -> Gemini ranks the best moments -> LR-ASD speaker
                         crop (9:16) -> subtitles from the same transcript.

  transcript="reuse"     re-rank & re-render: reuse the Parakeet transcript of an
                         earlier run (uploaded again by runner.py --rerank).

  transcript="uploaded"  legacy jobs from the removed Whistle engine: reuse their
                         uploaded transcript, but re-transcribe each chosen clip with
                         Parakeet for accurate subtitles.

Volume layout (per job):
    /<job_id>/input.mp4          uploaded by runner.py
    /<job_id>/transcript.json    word-level transcript (Parakeet)
    /<job_id>/moments.json       ranked clip moments chosen by Gemini (best first)
    /<job_id>/clips/clip_N.mp4   finished vertical clips with subtitles (clip_0 = best)
"""

import glob
import json
import os
import pathlib
import pickle
import shutil
import subprocess
import time

import modal

import moments as moment_selection
import settings

APP_NAME = "ai-podcast-clipper"
# Primary model: GEMINI_MODEL in the Modal secret if set, else the first of GEMINI_MODELS (.env).
DEFAULT_MAX_CLIPS = 5
CLIPPER_GPU = settings.CLIPPER_GPU          # .env, read at deploy time
PARAKEET_DIR = "/opt/parakeet-tdt-0.6b-v2"

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
        # cuDNN 9 / cuBLAS 12.8 from the nvidia-* wheels that torch installs (used by ONNX Runtime too)
        "LD_LIBRARY_PATH": "/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:"
                           "/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:"
                           "/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
    })
    .add_local_dir("asd", "/asd", copy=True)
    .add_local_python_source("moments", "settings", "audio_utils", "parakeet_transcriber")
    .add_local_dir("prompts", "/root/prompts"))

# Heavy ML libraries only exist inside the GPU container. Importing them here
# means your laptop only needs `pip install modal` to deploy this file.
with image.imports():
    import cv2
    import ffmpegcv
    import numpy as np
    import pysubs2
    from tqdm import tqdm

    import parakeet_transcriber

app = modal.App(APP_NAME, image=image)

# Exchange folder between your laptop and the GPU container.
jobs_volume = modal.Volume.from_name(
    "ai-podcast-clipper-jobs", create_if_missing=True)
secret = modal.Secret.from_name("ai-podcast-clipper-secret")
# Ranking settings from .env (MIN_CLIPS, CLIP_MIN/MAX_SECONDS, GEMINI_MODELS, ...).
settings_secret = modal.Secret.from_dict(settings.CONTAINER_SETTINGS)

JOBS_PATH = pathlib.Path("/jobs")
# Live progress for runner.py / the web app: {job_id: {"stage", "done", "total", "etaSeconds"}}.
progress_dict = modal.Dict.from_name("ai-podcast-clipper-progress", create_if_missing=True)


def report_progress(job_id: str, stage: str, **fields) -> None:
    try:
        progress_dict.put(job_id, {"stage": stage, **fields})
    except Exception as exc:  # noqa: BLE001 - progress is best effort
        print(f"Could not report progress: {exc!r}")


def gemini_settings():
    client = moment_selection.make_client(os.environ["GEMINI_API_KEY"])
    return client, os.environ.get("GEMINI_MODEL") or moment_selection.GEMINI_MODELS[0]


def read_audio(media_path: pathlib.Path):
    """Decode any media file to 16 kHz mono float32 samples with ffmpeg."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(media_path), "-vn", "-ac", "1",
                          "-ar", "16000", "-f", "s16le", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


# --------------------------------------------------------------------------- #
# Rendering helpers (GPU container)
# --------------------------------------------------------------------------- #

def create_vertical_video(tracks, scores, pyframes_path, pyavi_path, audio_path, output_path, framerate=25):
    target_width = 1080
    target_height = 1920

    flist = glob.glob(os.path.join(pyframes_path, "*.jpg"))
    flist.sort()

    faces = [[] for _ in range(len(flist))]

    for tidx, track in enumerate(tracks):
        score_array = scores[tidx]
        for fidx, frame in enumerate(track["track"]["frame"].tolist()):
            slice_start = max(fidx - 30, 0)
            slice_end = min(fidx + 30, len(score_array))
            score_slice = score_array[slice_start:slice_end]
            avg_score = float(np.mean(score_slice)
                              if len(score_slice) > 0 else 0)

            faces[frame].append(
                {'track': tidx, 'score': avg_score, 's': track['proc_track']["s"][fidx], 'x': track['proc_track']["x"][fidx], 'y': track['proc_track']["y"][fidx]})

    temp_video_path = os.path.join(pyavi_path, "video_only.mp4")

    vout = None
    for fidx, fname in tqdm(enumerate(flist), total=len(flist), desc="Creating vertical video"):
        img = cv2.imread(fname)
        if img is None:
            continue

        current_faces = faces[fidx]

        max_score_face = max(
            current_faces, key=lambda face: face['score']) if current_faces else None

        if max_score_face and max_score_face['score'] < 0:
            max_score_face = None

        if vout is None:
            vout = ffmpegcv.VideoWriterNV(
                file=temp_video_path,
                codec=None,
                fps=framerate,
                resize=(target_width, target_height)
            )

        if max_score_face:
            mode = "crop"
        else:
            mode = "resize"

        if mode == "resize":
            scale = target_width / img.shape[1]
            resized_height = int(img.shape[0] * scale)
            resized_image = cv2.resize(
                img, (target_width, resized_height), interpolation=cv2.INTER_AREA)

            scale_for_bg = max(
                target_width / img.shape[1], target_height / img.shape[0])
            bg_width = int(img.shape[1] * scale_for_bg)
            bg_heigth = int(img.shape[0] * scale_for_bg)

            blurred_background = cv2.resize(img, (bg_width, bg_heigth))
            blurred_background = cv2.GaussianBlur(
                blurred_background, (121, 121), 0)

            crop_x = (bg_width - target_width) // 2
            crop_y = (bg_heigth - target_height) // 2
            blurred_background = blurred_background[crop_y:crop_y +
                                                    target_height, crop_x:crop_x + target_width]

            center_y = (target_height - resized_height) // 2
            blurred_background[center_y:center_y +
                               resized_height, :] = resized_image

            vout.write(blurred_background)

        elif mode == "crop":
            scale = target_height / img.shape[0]
            resized_image = cv2.resize(
                img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            frame_width = resized_image.shape[1]

            center_x = int(
                max_score_face["x"] * scale if max_score_face else frame_width // 2)
            top_x = max(min(center_x - target_width // 2,
                        frame_width - target_width), 0)

            image_cropped = resized_image[0:target_height,
                                          top_x:top_x + target_width]

            vout.write(image_cropped)

    if vout:
        vout.release()

    ffmpeg_command = (f"ffmpeg -y -i {temp_video_path} -i {audio_path} "
                      f"-c:v h264 -preset fast -crf 23 -c:a aac -b:a 128k "
                      f"{output_path}")
    subprocess.run(ffmpeg_command, shell=True, check=True, text=True)


def create_subtitles_with_ffmpeg(transcript_segments: list, clip_start: float, clip_end: float, clip_video_path: str, output_path: str, max_words: int = 5):
    temp_dir = os.path.dirname(output_path)
    subtitle_path = os.path.join(temp_dir, "temp_subtitles.ass")

    clip_segments = [segment for segment in transcript_segments
                     if segment.get("start") is not None
                     and segment.get("end") is not None
                     and segment.get("end") > clip_start
                     and segment.get("start") < clip_end
                     ]

    subtitles = []
    current_words = []
    current_start = None
    current_end = None

    for segment in clip_segments:
        word = segment.get("word", "").strip()
        seg_start = segment.get("start")
        seg_end = segment.get("end")

        if not word or seg_start is None or seg_end is None:
            continue

        start_rel = max(0.0, seg_start - clip_start)
        end_rel = max(0.0, seg_end - clip_start)

        if end_rel <= 0:
            continue

        if not current_words:
            current_start = start_rel
            current_end = end_rel
            current_words = [word]
        elif len(current_words) >= max_words:
            subtitles.append(
                (current_start, current_end, ' '.join(current_words)))
            current_words = [word]
            current_start = start_rel
            current_end = end_rel
        else:
            current_words.append(word)
            current_end = end_rel

    if current_words:
        subtitles.append(
            (current_start, current_end, ' '.join(current_words)))

    subs = pysubs2.SSAFile()

    subs.info["WrapStyle"] = 0
    subs.info["ScaledBorderAndShadow"] = "yes"
    subs.info["PlayResX"] = 1080
    subs.info["PlayResY"] = 1920
    subs.info["ScriptType"] = "v4.00+"

    style_name = "Default"
    new_style = pysubs2.SSAStyle()
    new_style.fontname = "Anton"
    new_style.fontsize = 140
    new_style.primarycolor = pysubs2.Color(255, 255, 255)
    new_style.outline = 2.0
    new_style.shadow = 2.0
    new_style.shadowcolor = pysubs2.Color(0, 0, 0, 128)
    new_style.alignment = 2
    new_style.marginl = 50
    new_style.marginr = 50
    new_style.marginv = 50
    new_style.spacing = 0.0

    subs.styles[style_name] = new_style

    for i, (start, end, text) in enumerate(subtitles):
        start_time = pysubs2.make_time(s=start)
        end_time = pysubs2.make_time(s=end)
        line = pysubs2.SSAEvent(
            start=start_time, end=end_time, text=text, style=style_name)
        subs.events.append(line)

    subs.save(subtitle_path)

    ffmpeg_cmd = (f"ffmpeg -y -i {clip_video_path} -vf \"ass={subtitle_path}\" "
                  f"-c:v h264 -preset fast -crf 23 {output_path}")

    subprocess.run(ffmpeg_cmd, shell=True, check=True)


def process_clip(base_dir: pathlib.Path, original_video_path: pathlib.Path, output_path: pathlib.Path,
                 start_time: float, end_time: float, clip_index: int, transcript_segments: list,
                 refine_words=None) -> str:
    """Render one vertical clip. `refine_words(audio_path, start_time)` may return
    more accurate absolute-time words for the subtitles (hybrid mode).
    Returns the subtitle source used: "refined" or "transcript"."""
    clip_name = f"clip_{clip_index}"

    clip_dir = base_dir / clip_name
    clip_dir.mkdir(parents=True, exist_ok=True)

    clip_segment_path = clip_dir / f"{clip_name}_segment.mp4"
    vertical_mp4_path = clip_dir / "pyavi" / "video_out_vertical.mp4"
    subtitle_output_path = clip_dir / "pyavi" / "video_with_subtitles.mp4"

    (clip_dir / "pywork").mkdir(exist_ok=True)
    pyframes_path = clip_dir / "pyframes"
    pyavi_path = clip_dir / "pyavi"
    audio_path = clip_dir / "pyavi" / "audio.wav"

    pyframes_path.mkdir(exist_ok=True)
    pyavi_path.mkdir(exist_ok=True)

    duration = end_time - start_time
    cut_command = (f"ffmpeg -i {original_video_path} -ss {start_time} -t {duration} "
                   f"{clip_segment_path}")
    subprocess.run(cut_command, shell=True, check=True,
                   capture_output=True, text=True)

    extract_cmd = f"ffmpeg -i {clip_segment_path} -vn -acodec pcm_s16le -ar 16000 -ac 1 {audio_path}"
    subprocess.run(extract_cmd, shell=True,
                   check=True, capture_output=True)

    subtitle_words, subtitle_source = transcript_segments, "transcript"
    if refine_words is not None:
        try:
            refined = refine_words(audio_path, start_time)
            if refined:
                subtitle_words, subtitle_source = refined, "refined"
        except Exception as exc:  # noqa: BLE001 - fall back to the full transcript
            print(f"Clip {clip_index}: subtitle re-transcription failed ({exc!r}); using the full transcript")

    shutil.copy(clip_segment_path, base_dir / f"{clip_name}.mp4")

    # Active speaker detection (LR-ASD). Produces pyframes/ and pywork/*.pckl.
    columbia_command = (f"python Columbia_test.py --videoName {clip_name} "
                        f"--videoFolder {str(base_dir)} "
                        f"--pretrainModel weight/finetuning_TalkSet.model")

    columbia_start_time = time.time()
    asd_result = subprocess.run(columbia_command, cwd="/asd", shell=True)
    print(
        f"Columbia script completed in {time.time() - columbia_start_time:.2f} seconds (exit code {asd_result.returncode})")

    tracks_path = clip_dir / "pywork" / "tracks.pckl"
    scores_path = clip_dir / "pywork" / "scores.pckl"
    if not tracks_path.exists() or not scores_path.exists():
        raise FileNotFoundError(
            "Active speaker detection produced no tracks/scores - check the LR-ASD output above")

    with open(tracks_path, "rb") as f:
        tracks = pickle.load(f)

    with open(scores_path, "rb") as f:
        scores = pickle.load(f)

    cvv_start_time = time.time()
    create_vertical_video(
        tracks, scores, pyframes_path, pyavi_path, audio_path, vertical_mp4_path
    )
    print(
        f"Clip {clip_index} vertical video creation time: {time.time() - cvv_start_time:.2f} seconds")

    create_subtitles_with_ffmpeg(subtitle_words, start_time,
                                 end_time, vertical_mp4_path, subtitle_output_path, max_words=5)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(subtitle_output_path, output_path)
    return subtitle_source


# --------------------------------------------------------------------------- #
# GPU class: Parakeet + Gemini + rendering in one container
# --------------------------------------------------------------------------- #

@app.cls(gpu=CLIPPER_GPU, timeout=3600, retries=0, scaledown_window=20,
         secrets=[secret, settings_secret], volumes={str(JOBS_PATH): jobs_volume})
class AiPodcastClipper:
    @modal.enter()
    def load_model(self):
        self.parakeet = parakeet_transcriber.ParakeetEngine(
            parakeet_transcriber.default_model_dir(), device="cuda")
        self.gemini_client, self.gemini_model = gemini_settings()
        print(f"Created gemini client (model: {self.gemini_model})")

    def prepare(self, job_id: str):
        jobs_volume.reload()
        job_dir = JOBS_PATH / job_id
        input_path = job_dir / "input.mp4"
        if not input_path.exists():
            raise FileNotFoundError(f"{input_path} not found in the jobs volume")
        base_dir = pathlib.Path("/tmp") / job_id
        if base_dir.exists():
            shutil.rmtree(base_dir, ignore_errors=True)
        base_dir.mkdir(parents=True, exist_ok=True)
        video_path = base_dir / "input.mp4"
        shutil.copy(input_path, video_path)
        return job_dir, base_dir, video_path

    def render_clips(self, job_dir, base_dir, video_path, clip_moments, transcript_segments, refine_words=None,
                     job_id: str | None = None):
        clips, errors, sources = [], [], {}
        started = time.time()
        for index, moment in enumerate(clip_moments):
            if job_id:
                eta = (time.time() - started) / index * (len(clip_moments) - index) if index else None
                report_progress(job_id, "rendering", done=index, total=len(clip_moments),
                                etaSeconds=round(eta) if eta is not None else None)
            print(f"Processing clip {index} (rank {moment.get('rank', index + 1)}, score "
                  f"{moment.get('score', '-')}) from {moment['start']} to {moment['end']}")
            output_path = job_dir / "clips" / f"clip_{index}.mp4"
            try:
                sources[output_path.name] = process_clip(
                    base_dir, video_path, output_path, float(moment["start"]), float(moment["end"]),
                    index, transcript_segments, refine_words)
                clips.append(output_path.name)
            except Exception as exc:  # noqa: BLE001 - one failing clip does not stop the others
                print(f"Clip {index} failed: {exc!r}")
                errors.append(f"clip_{index}: {exc}")
        return clips, errors, sources

    @modal.method()
    def process_job(self, job_id: str, max_clips: int = DEFAULT_MAX_CLIPS, context: dict | None = None,
                    transcript: str = "parakeet") -> dict:
        """Transcribe (or load the uploaded transcript), rank moments, render clips.

        transcript="parakeet": transcribe the full video with Parakeet on the GPU.
        transcript="uploaded": use /jobs/<job_id>/transcript.json (legacy Whistle jobs) and
                               re-transcribe only the chosen clips with Parakeet.
        transcript="reuse":    use /jobs/<job_id>/transcript.json as-is (a Parakeet transcript
                               from an earlier run: "Re-rank & re-render").
        """
        job_dir, base_dir, video_path = self.prepare(job_id)
        timings, stats = {}, {}
        try:
            started = time.time()
            report_progress(job_id, "transcribing" if transcript == "parakeet" else "ranking")
            if transcript in ("uploaded", "reuse"):
                transcript_segments = json.loads((job_dir / "transcript.json").read_text())
                print(f"Using the uploaded transcript ({len(transcript_segments)} words, mode {transcript})")
            else:
                audio = read_audio(video_path)
                transcript_segments = self.parakeet.transcribe(audio)
                (job_dir / "transcript.json").write_text(json.dumps(transcript_segments))
                took = time.time() - started
                stats = {"audioSeconds": round(len(audio) / 16000, 1), "words": len(transcript_segments),
                         "realTimeFactor": round(len(audio) / 16000 / max(took, 1e-6), 1)}
                timings["transcribe"] = round(took, 1)

            started = time.time()
            report_progress(job_id, "ranking")
            print("Ranking clip moments")
            ranking: dict = {}
            clip_moments = moment_selection.select_moments(
                transcript_segments, max_clips, self.gemini_client, self.gemini_model, context, report=ranking)
            (job_dir / "moments.json").write_text(json.dumps(clip_moments, indent=2))
            timings["select"] = round(time.time() - started, 1)

            refine = None
            if transcript == "uploaded":
                def refine(audio_path, start_time):
                    return self.parakeet.transcribe(read_audio(pathlib.Path(audio_path)), offset_s=start_time)

            started = time.time()
            clips, errors, sources = self.render_clips(
                job_dir, base_dir, video_path, clip_moments, transcript_segments, refine, job_id)
            timings["render"] = round(time.time() - started, 1)
            jobs_volume.commit()

            if transcript == "uploaded":
                subtitles = "parakeet" if sources and all(s == "refined" for s in sources.values()) else (
                    "mixed" if any(s == "refined" for s in sources.values()) else "whistle")
            else:
                subtitles = "parakeet"
            return {"clips": clips, "moments": clip_moments, "errors": errors, "subtitles": subtitles,
                    "timings": timings, "transcription": stats, "gpu": CLIPPER_GPU, "ranking": ranking}
        finally:
            print(f"Cleaning up temp dir {base_dir}")
            shutil.rmtree(base_dir, ignore_errors=True)
