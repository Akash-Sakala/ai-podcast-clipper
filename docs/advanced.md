# Advanced usage

Everything beyond setup and everyday use. Backend commands run from `ai-podcast-clipper-backend` with the venv active (`.venv\Scripts\Activate.ps1`).

## Where files are

Each video is a job folder in `data/jobs/<job id>/` (or under `DATA_DIR` if you set it):

| File | Contents |
|---|---|
| `job.json` | Status, progress, clip details, the Gemini `ranking` report and `runs`, the history of every run (stats page) |
| `input.mp4` | An uploaded video, only until its run finishes (kept after a failed run, for **Try again**). YouTube videos download straight to Modal and never land here |
| `thumbnail.jpg` | Cover in the video list: YouTube's own thumbnail, or a frame from an uploaded video |
| `transcript.json` | Word-level Parakeet transcript (reused by Re-rank) |
| `moments.json` | Every candidate moment with its scores and reason |
| `clips/clip_0.mp4` … | Finished clips, `clip_0` = best |
| `clips/clip_0.jpg` … | Poster still for each clip, shown before the clip loads |
| `runner.log` | Everything the runner printed; check this first when a job fails |

## Settings

Backend settings live in `ai-podcast-clipper-backend/.env` (copied from `.env.example`, git-ignored). Values set in the shell override the file. They are read by `modal deploy main.py`, so **redeploy after changing them**.

| Setting | Default | Meaning |
|---|---|---|
| `YOUTUBE_DOWNLOAD` | `cloud` | `cloud`: YouTube videos download straight into the Modal volume, and the laptop takes over if YouTube blocks it. `laptop`: always download on the laptop and upload (read by the runner, no redeploy needed) |
| `YTDLP_VERSION` / `DENO_VERSION` | `2026.10.21` / `2.9.8` | yt-dlp and its JavaScript runtime in the Modal download image. When YouTube downloads start failing, bump `YTDLP_VERSION` and redeploy: only the small download image is rebuilt |
| `CLIPPER_GPU` | `A10` | GPU for transcription and for each clip's render container. A list such as `L40S,A10` is a fallback order. Needs an NVENC encoder: L4, A10 or L40S, not A100/H100/H200/B200. See [Choose a GPU](#choose-a-gpu) |
| `RENDER_MAX_CONTAINERS` | `8` | Most clip render containers running at once (Modal Starter allows 10 GPUs) |
| `RENDER_CPU` | `4` | CPU cores reserved per render container (decoding and framing run on the CPU) |
| `FLASH_FRAMES_MAX` | `3` | A run of up to this many frames of another shot between two cuts in the video itself shows the frame before it instead. Remote-podcast edits often flash a frame of the side-by-side layout or a zoomed-out view, which reads as a jump. `0` = off |
| `SPEAKER_MIN_HOLD_SECONDS` | `0.5` | Shortest time the crop stays on one speaker, or on the whole frame, before it may switch. Removes split-second jumps; cuts in the video itself still switch at once. `0` = off |
| `MIN_CLIPS` | `3` | A job never ends with fewer clips: missing ones are picked from the transcript and marked "fallback" |
| `CLIP_MIN_SECONDS` / `CLIP_MAX_SECONDS` | `30` / `60` | Clip length limits |
| `CLIP_GRACE_SECONDS` | `5` | Extra seconds a clip may run past the maximum, only to finish its last sentence |
| `GEMINI_MODELS` | `gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite` | Models tried in order. `GEMINI_MODEL` in the Modal secret, if set, goes first |
| `GEMINI_BUSY_RETRIES` / `GEMINI_BUSY_WAIT_SECONDS` | `1` / `3` | Quick retries for a busy model (429/503/504) before moving to the next |
| `GEMINI_THINKING_LEVEL` | `low` | `minimal`, `low`, `medium` or `high`; empty = model default |
| `GEMINI_TEMPERATURE` | empty | Empty = model default (recommended for Gemini 3) |
| `GEMINI_TIMEOUT_SECONDS` / `RANKING_TIME_LIMIT_SECONDS` | `120` / `240` | Per-request timeout and the total ranking budget before the transcript fallback |

Frontend settings (`ai-podcast-clipper-frontend/.env`, all optional): `DATA_DIR`, `RUNNER_PYTHON`, `YTDLP_COOKIES_FROM_BROWSER` (`firefox`, `edge` or `chrome`).

To change the Modal secret (for example to set `GEMINI_MODEL`), re-create it with `--force`:

```powershell
modal secret create ai-podcast-clipper-secret GEMINI_API_KEY=<key> GEMINI_MODEL=<model id> --force
```

