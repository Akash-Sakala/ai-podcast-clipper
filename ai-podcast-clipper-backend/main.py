"""
AI Podcast Clipper - GPU backend (runs on Modal).

Deploy once with `modal deploy main.py`. The local runner (runner.py) uploads a
video into the `ai-podcast-clipper-jobs` Modal Volume, calls
`AiPodcastClipper.process_job` and downloads the finished clips again.

Volume layout (per job):
    /<job_id>/input.mp4          uploaded by runner.py
    /<job_id>/transcript.json    word-level transcript (WhisperX)
    /<job_id>/moments.json       clip moments chosen by Gemini
    /<job_id>/clips/clip_N.mp4   finished vertical clips with subtitles
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

image = (modal.Image.from_registry(
    "nvidia/cuda:12.4.0-devel-ubuntu22.04", add_python="3.12")
    .apt_install(["ffmpeg", "libgl1-mesa-glx", "wget", "libcudnn8", "libcudnn8-dev"])
    .pip_install_from_requirements("requirements.txt")
    .run_commands(["mkdir -p /usr/share/fonts/truetype/custom",
                   "wget -O /usr/share/fonts/truetype/custom/Anton-Regular.ttf https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf",
                   "fc-cache -f -v"])
    .env({
        "HF_HOME": "/models/huggingface",
        "TORCH_HOME": "/models/torch",
        # Let CTranslate2 (faster-whisper) find the cuDNN 9 / cuBLAS libraries shipped with torch
        "LD_LIBRARY_PATH": "/usr/local/lib/python3.12/site-packages/nvidia/cudnn/lib:"
                           "/usr/local/lib/python3.12/site-packages/nvidia/cublas/lib:"
                           "/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
    })
    .add_local_dir("asd", "/asd", copy=True))

# Heavy ML libraries only exist inside the Modal container. Importing them here
# means your laptop only needs `pip install modal` to deploy this file.
with image.imports():
    import cv2
    import ffmpegcv
    import numpy as np
    import pysubs2
    import whisperx
    from google import genai
    from tqdm import tqdm

app = modal.App("ai-podcast-clipper", image=image)

# Caches downloaded model weights (Whisper, alignment model) between cold starts.
model_cache = modal.Volume.from_name(
    "ai-podcast-clipper-model-cache", create_if_missing=True)
# Exchange folder between your laptop and the GPU container.
jobs_volume = modal.Volume.from_name(
    "ai-podcast-clipper-jobs", create_if_missing=True)

MODEL_CACHE_PATH = "/models"
JOBS_PATH = pathlib.Path("/jobs")

# Override with GEMINI_MODEL in the Modal secret if this model is retired.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_MAX_CLIPS = 5


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


def process_clip(base_dir: pathlib.Path, original_video_path: pathlib.Path, output_path: pathlib.Path, start_time: float, end_time: float, clip_index: int, transcript_segments: list):
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

    create_subtitles_with_ffmpeg(transcript_segments, start_time,
                                 end_time, vertical_mp4_path, subtitle_output_path, max_words=5)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(subtitle_output_path, output_path)


def parse_moments(raw: str) -> list:
    cleaned = raw.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json"):].strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[len("```"):].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-len("```")].strip()

    try:
        moments = json.loads(cleaned)
    except json.JSONDecodeError:
        print(f"Could not parse Gemini response as JSON: {raw[:500]}")
        return []

    if not isinstance(moments, list):
        return []
    return [m for m in moments if isinstance(m, dict) and "start" in m and "end" in m]


@app.cls(gpu="L40S", timeout=3600, retries=0, scaledown_window=20,
         secrets=[modal.Secret.from_name("ai-podcast-clipper-secret")],
         volumes={MODEL_CACHE_PATH: model_cache, str(JOBS_PATH): jobs_volume})
class AiPodcastClipper:
    @modal.enter()
    def load_model(self):
        print("Loading models")

        self.whisperx_model = whisperx.load_model(
            "large-v2", device="cuda", compute_type="float16")

        self.alignment_model, self.metadata = whisperx.load_align_model(
            language_code="en",
            device="cuda"
        )

        print("Transcription models loaded...")

        self.gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.gemini_model = os.environ.get(
            "GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        print(f"Created gemini client (model: {self.gemini_model})")

    def transcribe_video(self, base_dir: pathlib.Path, video_path: pathlib.Path) -> list:
        audio_path = base_dir / "audio.wav"
        extract_cmd = f"ffmpeg -i {video_path} -vn -acodec pcm_s16le -ar 16000 -ac 1 {audio_path}"
        subprocess.run(extract_cmd, shell=True,
                       check=True, capture_output=True)

        print("Starting transcription with WhisperX...")
        start_time = time.time()

        audio = whisperx.load_audio(str(audio_path))
        result = self.whisperx_model.transcribe(audio, batch_size=16)

        result = whisperx.align(
            result["segments"],
            self.alignment_model,
            self.metadata,
            audio,
            device="cuda",
            return_char_alignments=False
        )

        print(
            f"Transcription and alignment took {time.time() - start_time:.1f} seconds")

        segments = []

        if "word_segments" in result:
            for word_segment in result["word_segments"]:
                segments.append({
                    "start": word_segment.get("start"),
                    "end": word_segment.get("end"),
                    "word": word_segment["word"],
                })

        return segments

    def identify_moments(self, transcript: list) -> str:
        response = self.gemini_client.models.generate_content(model=self.gemini_model, contents="""
    This is a podcast video transcript consisting of word, along with each words's start and end time. I am looking to create clips between a minimum of 30 and maximum of 60 seconds long. The clip should never exceed 60 seconds.

    Your task is to find and extract stories, or question and their corresponding answers from the transcript.
    Each clip should begin with the question and conclude with the answer.
    It is acceptable for the clip to include a few additional sentences before a question if it aids in contextualizing the question.

    Please adhere to the following rules:
    - Ensure that clips do not overlap with one another.
    - Start and end timestamps of the clips should align perfectly with the sentence boundaries in the transcript.
    - Only use the start and end timestamps provided in the input. modifying timestamps is not allowed.
    - Format the output as a list of JSON objects, each representing a clip with 'start' and 'end' timestamps: [{"start": seconds, "end": seconds}, ...clip2, clip3]. The output should always be readable by the python json.loads function.
    - Aim to generate longer clips between 40-60 seconds, and ensure to include as much content from the context as viable.

    Avoid including:
    - Moments of greeting, thanking, or saying goodbye.
    - Non-question and answer interactions.

    If there are no valid clips to extract, the output should be an empty list [], in JSON format. Also readable by json.loads() in Python.

    The transcript is as follows:\n\n""" + str(transcript))
        print(f"Identified moments response: {response.text}")
        return response.text or "[]"

    @modal.method()
    def process_job(self, job_id: str, max_clips: int = DEFAULT_MAX_CLIPS) -> dict:
        """Process /jobs/<job_id>/input.mp4 and write clips to /jobs/<job_id>/clips/."""
        jobs_volume.reload()

        job_dir = JOBS_PATH / job_id
        input_path = job_dir / "input.mp4"
        if not input_path.exists():
            raise FileNotFoundError(f"{input_path} not found in the jobs volume")

        base_dir = pathlib.Path("/tmp") / job_id
        if base_dir.exists():
            shutil.rmtree(base_dir, ignore_errors=True)
        base_dir.mkdir(parents=True, exist_ok=True)

        try:
            video_path = base_dir / "input.mp4"
            shutil.copy(input_path, video_path)

            # 1. Transcription
            transcript_segments = self.transcribe_video(base_dir, video_path)
            (job_dir / "transcript.json").write_text(json.dumps(transcript_segments))

            # 2. Identify moments for clips
            print("Identifying clip moments")
            clip_moments = parse_moments(
                self.identify_moments(transcript_segments))
            (job_dir / "moments.json").write_text(json.dumps(clip_moments, indent=2))
            print(clip_moments)

            # 3. Process clips (one failing clip does not stop the others)
            clips, errors = [], []
            for index, moment in enumerate(clip_moments[:max_clips]):
                print(
                    f"Processing clip {index} from {moment['start']} to {moment['end']}")
                output_path = job_dir / "clips" / f"clip_{index}.mp4"
                try:
                    process_clip(base_dir, video_path, output_path,
                                 float(moment["start"]), float(moment["end"]), index, transcript_segments)
                    clips.append(output_path.name)
                except Exception as exc:  # noqa: BLE001
                    print(f"Clip {index} failed: {exc!r}")
                    errors.append(f"clip_{index}: {exc}")

            jobs_volume.commit()
            return {"clips": clips, "moments": clip_moments, "errors": errors}
        finally:
            print(f"Cleaning up temp dir {base_dir}")
            shutil.rmtree(base_dir, ignore_errors=True)
