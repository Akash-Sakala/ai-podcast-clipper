# Advanced usage

Everything beyond setup and everyday use. Backend commands run from `ai-podcast-clipper-backend` with the venv active (`.venv\Scripts\Activate.ps1`).

## Where files are

Each video is a job folder in `data/jobs/<job id>/` (or under `DATA_DIR` if you set it):

| File | Contents |
|---|---|
| `job.json` | Status, progress, clip details and the Gemini `ranking` report shown in the app |
| `input.mp4` | The source video |
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
| `CLIPPER_GPU` | `A10` | GPU for the cloud pipeline. L4 is cheaper and slower, L40S faster |
| `MIN_CLIPS` | `3` | A job never ends with fewer clips: missing ones are picked from the transcript and marked "fallback" |
| `CLIP_MIN_SECONDS` / `CLIP_MAX_SECONDS` | `30` / `60` | Clip length limits |
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
```

## Re-rank

**Re-rank** (in the app, or `--rerank` above) reuses the saved video and transcript: no download and no transcription, only Gemini and rendering run again. The current clips stay until the new ones have downloaded. Use it after a Gemini outage left fallback clips, or after editing `prompts/viral_moments.md`.

## Preview ranking locally

Runs only the Gemini step on a finished job, with no GPU and no rendering:

```powershell
$env:GEMINI_API_KEY = "<your key>"
python rank_moments.py --list-models                           # model IDs your key can use
python rank_moments.py "..\data\jobs\<job id>" --max-clips 5   # writes moments.preview.json
```

## Logs

- GPU: `modal app logs ai-podcast-clipper`, or modal.com → Apps → ai-podcast-clipper
- Laptop: `data/jobs/<job id>/runner.log`

## Cancel a running job

Stopping `npm run dev` does not stop a job. To cancel one:

```powershell
# 1. stop the runner(s) on this laptop
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*runner.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2. stop the GPU container (an empty list = nothing running, nothing billed)
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
| "YouTube asked to confirm you're not a bot" | Set `YTDLP_COOKIES_FROM_BROWSER=firefox` (or `edge`/`chrome`) in the frontend `.env`, then **Try again** |
| "The backend Python environment wasn't found" | Create the backend `.venv` (setup step 1) or set `RUNNER_PYTHON` in the frontend `.env` |
| App or secret `ai-podcast-clipper` not found | Run `modal deploy main.py`, or create the secret (setup step 3) |
| Clips marked "picked from the transcript" | Gemini was busy. Click **Re-rank** later, or check the model IDs with `rank_moments.py --list-models` |
| HTTP 429 from Hugging Face during the first deploy | Add `HF_TOKEN` to the Modal secret and deploy again |
| Modal auth or token errors | Run `modal setup` again in the backend venv |

## Transcription accuracy

Measured on this project's podcasts as the difference from earlier WhisperX transcripts (Andrew Ng talk / a conversation with a child): Parakeet v2 5.2% / 11.5%, Parakeet v3 6.6% / 12.4%. Parakeet v2 is used; on an A10, 30 minutes of audio takes about 16 seconds.