## Command line

Jobs started here show up in the web app too.

```powershell
python runner.py --video "C:\path\to\podcast.mp4"
python runner.py --youtube "https://www.youtube.com/watch?v=..." --max-clips 3
python runner.py --job-dir "..\data\jobs\<job id>" --rerank
python runner.py --thumbnails          # covers and clip posters for videos made before they existed
python runner.py --backfill-stats      # run history for videos made before the stats page (once)
python runner.py --prune-videos --dry-run   # list the videos older versions kept in finished job folders
python runner.py --prune-videos        # delete them (failed jobs keep theirs for Try again)
```

Add `--keep-video` to a run to keep `input.mp4` in the job folder afterwards, for example to benchmark it.

## Where the video goes

- **YouTube link:** `download_video` (a small CPU container on Modal) downloads it with yt-dlp straight into the `ai-podcast-clipper-jobs` volume. YouTube sometimes blocks cloud IP addresses; then the runner downloads on the laptop and uploads instead, and the job's details say "On your laptop (YouTube blocked Modal)". `YTDLP_COOKIES_FROM_BROWSER` applies to that laptop download only.
- **MP4 upload:** the browser sends it to the job folder, and the runner uploads it to the volume.
- **After a successful run** the runner deletes the video from the volume and from the job folder. After a failed run the volume copy is deleted too, but a video in the job folder stays, so **Try again** does not download or upload twice.

## Re-rank

**Re-rank** (in the app, or `--rerank` above) reuses the saved transcript: no transcription, only Gemini and rendering run again. A YouTube video is downloaded on Modal again (about a minute). For an uploaded MP4, Re-rank asks for the same file again (or drop it on the home page and choose Re-rank); it must match the original's fingerprint. The current clips stay until the new ones have downloaded. Use it after a Gemini outage left fallback clips, or after editing `prompts/viral_moments.md`.

## Preview ranking locally

Runs only the Gemini step on a finished job, with no GPU and no rendering:

```powershell
$env:GEMINI_API_KEY = "<your key>"
python rank_moments.py --list-models                           # model IDs your key can use
python rank_moments.py "..\data\jobs\<job id>" --max-clips 5   # writes moments.preview.json
```

## How a job runs in the cloud

