# AI Podcast Clipper (personal local tool)

A web app that turns a long podcast or interview into ready-to-post short clips. Give it a YouTube link or an MP4 and it:

1. transcribes the (English) audio with word-level timestamps using [NVIDIA Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) on a cloud GPU,
2. asks Gemini to score candidate moments of 30-60 seconds on a virality rubric (hook, standalone, payoff, emotion, value, shareability) and keeps the **best 5, ranked**, from anywhere in the video,
3. follows whoever is speaking with face tracking (LR-ASD) and reframes each moment to a vertical 9:16 video,
4. burns in word-timed subtitles,

and saves each result as an MP4 you can upload to YouTube Shorts, Instagram Reels or TikTok. The heavy models run on a rented cloud GPU ([Modal](https://modal.com)); the web app, job queue and finished clips live on your own machine.

Turns a full podcast into vertical, subtitled clips for YouTube Shorts / Reels / TikTok.
A single-user, local version of [Andreas Trolle's AI Podcast Clipper SaaS](https://github.com/Andreaswt/ai-podcast-clipper-saas):
no accounts, payments, S3 or database. Everything is stored on your laptop; only the GPU work runs on [Modal](https://modal.com).

```
Browser (localhost:3000)  →  Next.js app  →  runner.py (laptop)  →  Modal A10 GPU (one container)
 paste YouTube link / .mp4    saves job to       yt-dlp download,       Parakeet v2 transcript → Gemini ranks
                              data/jobs/<id>/    upload, live progress, moments → LR-ASD speaker crop (9:16)
                                                 collect clips          → subtitles
```

## Requirements

- Windows, macOS or Linux (no local GPU needed)
- [Node.js](https://nodejs.org) 20+
- [Python 3.12](https://www.python.org/downloads/) and Git
- A [Modal](https://modal.com) account (Starter plan: $30/month free credits; an A10 costs about $1.10/hour)
- A [Gemini API key](https://aistudio.google.com/apikey)

## Setup (one time)

Commands are PowerShell; on macOS/Linux activate the venv with `source .venv/bin/activate`.

**1. Backend**

```powershell
cd ai-podcast-clipper-backend
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-local.txt
copy .env.example .env
```

**2. Face-detector weights** (LR-ASD is already included in `asd/`; only this 90 MB file is downloaded)

```powershell
pip install gdown
gdown 1KafnHz7ccT-3IyddBsL5yi2xGtxAKypt -O asd\model\faceDetector\s3fd\sfd_face.pth
```

**3. Deploy the GPU backend to Modal** (from `ai-podcast-clipper-backend`, venv active)

```powershell
modal setup
modal secret create ai-podcast-clipper-secret GEMINI_API_KEY=<your key>
modal deploy main.py
```

The first deploy builds the CUDA image with Parakeet v2 (~2.5 GB of ONNX weights) baked in, and takes a while. Re-run `modal deploy main.py` whenever you change `main.py` or anything in `asd/`.
To use another Gemini model, add `GEMINI_MODEL=<model id>` to the secret (re-create it with `--force`).

**4. Frontend**

```powershell
cd ..\ai-podcast-clipper-frontend
npm install
```

Optional settings go in `ai-podcast-clipper-frontend/.env` (see `.env.example`):

- `DATA_DIR`: where job folders go (default `data/` in the repo). Use a folder outside OneDrive, e.g. `C:\podcast-clipper-data`, so large videos aren't synced.
- `RUNNER_PYTHON`: Python used to run `runner.py` (default: the backend `.venv`).
- `YTDLP_COOKIES_FROM_BROWSER`: `firefox`, `edge` or `chrome`, to use your browser's YouTube login if YouTube asks to confirm you're not a bot.

## Run

```powershell
cd ai-podcast-clipper-frontend
npm run dev
```

Open http://localhost:3000 and paste a YouTube link, click **Upload MP4**, or drop an MP4 (up to 4 GB) onto the input bar.

The app has two pages:
- **Home:** the input bar, then **Your videos**: one row per video with its status. A running video shows a slim progress bar and its current step ("Rendering clip 3 of 5"), a finished one its clip count, and a failed one a **Try again** button. You can add more videos while others run, and a toast with **View clips** appears when one finishes.
- **Clips page** (click a row, `/videos/<job id>`): the full progress strip while it runs, then the clips, best first. The amber badge shows the rank and score (`#1 86`); hover or focus it for the six-part score breakdown and Gemini's reason. Each clip has **Copy title** and **Download**; **Download all** saves one ZIP named by rank and title. Failures are explained in plain language with **Try again** (a downloaded YouTube video is reused), and clips picked from the transcript because Gemini was busy are flagged with a **Re-rank** button. **Details** (the ⓘ button) shows when it was added, the source, video length, transcription speed, the Gemini model and processing time.
- The theme menu (top right) switches between Light, Dark and System (default).

Files are in `data/jobs/<job id>/` (`clips/`, `moments.json` with every score and reason, `runner.log` for troubleshooting).

Transcription is Parakeet v2 on the GPU (English only for now). Measured on this project's podcasts (difference from the earlier WhisperX transcripts): Parakeet v2 5.2% / 11.5%, Parakeet v3 6.6% / 12.4% (Andrew Ng talk / child conversation); on an A10, 30 minutes of audio took about 16 s.

Live GPU logs: `modal app logs ai-podcast-clipper` or modal.com → Apps → ai-podcast-clipper.

Without the UI (from `ai-podcast-clipper-backend`, venv active; jobs still show up in the web UI):

```powershell
python runner.py --video "C:\path\to\podcast.mp4"
python runner.py --youtube "https://www.youtube.com/watch?v=..." --max-clips 3
```

### Settings

Backend settings live in `ai-podcast-clipper-backend/.env` (copy `.env.example`; it is git-ignored). Values set in the shell override the file.

| Setting | Default | Meaning |
|---|---|---|
| `CLIPPER_GPU` | `A10` | GPU for the cloud pipeline (Parakeet, Gemini call, rendering) |
| `MIN_CLIPS` | `3` | A job never ends with fewer clips: if Gemini returns fewer usable moments, the rest are picked from the transcript and labelled "fallback" |
| `CLIP_MIN_SECONDS` / `CLIP_MAX_SECONDS` | `30` / `60` | Clip length limits |
| `GEMINI_MODELS` | `gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite` | Models tried in order (`GEMINI_MODEL` in the Modal secret, if set, goes first). Check the IDs with `python rank_moments.py --list-models` |
| `GEMINI_BUSY_RETRIES` / `GEMINI_BUSY_WAIT_SECONDS` | `1` / `3` | A busy model (429/503/504) gets this many quick retries, then the next model is used |
| `GEMINI_THINKING_LEVEL` | `low` | Gemini 3 thinking level for ranking (`minimal`, `low`, `medium`, `high`; empty = model default) |
| `GEMINI_TEMPERATURE` | empty | Empty = model default (recommended for Gemini 3) |
| `GEMINI_TIMEOUT_SECONDS` / `RANKING_TIME_LIMIT_SECONDS` | `120` / `240` | Per-request timeout and the total ranking budget; after it the transcript fallback is used |

All of these are read by `modal deploy main.py`, so redeploy after changing them. When a job used the fallback or Gemini failed, the job's section says so and `job.json` has a `ranking` report. Requests are streamed with a low thinking level and ask for 8 candidates, which keeps Gemini calls short.

### Re-rank & re-render

Finished jobs show a **Re-rank** button. It reuses the job's saved video and transcript (no YouTube download, no transcription), asks Gemini again and re-renders the clips on the GPU; the current clips stay until the new ones are downloaded. Use it after a Gemini outage left a job with fallback clips, or after editing `prompts/viral_moments.md`. From the terminal: `python runner.py --job-dir "..\data\jobs\<job id>" --rerank`.

### Preview ranking locally (no GPU, no rendering)

```powershell
$env:GEMINI_API_KEY = "<your key>"
python rank_moments.py --list-models                                # model IDs your key can use
python rank_moments.py "..\data\jobs\<job id>" --max-clips 5     # writes moments.preview.json
```

### Tests

```powershell
pip install pytest
python -m pytest tests -q
```

## Stop

Stopping `npm run dev` (Ctrl+C) does **not** stop a running job; the runner and the Modal GPU call keep going. To cancel a job (from `ai-podcast-clipper-backend`, venv active):

```powershell
# 1. stop the runner(s) on this laptop
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*runner.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 2. stop the GPU container (an empty list = nothing running, nothing billed)
modal container list
modal container stop <container-id>          # never `modal app stop` - that undeploys the app

# 3. remove the job's files from the Modal volume, then from the UI
modal volume rm ai-podcast-clipper-jobs /<job-id> -r
Remove-Item -Recurse -Force ..\data\jobs\<job-id>
```

## Credits & license

MIT. See [LICENSE.MD](LICENSE.MD). Based on [Andreaswt/ai-podcast-clipper-saas](https://github.com/Andreaswt/ai-podcast-clipper-saas) by Andreas Trolle;
active speaker detection by [LR-ASD](https://github.com/Junhua-Liao/LR-ASD) (Liao Junhua, MIT). What was changed: see [CHANGES.md](CHANGES.md).
