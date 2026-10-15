# AI Podcast Clipper (personal local tool)

A web app that turns a long podcast or interview into ready-to-post short clips. Give it a YouTube link or an MP4 and it:

1. transcribes the audio with word-level timestamps (WhisperX),
2. asks Gemini to pick up to 5 self-contained moments of 30-60 seconds (question-and-answer exchanges and stories),
3. follows whoever is speaking with face tracking (LR-ASD) and reframes each moment to a vertical 9:16 video,
4. burns in word-timed subtitles,

and saves each result as an MP4 you can upload to YouTube Shorts, Instagram Reels or TikTok. The heavy models run on a rented cloud GPU ([Modal](https://modal.com)); the web app, job queue and finished clips live on your own machine.

Turns a full podcast into vertical, subtitled clips for YouTube Shorts / Reels / TikTok.
A single-user, local version of [Andreas Trolle's AI Podcast Clipper SaaS](https://github.com/Andreaswt/ai-podcast-clipper-saas):
no accounts, payments, S3 or database. Everything is stored on your laptop; only the GPU work runs on [Modal](https://modal.com).

```
Browser (localhost:3000)  →  Next.js app  →  runner.py (laptop)  →  Modal L40S GPU
 paste YouTube link / .mp4    saves job to       yt-dlp download,       WhisperX → Gemini picks moments →
                              data/jobs/<id>/    upload, collect clips  LR-ASD speaker crop (9:16) → subtitles
```

## Requirements

- Windows, macOS or Linux (no local GPU needed)
- [Node.js](https://nodejs.org) 20+
- [Python 3.12](https://www.python.org/downloads/) and Git
- A [Modal](https://modal.com) account (Starter plan: $30/month free credits; an L40S costs about $2/hour)
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

The first deploy builds the CUDA image and takes a while. Re-run `modal deploy main.py` whenever you change `main.py` or anything in `asd/`.
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

Open http://localhost:3000, then paste a YouTube link or upload an MP4 (up to 4 GB).
Finished clips (up to 5) appear in the Clips tab and in `data/jobs/<job id>/clips/`; `runner.log` in the job folder has the details.
Live GPU logs: `modal app logs ai-podcast-clipper` or modal.com → Apps → ai-podcast-clipper.

Without the UI (from `ai-podcast-clipper-backend`, venv active; jobs still show up in the web UI):

```powershell
python runner.py --video "C:\path\to\podcast.mp4"
python runner.py --youtube "https://www.youtube.com/watch?v=..." --max-clips 3
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