First the video reaches the volume (see [Where the video goes](#where-the-video-goes)). Then `process_job`, a CPU function, runs the job in three steps:

1. **Transcribe**: one GPU container (`Transcriber`) runs Parakeet on the whole video.
2. **Rank**: Gemini picks the moments from the CPU function, so no GPU is billed while it ranks.
3. **Render**: one GPU container (`Renderer`) per clip, all at the same time. Each one seeks straight to its clip in the video on the volume, finds the speaker with LR-ASD, and encodes the clip once on the GPU's NVENC encoder (libx264 if there is none), with subtitles and the audio fade.

Flash frames are removed first (`render.flash_frames`): 1-3 frames of another shot between two hard cuts, found on tiny thumbnails of every frame. Every pass (face detection, speaker scoring, encoding) sees the frame before the flash instead, so face tracks run straight through it and the clip shows a 40-120 ms freeze instead of a jump. A camera change right after a flash stays a hard cut.

The crop is then steadied (`render.steady_faces`). Inside each shot of the source video, a stretch shorter than `SPEAKER_MIN_HOLD_SECONDS` goes to the stretch next to it:
- A neighbouring speaker who is still on screen keeps being followed; if both neighbours qualify, the one with the higher speaking score wins.
- Otherwise the nearest crop is held still, for example over a frame where the face was briefly lost.

Cuts in the source are never crossed, so a cut to another camera still switches immediately.

A job's details in the app show its processing time and estimated Modal cost (`costs.py`: GPU, CPU and memory per container, using Modal's October 2026 prices). The Modal dashboard has the real bill.

## Stats page

The chart icon in the header opens `/stats`: Modal spend (all time and this month against the Starter plan's $30 credit), cost and end-to-end time per video (first run plus re-ranks), where a run's time goes (download and upload on the laptop, the cloud work including a cloud download, saving clips), and average step times by podcast length. It refreshes every few seconds while a video is processing.

- **Run history.** When a run ends (first run, re-rank or failure), the runner appends it to `runs` in `job.json`: mode, result, start and end time, the step timings and the cost (`run_history.py`).
- **Older videos.** `python runner.py --backfill-stats` rebuilds `runs` once from each job's `runner.log`, which keeps every run's lines. It skips videos that already have a history (`--force` rebuilds them).
- **Estimated costs.** Runs from before the parallel renderer ran everything in one GPU container and logged no cost. Their cost is estimated from the logged GPU time at the same prices and marked "est.". The first version (WhisperX, before Parakeet) ran on an L40S, later ones on an A10. Failed runs that stopped before reporting a cost count as $0.
- **Averages.** "Current pipeline" uses only runs where every clip rendered on its own GPU; "All runs" includes the older ones. End to end counts first runs that made clips; Find moments and Render include re-ranks.

## Choose a GPU

`benchmark_gpu.py` renders clips from one of your finished jobs on several GPUs and prints the time and estimated cost of each. It runs as a separate, temporary app and does not touch the deployed one:

```powershell
modal run benchmark_gpu.py --job-dir "..\data\jobs\<job id>"                          # L4, A10, L40S, 2 clips each
modal run benchmark_gpu.py --job-dir "..\data\jobs\<job id>" --gpus "A10,L40S" --clips 5
modal run benchmark_gpu.py --job-dir "..\data\jobs\<job id>" --parity                 # old vs new crop decisions
modal run benchmark_gpu.py --job-dir "..\data\jobs\<job id>" --preview 2 --gpus none # re-render clip_2 into <job>\preview
```

`--preview` re-renders some of a job's clips (same moments, comma-separated clip numbers) with the current code into `<job dir>/preview/`, without touching the job. Use it to compare a clip before and after a change. It also prints how many crop changes the speaker hold removed.

`--parity` renders the best clip on `CLIPPER_GPU` the old way (`Columbia_test.py` in its own process) and the new way, each twice. Add `--gpus none` to skip the GPU comparison. It prints three lines of the share of frames with the same crop decision (follow a speaker or show the whole frame), frames on another person, and the mean crop-centre shift in pixels of the 1080-px-wide clip:

- **old vs old** and **new vs new**: how much each path varies between runs on its own.
- **old vs new**: the change. The two are lined up by picture first, since the old cut often starts one frame earlier.

It then lists every stretch where old and new crop differently, with its time in the video. `<job dir>/parity/` gets `report.json` and a side-by-side still of each stretch (old left, new right), so you can see which one framed the person talking.

Put the winner in `CLIPPER_GPU`, then `modal deploy main.py`.

## Logs

- GPU: `modal app logs ai-podcast-clipper`, or modal.com → Apps → ai-podcast-clipper
- Laptop: `data/jobs/<job id>/runner.log`

## Cancel a running job

Stopping `npm run dev` does not stop a job. To cancel one:

```powershell
# 1. stop the runner(s) on this laptop
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*runner.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2. stop the job's containers: process_job, then any Transcriber or Renderer ones
#    (an empty list = nothing running, nothing billed)
modal container list
modal container stop <container-id>          # never `modal app stop`: that undeploys the app

# 3. remove the job's files from the Modal volume, then locally
modal volume rm ai-podcast-clipper-jobs /<job-id> -r
Remove-Item -Recurse -Force ..\data\jobs\<job-id>
```

## Tests

```powershell
pip install pytest
python -m pytest tests -q
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| "YouTube asked to confirm you're not a bot" | YouTube blocked the laptop download too. Set `YTDLP_COOKIES_FROM_BROWSER=firefox` (or `edge`/`chrome`) in the frontend `.env`, then **Try again** |
| Every YouTube video says "On your laptop" in its details | YouTube is blocking Modal's download; `runner.log` has the reason. Bump `YTDLP_VERSION` and `modal deploy main.py`, or set `YOUTUBE_DOWNLOAD=laptop` to skip the cloud attempt. If the reason is "download_video not found", run `modal deploy main.py` |
| "The backend Python environment wasn't found" | Create the backend `.venv` (setup step 1) or set `RUNNER_PYTHON` in the frontend `.env` |
| App or secret `ai-podcast-clipper` not found | Run `modal deploy main.py`, or create the secret (setup step 3) |
| Clips marked "picked from the transcript" | Gemini was busy. Click **Re-rank** later, or check the model IDs with `rank_moments.py --list-models` |
| HTTP 429 from Hugging Face during the first deploy | Add `HF_TOKEN` to the Modal secret and deploy again |
| Modal auth or token errors | Run `modal setup` again in the backend venv |

## Transcription accuracy

Measured on this project's podcasts as the difference from earlier WhisperX transcripts (Andrew Ng talk / a conversation with a child): Parakeet v2 5.2% / 11.5%, Parakeet v3 6.6% / 12.4%. Parakeet v2 is used; on an A10, 30 minutes of audio takes about 16 seconds.
