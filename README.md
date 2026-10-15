# AI Podcast Clipper (personal local tool)

![alt text](thumbnail.png)

Turns a full podcast into vertical, subtitled short clips for YouTube Shorts / Reels / TikTok.
This is a single-user version of [Andreas Trolle's AI Podcast Clipper SaaS](https://github.com/Andreaswt/ai-podcast-clipper-saas):
there are no accounts, credits, payments, S3, database or queue. Everything is stored on your laptop.
Only the GPU work runs in the cloud, on [Modal](https://modal.com).

## How it works

```
Browser (localhost:3000)
  │  paste a YouTube link or upload an .mp4
  ▼
Next.js app  ── saves the video to  data/jobs/<job id>/input.mp4
  │             and starts the runner in the background
  ▼
runner.py (your laptop)
  │  1. downloads the YouTube video (yt-dlp)            [YouTube jobs only]
  │  2. uploads input.mp4 to the Modal Volume "ai-podcast-clipper-jobs"
  │  3. calls the deployed GPU class  AiPodcastClipper.process_job
  │  4. downloads the clips to  data/jobs/<job id>/clips/
  ▼
main.py on a Modal L40S GPU
     WhisperX transcription → Gemini picks the best 30-60 s moments →
     LR-ASD active-speaker detection → 9:16 crop on the speaker → subtitles (FFmpeg)
```

Each job is a folder:

```
data/jobs/20261015-184647-d1ef80/
  job.json          status shown in the UI (queued → downloading → uploading → processing → saving → done/failed)
  input.mp4         the source video
  clips/clip_0.mp4  finished clips (up to 5)
  moments.json      the moments Gemini picked
  transcript.json   word-level transcript
  runner.log        everything the runner printed - check this when a job fails
```

## Requirements

- Windows, macOS or Linux. No NVIDIA GPU needed.
- [Node.js](https://nodejs.org) 20+ (also used by yt-dlp to solve YouTube's JavaScript challenges)
- [Python 3.12](https://www.python.org/downloads/windows/) (3.12.10 is the last 3.12 with a Windows installer)
- Git
- A [Modal](https://modal.com) account. The Starter plan includes $30/month of free credits; an L40S costs about $2/hour.
- A [Gemini API key](https://aistudio.google.com/apikey)

## One-time setup

### 1. Backend (Python, Modal)

```powershell
cd ai-podcast-clipper-backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-local.txt
```

`requirements-local.txt` is all your laptop needs (modal, yt-dlp, a bundled ffmpeg).
`requirements.txt` holds the GPU packages (torch, WhisperX, …); those are installed only inside the Modal container.

**Active speaker detection model ([LR-ASD](https://github.com/Junhua-Liao/LR-ASD))** - clone it into a folder named `asd`,
download the face-detector weights and patch one line that breaks with NumPy 2:

```powershell
git clone https://github.com/Junhua-Liao/LR-ASD.git asd
pip install gdown
gdown 1KafnHz7ccT-3IyddBsL5yi2xGtxAKypt -O asd\model\faceDetector\s3fd\sfd_face.pth
(Get-Content asd\model\faceDetector\s3fd\box_utils.py) -replace 'astype\(np\.int\)', 'astype(int)' | Set-Content asd\model\faceDetector\s3fd\box_utils.py
```

**Modal:**

```powershell
modal setup                                                   # log in (opens the browser)
modal secret create ai-podcast-clipper-secret GEMINI_API_KEY=<your key>
modal deploy main.py                                          # run from the backend folder
```

- The first deploy builds the CUDA image and takes a while. Later deploys are fast.
- To use a different Gemini model, add `GEMINI_MODEL=<model id>` to the secret
  (`modal secret create ai-podcast-clipper-secret GEMINI_API_KEY=... GEMINI_MODEL=... --force`).
  The default is set in `DEFAULT_GEMINI_MODEL` in `main.py`.
- **Re-run `modal deploy main.py` every time you change `main.py` or anything in `asd/`.**

Models used (all downloaded automatically inside Modal and cached in the `ai-podcast-clipper-model-cache` volume):

| Model | Purpose |
|---|---|
| WhisperX `large-v2` + bundled VAD | Transcription |
| wav2vec2 English alignment | Word-level timestamps for subtitles |
| Gemini (API) | Picks the viral moments |
| LR-ASD `finetuning_TalkSet.model` + S3FD | Active speaker / face detection for the crop |

### 2. Frontend (Next.js)

```powershell
cd ai-podcast-clipper-frontend
npm install
```

No `.env` is needed with the standard folder layout. Optional settings are in `.env.example`:

- `DATA_DIR`: where job folders go. The default is `data/` in the repo. The repo is inside OneDrive,
  so consider a folder outside it, e.g. `C:\podcast-clipper-data`, so large videos aren't synced.
- `RUNNER_PYTHON`: path to the Python that runs `runner.py`. The default is the backend `.venv`.

## Running

```powershell
cd ai-podcast-clipper-frontend
npm run dev
```

Open http://localhost:3000.

- **New job:** paste a YouTube link, or upload an MP4 (up to 4 GB).
- The jobs table refreshes itself while jobs run.
- **Clips tab:** play or download the clips. They're also in `data/jobs/<job id>/clips/`.
- Live GPU logs are at modal.com → Apps → ai-podcast-clipper.

A job keeps running if you stop the dev server. Start it again and the status catches up.

### Command line (no UI)

```powershell
cd ai-podcast-clipper-backend
.venv\Scripts\Activate.ps1
python runner.py --video "C:\path\to\podcast.mp4"
python runner.py --youtube "https://www.youtube.com/watch?v=..."
python runner.py --youtube "..." --max-clips 3
```

Jobs started from the command line show up in the web UI too.

### Watching and stopping jobs

Stopping `npm run dev` does **not** stop a running job - the runner and the Modal GPU call keep going.

```powershell
# live GPU logs (start a job first; run from the backend folder with the venv active)
modal app logs ai-podcast-clipper

# 1. is a runner still running on this laptop? (one job shows as 2 python.exe processes: venv launcher + real Python)
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*runner.py*' } | Select-Object ProcessId, CommandLine
Stop-Process -Id <pid>, <pid> -Force

# 2. is a GPU container still running? (an empty table = nothing running, nothing billed)
modal container list
modal container stop <container-id>        # do NOT use `modal app stop` - that undeploys the app

# 3. remove the job's leftover files from the Modal volume (skipped when the runner is force-stopped)
modal volume ls ai-podcast-clipper-jobs
modal volume rm ai-podcast-clipper-jobs /<job-id> -r

# 4. remove the job from the UI (it shows as Failed once its runner is gone)
Remove-Item -Recurse -Force ..\data\jobs\<job-id>
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| Job fails with `Python not found at ...` | Create the backend `.venv` (step 1) or set `RUNNER_PYTHON` in the frontend `.env` |
| `NotFoundError ... ai-podcast-clipper` / app not found | Run `modal deploy main.py` |
| `Secret ai-podcast-clipper-secret not found` | Create the secret (step 1) |
| Token / auth errors from Modal | Run `modal setup` again in the backend venv |
| "No clips were produced - Gemini found no suitable moments" | The video has no clear Q&A or story segments. Try another video. |
| Gemini 404 / model not found | Set `GEMINI_MODEL` to a current model ([list](https://ai.google.dev/gemini-api/docs/models)) |
| Clip errors mentioning tracks/scores | LR-ASD crashed. Check the modal.com logs. Usually the `box_utils.py` patch or `sfd_face.pth` is missing. Re-deploy after fixing. |
| YouTube download fails with `HTTP Error 403: Forbidden` | The runner already retries 3 times with fresh links. If it still fails, wait a few minutes and start the job again, update yt-dlp (`pip install -U "yt-dlp[default]"` in the backend venv), or download the video yourself and upload the file |
| YouTube says "Sign in to confirm you're not a bot" | Add `YTDLP_COOKIES_FROM_BROWSER="firefox"` (or `edge`/`chrome`) to the frontend `.env` so yt-dlp uses your browser's YouTube login. Firefox works best; Chrome/Edge may need to be closed first |
| `torchaudio has no attribute AudioMetaData` / container crash-looping | Torch and WhisperX versions are out of sync. Keep the `torch`, `torchaudio`, `torchvision` and `whisperx` pins in `requirements.txt` together, then re-deploy |
| `FileNotFoundError: /jobs/<id>/input.mp4 not found in the jobs volume` right after a deploy | A request from a job you stopped was still queued on Modal and ran once the app worked again. Harmless - start a new job |
| `Weights only load failed` in Modal logs | Add `weights_only=False` to the `torch.load` calls in `asd/ASD.py` and `asd/model/faceDetector/s3fd/__init__.py`, then re-deploy |

## What I changed vs. upstream

Forked from [Andreaswt/ai-podcast-clipper-saas](https://github.com/Andreaswt/ai-podcast-clipper-saas) (MIT) and turned into a single-user, local-first tool.

**Removed**
- The multi-user SaaS layer: NextAuth login, Stripe billing and credits, Prisma/Postgres, the Inngest queue, S3 uploads and the bearer-token FastAPI endpoint.

**Added**
- `runner.py`: downloads from YouTube with yt-dlp (H.264 up to 1080p, chunked, retries with fresh signed URLs, optional browser cookies), uploads to a Modal Volume, calls the deployed GPU class, downloads the clips and cleans up remote files. It also works as a standalone CLI.
- A file-based job store (`data/jobs/<id>/job.json`, atomic writes) with detection of runner processes that died, so jobs never hang as "processing".
- A frontend for YouTube links or MP4 uploads (up to 4 GB, with progress), a live job table, and a clip player and download, with validated job and clip IDs.

**Changed in the GPU backend (`main.py`)**
- S3 and the HTTP endpoint replaced by a Modal Volume exchange and a `process_job` method.
- One failing clip no longer fails the whole job (upstream had no per-clip error handling), `max_clips` is a parameter (upstream hard-coded 5) and the job timeout went from 15 to 60 minutes.
- Gemini model is configurable via `GEMINI_MODEL`, and Gemini's JSON reply is parsed defensively.
- `torch`, `torchaudio`, `torchvision` and `whisperx` pinned to a set that resolves together (unpinned installs crash on `torchaudio.AudioMetaData`).
- LR-ASD vendored into `asd/` with a NumPy 2 fix.

**Unchanged from upstream:** the overall pipeline design (WhisperX transcription, Gemini moment selection, LR-ASD active-speaker crop, FFmpeg subtitles) and the crop and subtitle rendering code.

## Credits

- Original project by Andreas Trolle (MIT). See `LICENSE.MD`.
- Active speaker detection: [LR-ASD](https://github.com/Junhua-Liao/LR-ASD) by Liao Junhua (MIT), in `ai-podcast-clipper-backend/asd/`.

## License

MIT. See [LICENSE.MD](LICENSE.MD). Based on [Andreaswt/ai-podcast-clipper-saas](https://github.com/Andreaswt/ai-podcast-clipper-saas).
