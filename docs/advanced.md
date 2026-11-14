# Advanced usage

Everything beyond setup and everyday use. Backend commands run from `ai-podcast-clipper-backend` with the venv active (`.venv\Scripts\Activate.ps1`).

## Where files are

Each video is a job folder in `data/jobs/<job id>/` (or under `DATA_DIR` if you set it):

| File | Contents |
|---|---|
| `job.json` | Status, progress, clip details, the Gemini `ranking` report and `runs`, the history of every run (stats page) |
| `input.mp4` | An uploaded video, only until its run finishes (kept after a failed run, for **Try again**). YouTube videos download to a temp folder instead (see [Where the video goes](#where-the-video-goes)) |
| `thumbnail.jpg` | Cover in the video list: YouTube's own thumbnail, or a frame from an uploaded video |
| `transcript.json` | Word-level Parakeet transcript (reused by Re-rank) |
| `moments.json` | Every candidate moment with its scores and reason |
| `clips/clip_0.mp4` … | Finished clips, `clip_0` = best |
| `clips/clip_0.jpg` … | Poster still for each clip, shown before the clip loads |
| `runner.log` | Everything the runner printed; check this first when a job fails |

The stats page has its own file, `data/stats/runs.jsonl`: one line per run, kept when a video's job folder is deleted (see [Stats page](#stats-page)).

**Delete a video:** stop `npm run dev`, delete its folder in `data/jobs`, start the app again. Its cost and times stay on the stats page.

## Settings

Backend settings live in `ai-podcast-clipper-backend/.env` (copied from `.env.example`, git-ignored). Values set in the shell override the file. They are read by `modal deploy main.py`, so **redeploy after changing them**.

The app's **Settings page** (gear icon) saves `data/settings.json`: currency, clips per video, Most replayed, audience weight and SponsorBlock. The runner reads it before every run, and its values win over `MOST_REPLAYED`, `SPONSORBLOCK` and `REPLAY_WEIGHT` below. No redeploy is needed for any of them.

| Setting | Default | Meaning |
|---|---|---|
| `YOUTUBE_IPV4` | `on` | Download YouTube videos over IPv4, which avoids repeated timeouts to one YouTube server on a broken IPv6 route. `off` lets the system choose (runner, no redeploy) |
| `MOST_REPLAYED` | `on` | Use YouTube's Most replayed graph: replay hotspots become clip candidates and each clip gets an audience score. Read by the runner, no redeploy needed. See [Most replayed and SponsorBlock](#most-replayed-and-sponsorblock) |
| `SPONSORBLOCK` | `on` | Keep SponsorBlock-marked sponsor reads, self-promotion, subscribe reminders, intros, outros and previews out of clips (runner, no redeploy) |
| `REPLAY_WEIGHT` | `0.4` | Share of the final score from the audience, 0-1. `0.4` = 60% Gemini, 40% viewers; `0` = Gemini only (runner, no redeploy) |
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

Frontend settings (`ai-podcast-clipper-frontend/.env`, all optional): `DATA_DIR`, `RUNNER_PYTHON`, and YouTube cookies (see [YouTube cookies](#youtube-cookies)): `YTDLP_COOKIES_FILE` or `YTDLP_COOKIES_FROM_BROWSER`.

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
python runner.py --backfill-replay     # Most replayed graph + SponsorBlock for older YouTube videos (once; --force re-reads)
python runner.py --modal-bill          # read this month's Modal bill into data/stats (the stats page also does this)
python runner.py --check-rates         # Modal's live prices as the cost estimate reads them ("source": "modal" or "built-in")
python runner.py --prune-videos --dry-run   # list the videos older versions kept in finished job folders
python runner.py --prune-videos        # delete them (failed jobs keep theirs for Try again)
```

Add `--keep-video` to a run to keep `input.mp4` in the job folder afterwards, for example to benchmark it.

## Where the video goes

- **YouTube link:** the runner downloads it with yt-dlp (H.264 up to 1080p) into `%TEMP%\ai-podcast-clipper\<job id>`, outside OneDrive so sync can't lock the file, uploads it to the `ai-podcast-clipper-jobs` volume and deletes the temp folder. A dropped connection is retried with a growing pause and resumes the partial file; if the run still fails, the partial file stays, so **Try again** resumes it. Temp folders left for over two days are cleared at the next run.
- **The upload** goes to Modal in 8 MiB pieces, 4 at a time (Modal's default of 128 at once makes every piece crawl on a home connection). A piece that takes over 10 minutes, or fails, is retried, and a retry only sends the pieces Modal doesn't have yet. The progress strip shows the percentage and the time left, and "no data moving" after two minutes without progress; `runner.log` gets a line every 10% with the average speed, the stalls, and the total time.
- **Waiting for the GPU:** if the connection to Modal drops while a job runs there, the runner keeps asking for the result for up to 15 minutes (`runner.log`: "Lost the connection to Modal"), instead of failing the run.
- **MP4 upload:** the browser sends it to the job folder, and the runner uploads it to the volume.
- **After a successful run** the runner deletes the video from the volume and from the job folder. After a failed run the volume copy is deleted too, but a video in the job folder stays, so **Try again** does not download or upload twice.

## Most replayed and SponsorBlock

Before every run of a YouTube video (first run and re-rank), the runner spends a few seconds on the laptop:

- **Most replayed graph:** a metadata-only yt-dlp call (no download, with your YouTube cookies if set) returns YouTube's graph as 100 equal segments with a replay intensity from 0 to 1. Only videos with enough views have one, usually tens of thousands. It is read on the laptop because YouTube often strips it from pages it sends to cloud IPs. If YouTube refuses that call ("confirm you're not a bot") but the download goes through, the graph is taken from the download instead (`runner.log`: "read from the download").
- **SponsorBlock:** the hashed API (only the first 4 characters of the video id's SHA-256 leave the laptop) returns the parts viewers marked: sponsor reads, self-promotion, subscribe reminders, intros, outros, previews and a highlight point.

Both go into `job.json` (`replay`). Hotspots (`replay.find_peaks`) are the graph's peaks after three corrections: the first 45 s and last 30 s never count (everyone starts at the beginning), a bump only in the segment where a chapter starts is navigation rather than interest, and sponsor reads are flattened. A peak must stand at least 0.12 above the video's median, and peaks closer than 90 s merge.

Gemini then gets the hotspots (without their intensities) and returns one cleanly cut clip around each, scored on the transcript alone. Hotspots it skips are cut in code (about 30 s of set-up before the peak) and scored in the re-scoring call. Each clip's audience score is the best segment it covers well, on a scale where 50 is the video's median and 100 its most replayed part (parts viewers skip score below 50). Final score = (1 - `REPLAY_WEIGHT`) x Gemini + `REPLAY_WEIGHT` x audience. On a 60-minute video each segment is about 36 s, so the graph says where to clip and the transcript still decides the exact cut.

If the graph or SponsorBlock cannot be read, the run goes on without it (the reason is in `runner.log` and the job's Details). Uploaded MP4s have neither.

SponsorBlock data is licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) (non-commercial, attribution); if your channel becomes a commercial use, ask its author ([sponsor.ajay.app](https://sponsor.ajay.app)) for permission or set `SPONSORBLOCK=off`.

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

The chart icon in the header opens `/stats`, for this month or all time: Modal's bill for the month against the Starter plan's $30 credit, the average new video and re-rank (cost, time, cost per podcast hour), the average run by stage (time and cost), every run with its result and stage tooltip, and the Most replayed / SponsorBlock sentences. A time-per-video chart appears after 10 new runs. It refreshes every few seconds while a video is processing.

- **Modal's bill** comes from `modal billing summary` (works on the Starter plan). The runner saves it in `data/stats/modal-bill.json` after every run, and the page asks for a fresh one when it is older than 15 minutes. Modal's billing months are UTC calendar months.
- **Run costs** are estimates: each container's running time (plus start-up and the idle time before Modal stops it) at Modal's live rates from `modal billing rates`, saved once a day in `data/stats/rates.json`. If Modal's rate names change and can't be read, the built-in prices in `costs.py` are used (`--check-rates` shows which). The Runs card shows how this month's runs compare with what Modal charged for the app.
- **Stages:** Download (on the laptop: the download, the upload to Modal and the Most replayed lookup; no Modal cost), Transcribe, Rank (Gemini; its cost is the job's own CPU container), Render (time = the slowest clip, cost = all clip GPUs), Save (laptop, no cost) and Overhead (total minus the stages, mostly containers starting).
- **Rupees:** with Settings > Currency on rupees, each run is converted at the USD to INR rate saved when it finished (`fx.py`, `data/stats/fx.json`: Frankfurter, then ExchangeRate-API's open endpoint; no API key). The month's bill uses today's rate.

- **Run ledger.** When a run ends (first run, re-rank or failure), the runner appends one line to `data/stats/runs.jsonl` (`run_history.py`): the job's id, title and source, the mode, result, start and end time, step timings, cost and Parakeet's speed. The stats page reads only this file, so **deleting a video keeps its runs in the stats**; such videos are listed as "deleted from the app", without a link. The same run also goes into the job's own `job.json` `runs`.
- **Starting the stats over:** delete `data/stats/runs.jsonl` (or the whole `data/stats` folder). Deleting job folders alone does not reset the stats.
- **Failed runs** show where they stopped. One that ended before reporting a cost shows "—" and counts as $0. Averages count only runs that made clips, and say how many runs they are based on ("early estimate" below 3).

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

## YouTube cookies

When YouTube asks to "confirm you're not a bot", yt-dlp can use your YouTube login. Set one of these in the frontend `.env` and restart `npm run dev` (the runner reads them; it checks them once per run and carries on without cookies if they can't be read, with the reason in `runner.log`):

- **`YTDLP_COOKIES_FILE`**: a `cookies.txt` file. This is the way to use **Chrome**: Chrome (and Edge) on Windows encrypt their cookies so that other programs can't read them, so `YTDLP_COOKIES_FROM_BROWSER=chrome` fails ("Failed to decrypt with DPAPI"). Install the "Get cookies.txt LOCALLY" extension, open youtube.com while signed in, export the cookies for youtube.com, and save the file outside OneDrive and the repo, e.g. `C:\Users\<you>\podcast-clipper-cookies\youtube.txt`. The file signs in as you: keep it private. YouTube rotates cookies, so export again if the bot check comes back.
- **`YTDLP_COOKIES_FROM_BROWSER=firefox`**: Firefox's cookies can be read directly, while you are signed in to YouTube in Firefox.

The file wins when both are set.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "YouTube asked to confirm you're not a bot" | Set up [YouTube cookies](#youtube-cookies), restart `npm run dev`, then **Try again** |
| A YouTube download keeps timing out ("Read timed out", "connect timeout") | Your connection to YouTube's video servers is dropping. **Try again** resumes the download where it stopped. Check that `YOUTUBE_IPV4` is `on`, and update yt-dlp with `pip install -U "yt-dlp[default]"` |
| "The backend Python environment wasn't found" | Create the backend `.venv` (setup step 1) or set `RUNNER_PYTHON` in the frontend `.env` |
| App or secret `ai-podcast-clipper` not found | Run `modal deploy main.py`, or create the secret (setup step 3) |
| No Most replayed graph on a popular video | `runner.log` has the reason. "Sign in to confirm you're not a bot": set up [YouTube cookies](#youtube-cookies), then **Re-rank**. Otherwise YouTube shows no graph for that video yet |
| "Gemini was busy, so these clips were picked without it" | They came from the transcript or were cut around a replay peak. Click **Re-rank** later, or check the model IDs with `rank_moments.py --list-models` |
| HTTP 429 from Hugging Face during the first deploy | Add `HF_TOKEN` to the Modal secret and deploy again |
| Modal auth or token errors | Run `modal setup` again in the backend venv |

## Transcription accuracy

Measured on this project's podcasts as the difference from earlier WhisperX transcripts (Andrew Ng talk / a conversation with a child): Parakeet v2 5.2% / 11.5%, Parakeet v3 6.6% / 12.4%. Parakeet v2 is used; on an A10, 30 minutes of audio takes about 16 seconds.
