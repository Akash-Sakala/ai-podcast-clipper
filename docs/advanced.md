# Advanced usage

Everything beyond setup and everyday use. Backend commands run from `ai-podcast-clipper-backend` with the venv active (`.venv\Scripts\Activate.ps1`).

- [Where files are](#where-files-are)
- [Settings](#settings)
- [When to redeploy](#when-to-redeploy)
- [Command line](#command-line)
- [How a job runs](#how-a-job-runs)
- [Fallbacks to the whole video](#fallbacks-to-the-whole-video)
- [Where the video goes](#where-the-video-goes)
- [Most replayed and SponsorBlock](#most-replayed-and-sponsorblock)
- [Re-rank](#re-rank)
- [Preview ranking locally](#preview-ranking-locally)
- [Stats page](#stats-page)
- [Interface: motion and touch](#interface-motion-and-touch)
- [Choose a GPU](#choose-a-gpu)
- [Test deployment](#test-deployment)
- [Logs](#logs)
- [Cancel or delete a video](#cancel-or-delete-a-video)
- [Tests and CI](#tests-and-ci)
- [YouTube cookies](#youtube-cookies)
- [Troubleshooting](#troubleshooting)

## Where files are

Each video is a job folder in `%LOCALAPPDATA%\ai-podcast-clipper\data\jobs\<job id>\` (outside OneDrive; `DATA_DIR` in the frontend `.env` overrides it). Versions before 2026-11-28 kept it in the repo's `data\` folder, which the app keeps using until you move it once with `scripts\move-data.ps1`. The launcher's log is `%LOCALAPPDATA%\ai-podcast-clipper\app.log`.

| File | Contents |
|---|---|
| `job.json` | Status, progress, clip details, the Gemini `ranking` report, `runs` (the history of every run), `replay`, and run state such as `pendingSave` and `modalCallId` (see [job.json fields](#jobjson-fields)) |
| `input.mp4` | An uploaded video, only until its run finishes (kept after a failed run, for **Try again**); a successful run moves it to the kept videos. YouTube videos download to a temp folder instead (see [Where the video goes](#where-the-video-goes)) |
| `thumbnail.jpg` | Cover in the video list: YouTube's own thumbnail, or a frame from an uploaded video |
| `transcript.json` | Word-level Parakeet transcript, saved as soon as phase 1 has transcribed (reused by Re-rank and **Try again**) |
| `moments.json` | Every candidate moment with its scores and reason |
| `clips/clip_0.mp4` … | Finished clips, `clip_0` = best |
| `clips/clip_0.jpg` … | Poster still for each clip, shown before the clip loads |
| `clips_new/`, `moments.json.new` | After a re-rank, until you choose: the new clips and their moments (see [Re-rank](#re-rank)) |
| `clips_final/`, `clips_previous/` | Only while chosen clips are kept: the final set being staged, and the old clips while they are swapped file by file |
| `runner.log` | Everything the runner printed; check this first when a job fails |

The stats page has its own file, `data/stats/runs.jsonl`: one line per run, kept when a video's job folder is deleted (see [Stats page](#stats-page)).

On Modal, each job has a folder in the `ai-podcast-clipper-jobs` volume, removed when the run ends:

| Path | Contents |
|---|---|
| `/<job id>/audio.m4a` (or `audio.flac`) | The video's audio, uploaded first: phase 1's input |
| `/<job id>/input.mp4` | The whole video, only on a [fallback](#fallbacks-to-the-whole-video) |
| `/<job id>/transcript.json`, `moments.json` | Phase 1's output (a re-rank uploads `transcript.json`) |
| `/<job id>/footage/clip_N.mp4` | The stream-copied footage of clip N: phase 2's input |
| `/<job id>/clips/clip_N.mp4` | Finished clips, written by each renderer itself |

**Delete a video:** stop `npm run dev`, delete its folder in `data/jobs`, start the app again. Its cost and times stay on the stats page.

## Settings

Backend settings live in `ai-podcast-clipper-backend/.env` (copied from `.env.example`, git-ignored). Values set in the shell override the file. Each row below says when it takes effect:

- **runner**: read by `runner.py` when a run starts, no redeploy.
- **deploy**: read by `modal deploy main.py`, so deploy again after changing it (see [When to redeploy](#when-to-redeploy)).

The app's **Settings page** (gear icon) saves `data/settings.json`: currency, clips per video, Most replayed, audience weight and SponsorBlock. The runner reads it before every run, and its values win over `MOST_REPLAYED`, `SPONSORBLOCK` and `REPLAY_WEIGHT` below (the weight is capped at 0.6 there).

| Setting | Default | When | Meaning |
|---|---|---|---|
| `YOUTUBE_IPV4` | `on` | runner | Download YouTube videos over IPv4, which avoids repeated timeouts to one YouTube server on a broken IPv6 route. `off` lets the system choose |
| `MOST_REPLAYED` | `on` | runner | Use YouTube's Most replayed graph: replay hotspots become clip candidates and each clip gets an audience score. See [Most replayed and SponsorBlock](#most-replayed-and-sponsorblock) |
| `SPONSORBLOCK` | `on` | runner | Keep SponsorBlock-marked sponsor reads, self-promotion, subscribe reminders, intros, outros and previews out of clips |
| `REPLAY_WEIGHT` | `0.4` | runner | Share of the final score from the audience, 0-1. `0.4` = 60% Gemini, 40% viewers; `0` = Gemini only |
| `CLIP_DOWNLOADS_AT_ONCE` | `8` | runner | Clips saved from Modal at the same time (1-16). The old fixed value was 2; on a 50 Mbps line, 8 at once measured 44 Mbps against about 20 |
| `VIDEO_KEEP_DAYS` | `7` | runner | Days a video is kept on the laptop after its last use, for re-ranks. `0` = delete it after every successful run (the old behaviour) |
| `VIDEO_CACHE_MAX_GB` | `10` | runner | Total size of the kept videos; the least recently used go first. `0` = no cap |
| `VIDEO_CACHE_DIR` | empty | runner | Where kept videos live; empty = `%LOCALAPPDATA%\ai-podcast-clipper\videos`. Keep it outside OneDrive |
| `CLIPPER_GPU` | `A10` | deploy | GPU for transcription and for each clip's render container. A list such as `L40S,A10` is a fallback order. Needs an NVENC encoder: L4, A10 or L40S, not A100/H100/H200/B200. See [Choose a GPU](#choose-a-gpu) |
| `RENDER_MAX_CONTAINERS` | `8` | deploy | Most clip render containers running at once (Modal Starter allows 10 GPUs) |
| `RENDER_CPU` | `4` | deploy | CPU cores reserved per render container (decoding and framing run on the CPU) |
| `FLASH_FRAMES_MAX` | `3` | deploy | A run of up to this many frames of another shot between two cuts in the video itself shows the frame before it instead. Remote-podcast edits often flash a frame of the side-by-side layout or a zoomed-out view, which reads as a jump. `0` = off |
| `SPEAKER_MIN_HOLD_SECONDS` | `0.5` | deploy | Shortest time the crop stays on one speaker, or on the whole frame, before it may switch. Removes split-second jumps; cuts in the video itself still switch at once. `0` = off |
| `MIN_CLIPS` | `3` | deploy | A job never ends with fewer clips: missing ones are picked from the transcript and marked "fallback" |
| `CLIP_MIN_SECONDS` / `CLIP_MAX_SECONDS` | `30` / `120` | deploy | Clip length limits. Within them the conversation decides the length; a proposal over the maximum is dropped, never cut short |
| `SCORE_PROFILE` | `value` | deploy | Rubric weights. `value`: payoff 25%, value 25%, standalone 15%, hook 15%, emotion 10%, shareability 10%. `classic`: the earlier hook-first weights (hook 25%, standalone 20%, payoff 20%, emotion 15%, value 10%, shareability 10%) |
| `CLIP_GRACE_SECONDS` | `5` | deploy | Extra seconds a clip may run past the maximum, only to finish its last sentence |
| `GEMINI_MODELS` | `gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite` | deploy | Models tried in order. `GEMINI_MODEL` in the Modal secret, if set, goes first |
| `GEMINI_BUSY_RETRIES` / `GEMINI_BUSY_WAIT_SECONDS` | `1` / `3` | deploy | Quick retries for a busy model (500/502/503) before moving to the next. A stalled request (no reply within the timeout, or 408/504) and a used-up quota (429) go to the next model at once |
| `GEMINI_THINKING_LEVEL` | `low` | deploy | `minimal`, `low`, `medium` or `high`; empty = model default |
| `GEMINI_TEMPERATURE` | empty | deploy | Empty = model default (recommended for Gemini 3) |
| `CLIPPER_APP` / `CLIPPER_SECRET` | `ai-podcast-clipper` / `ai-podcast-clipper-secret` | deploy and runner | Names of the Modal app and secret, for a [test deployment](#test-deployment) |
| `YTDLP_COOKIES_FILE` / `YTDLP_COOKIES_FROM_BROWSER` | none | runner | YouTube login for yt-dlp: see [YouTube cookies](#youtube-cookies) |
| `GEMINI_TIMEOUT_SECONDS` / `RANKING_TIME_LIMIT_SECONDS` | `75` / `240` | deploy | How long a request may go without a reply, and the total ranking budget before the transcript fallback. Answers stream within 38-55 s, so 75 s only cuts stalls and leaves the next model most of the budget |

Frontend settings (`ai-podcast-clipper-frontend/.env`, all optional, read in `src/env.ts`): `DATA_DIR` and `RUNNER_PYTHON`. The cookie settings belong in the backend `.env`; an older frontend `.env` that still sets them keeps working and wins.

To change the Modal secret (a new Gemini key, or `GEMINI_MODEL`), re-create it with `--force`. That replaces the whole secret, so repeat every key it should keep (`HF_TOKEN` too), then run `modal deploy main.py` so new containers read it. `Read-Host` keeps the values out of PowerShell's history:

```powershell
$gemini = Read-Host "Gemini API key"; $hf = Read-Host "HF token"
modal secret create ai-podcast-clipper-secret "GEMINI_API_KEY=$gemini" "HF_TOKEN=$hf" --force
(Get-Content .env) -replace '^GEMINI_API_KEY=.*', "GEMINI_API_KEY=$gemini" | Set-Content .env   # the copy rank_moments.py reads
Remove-Variable gemini, hf
python rank_moments.py --list-models   # "Current chain" should show OK for every model
modal deploy main.py
```

Use a key from a project with billing on (AI Studio's paid tier). The free tier allows only a few requests a day for the Flash models, and a used-up quota (429) sends every call down the chain to the transcript fallback. The Gemini cost estimates assume paid-tier prices.

## When to redeploy

`modal deploy main.py` bakes the code into the Modal image and reads the deploy-time settings. Run it again after changing:

- `main.py`, `requirements.txt` or `download_parakeet.py`;
- any module `main.py` bundles (its `add_local_python_source` list): `moments.py`, `replay.py`, `settings.py`, `audio_utils.py`, `parakeet_transcriber.py`, `render.py`, `costs.py`, `clipper_names.py`, `segments.py`;
- the folders `asd/` and `prompts/` (editing a ranking prompt included);
- a **deploy** setting in `.env` (see [Settings](#settings)).

No redeploy is needed for the laptop-side modules (`runner.py`, `pipeline.py`, `phases.py`, `cloud.py`, `cutter.py`, `transfer.py`, `youtube.py`, `downloader.py`, `jobstore.py`, `media.py`, `billing.py`, `maintenance.py`, `run_history.py`, `fx.py`, `gemini_costs.py`, `videostore.py`, `clipsets.py`), for the frontend, or for the **runner** settings. `modal deploy` and `modal run` stop with a message when the S3FD weights are missing (`python download_weights.py` fetches them); `CLIPPER_SKIP_WEIGHT_CHECK=1` skips that check.

## Command line

Jobs started here show up in the web app too.

```powershell
python runner.py --video "C:\path\to\podcast.mp4"
python runner.py --youtube "https://www.youtube.com/watch?v=..." --max-clips 3
python runner.py --job-dir "..\data\jobs\<job id>" --rerank
python runner.py --job-dir "..\data\jobs\<job id>" --keep-clips '[{"set":"new","clip":"clip_0.mp4"}]'   # what the comparison's Keep button runs
python runner.py --thumbnails          # covers and clip posters for videos made before they existed
python runner.py --backfill-replay     # Most replayed graph + SponsorBlock for older YouTube videos (once; --force re-reads)
python runner.py --modal-bill          # read this month's Modal bill into data/stats (the stats page also does this)
python runner.py --check-rates         # Modal's live prices as the cost estimate reads them ("source": "modal" or "built-in")
python runner.py --prune-videos --dry-run   # list kept videos and those older versions left in finished job folders
python runner.py --prune-videos        # delete them (failed jobs keep theirs for Try again)
```

Add `--keep-video` to a run to keep `input.mp4` in the job folder afterwards instead of the kept videos, for example to benchmark it.

## Where the video goes

- **YouTube link:** the runner first downloads only the audio, then the video with yt-dlp (H.264 up to 1080p, preferring at most 30 fps at the same resolution, since the render resamples to 25 fps) into `%TEMP%\ai-podcast-clipper\<job id>`, outside OneDrive so sync can't lock the file. A dropped connection is retried with a growing pause and resumes the partial file; if the run still fails, the partial or finished download stays, so **Try again** neither downloads nor transcribes again. When the run succeeds the video moves to the kept videos and the temp folder is deleted (`--keep-video` moves the video into the job folder instead); temp folders left for over two days are cleared at the next run.
- **Kept videos** (`videostore.py`): `%LOCALAPPDATA%\ai-podcast-clipper\videos\<job id>\input.mp4`, outside OneDrive, for both YouTube videos and uploads. A re-rank or **Try again** uses it (`runner.log`: "Using the kept video"), so a YouTube video is not downloaded again and an upload is not asked for again. At the start of every run, videos unused for `VIDEO_KEEP_DAYS` (7), videos over `VIDEO_CACHE_MAX_GB` (10, least recently used first) and videos whose job folder was deleted are removed; a running job's video never is. The Settings page shows the space used and has **Remove kept videos**; `data/stats/video-store.json` tells the app where the folder is. Nothing is kept on Modal.
- **MP4 upload:** the browser sends it to the job folder; the runner copies its audio out and later cuts the footage from it.
- **Uploads to Modal** go in 8 MiB pieces, 4 at a time (Modal's default of 128 at once makes every piece crawl on a home connection). A piece that takes over 10 minutes, or fails, is retried, and a retry only sends the pieces Modal doesn't have yet. The progress strip shows the percentage and the time left, and "no data moving" after two minutes without progress; `runner.log` gets a line every 10% with the average speed and the total time.
- **Waiting for the GPU:** if the connection to Modal drops while a function runs there, the runner keeps asking for the result for up to 15 minutes (`runner.log`: "Lost the connection to Modal"), instead of failing the run.
- **After a successful run** the video, audio and footage are deleted locally and the job's volume folder is removed. After a failed run the volume copy is removed too (except rendered clips, see below), and a video already in the job folder stays for **Try again**.
- **Clips that rendered but could not be saved** stay on Modal: `job.json` `pendingSave` records them, and **Try again** (or Re-rank) downloads them instead of rendering again. The next run's sweep removes such folders after a day.
- **Cleanup of killed runs:** at the start of every run the runner sweeps the volume and removes folders of jobs that are finished, failed or gone (cancelling any Modal call still running for them, including the clip renders `render_clips` started, which do not stop with it); folders of active jobs are never touched. When a run fails or is interrupted, its running Modal calls and clip renders are cancelled. A clip render still waiting for a GPU when it is cancelled runs anyway once one is free (Modal does not drop it), but its footage is gone by then, so it fails within a second (checked 2026-11-22).

## Most replayed and SponsorBlock

Before every run of a YouTube video (first run and re-rank), the runner spends a few seconds on the laptop:

- **Most replayed graph:** a metadata-only yt-dlp call (no download, with your YouTube cookies if set) returns YouTube's graph as 100 equal segments with a replay intensity from 0 to 1. Only videos with enough views have one, usually tens of thousands. It is read on the laptop because YouTube often strips it from pages it sends to cloud IPs. If YouTube refuses that call ("confirm you're not a bot") but the download goes through, the graph is taken from the download instead (`runner.log`: "read from the download").
- **SponsorBlock:** the hashed API (only the first 4 characters of the video id's SHA-256 leave the laptop) returns the parts viewers marked: sponsor reads, self-promotion, subscribe reminders, intros, outros, previews and a highlight point.

Both go into `job.json` (`replay`). Hotspots (`replay.find_peaks`) are the graph's peaks after three corrections: the first 45 s and last 30 s never count (everyone starts at the beginning), a bump only in the segment where a chapter starts is navigation rather than interest, and sponsor reads are flattened. A peak must stand at least 0.12 above the video's median, and peaks closer than 90 s merge.

Gemini then gets the hotspots (without their intensities) and returns one cleanly cut clip around each, scored on the transcript alone. Hotspots it skips are cut in code (about 30 s of set-up before the peak) and scored in the re-scoring call. Each clip's audience score is the best segment it covers well, on a scale where 50 is the video's median and 100 its most replayed part (parts viewers skip score below 50). Final score = (1 - `REPLAY_WEIGHT`) x Gemini + `REPLAY_WEIGHT` x audience. On a 60-minute video each segment is about 36 s, so the graph says where to clip and the transcript still decides the exact cut.

If the graph or SponsorBlock cannot be read, the run goes on without it (the reason is in `runner.log` and the job's Details). Uploaded MP4s have neither.

SponsorBlock data is licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) (non-commercial, attribution); if your channel becomes a commercial use, ask its author ([sponsor.ajay.app](https://sponsor.ajay.app)) for permission or set `SPONSORBLOCK=off`.

## Re-rank

**Re-rank** (in the app, or `--rerank` above) reuses the saved transcript: no transcription, only Gemini and rendering run again. The kept video is reused; only when it is gone (after 7 days, the size cap or **Remove kept videos**) is a YouTube video downloaded again (while Gemini ranks), and an uploaded MP4 asked for again (or drop it on the home page and choose Re-rank; it must match the original's fingerprint). The new clips appear as they are saved, into `clips_new/`, and the current clips stay live.

When the re-rank is done, the clips page shows **Choose the clips to keep**: the new clips above the previous ones, each with a Keep checkbox. All new clips start ticked and no previous one; a badge marks the same moment in both sets (time ranges overlapping by at least half of the shorter clip). **Keep the new set** and **Keep the previous set** set the ticks; **Keep N clips** keeps the ticked clips, ranked together by score, and deletes the rest (`clipsets.apply_comparison`, run as `runner.py --keep-clips`). If that fails the previous clips and the choice stay as they were. Starting another re-rank or run discards a choice you never made. A video with no previous clips takes the new ones directly. Use it after a Gemini outage left fallback clips, or after editing `prompts/viral_moments.md` (deploy first: the prompts are part of the image).

## Preview ranking locally

Runs only the Gemini step on a finished job, with no GPU and no rendering:

```powershell
$env:GEMINI_API_KEY = "<your key>"
python rank_moments.py --list-models                           # model IDs your key can use
python rank_moments.py "..\data\jobs\<job id>" --max-clips 5   # writes moments.preview.json
```

To compare the saved picks of finished videos with what the current rules would pick, before deploying a prompt change:

```powershell
python rank_moments.py --compare "..\data\jobs\<id 1>" "..\data\jobs\<id 2>" --dry-run   # Gemini calls it would make; makes none
python rank_moments.py --compare "..\data\jobs\<id 1>" "..\data\jobs\<id 2>"             # writes data\eval\clip-comparison.html
python rank_moments.py --compare "..\data\jobs\<id 1>" --reuse                         # rebuild the report from <job>\eval\ with no calls
```

The report shows old and new picks side by side with scores under both profiles, the first and payoff sentences marked, and the lines after each cut, so a clip that stops before its answer is easy to see. Each ranking also prints, and the report shows, its Gemini tokens and estimated cost (rupees at the rate saved in `data/stats/fx.json`), so `--model` runs can be compared on cost as well.

## How a job runs

Two Modal functions (CPU) do the work, called by `runner.py` (`pipeline.py` and `phases.py`):

**Phase 1, `transcribe_and_rank`**, as soon as the audio is on the volume (YouTube: the audio stream alone; an upload: copied out of the MP4 with FFmpeg, stream copy):
1. **Transcribe:** one GPU container (`Transcriber`) runs Parakeet on the audio.
2. **Rank:** Gemini picks the moments from the CPU function, so no GPU is billed while it ranks: one proposal call per 30-minute window (one window up to 45 minutes), then one judge call that re-reads every shortlisted clip with the lines around it, marks it complete or names the issue, may correct its start or end (checked in code) and re-scores it. A 31-minute video makes 2 calls, a 103-minute one 5; `ranking.calls` records the real number. The transcript is saved to the job folder as soon as this phase has it.

The YouTube video starts downloading as soon as the audio stream is on the laptop, so it runs during the audio upload and phase 1 (the audio's progress shows "downloading the video meanwhile"). The progress strip shows Send audio, Transcribe, Find moments, Download (the percentage appears next to the cloud stage while both run), Send clips, Render and Save.

**Phase 2, `render_clips`**, once the laptop has cut and uploaded each chosen clip's footage (`cutter.py`, `segments.py`): one stream-copied piece per clip, checked packet by packet, uploaded as `footage/clip_N.mp4`. Then one GPU container (`Renderer`) per clip, all at the same time. Each one seeks into its footage (the arithmetic in `segments.py` makes the result identical to rendering from the whole video), finds the speaker with LR-ASD, and encodes the clip once on the GPU's NVENC encoder, with subtitles and the audio fade, then writes it straight to the volume. The runner downloads each clip as it appears in the progress `ready` list, up to `CLIP_DOWNLOADS_AT_ONCE` at a time, and publishes it to `job.json` `liveClips` so the app shows it at once. A container that starts without a working GPU or NVENC (on a GPU type that has it) fails at start-up instead of rendering slowly on the CPU.

Flash frames are removed first (`render.flash_frames`): 1-3 frames of another shot between two hard cuts, found on tiny thumbnails of every frame. Every pass (face detection, speaker scoring, encoding) sees the frame before the flash instead, so face tracks run straight through it and the clip shows a 40-120 ms freeze instead of a jump. A camera change right after a flash stays a hard cut.

The crop is then steadied (`render.steady_faces`). Inside each shot of the source video, a stretch shorter than `SPEAKER_MIN_HOLD_SECONDS` goes to the stretch next to it:
- A neighbouring speaker who is still on screen keeps being followed; if both neighbours qualify, the one with the higher speaking score wins.
- Otherwise the nearest crop is held still, for example over a frame where the face was briefly lost.

Cuts in the source are never crossed, so a cut to another camera still switches immediately.

A job's details in the app show its processing time and estimated cost: Modal (`costs.py`: GPU, CPU and memory per container, using Modal's November 2026 prices when Modal's live rates can't be read) plus Gemini (`gemini_costs.py`, see [Stats page](#stats-page)). The Modal dashboard and AI Studio's Billing page have the real amounts.

### Fallbacks to the whole video

The whole video is uploaded first (as before the two phases) and both functions read `input.mp4` when:
- the audio can't be had on its own (the audio download or the FFmpeg extraction fails; the reason is in `runner.log`), or
- the job is a legacy re-rank of a run from the removed Whistle engine.

Only phase 2 falls back (the whole video is uploaded after ranking, and clips render from it) when the video can't be cut into footage: `cutter.py` handles only MP4/MOV files with H.264 video and one AAC audio stream, and the cut is verified before upload; an FFmpeg error or a failed check also falls back. YouTube downloads are normally H.264 + AAC MP4, so uploads in other formats are the usual case.

### job.json fields

Besides status, progress and clip details (each with `complete` and `issue` from the judge, and `from` after a comparison): `videoTransfer` (`audio-first`, `laptop-download` or `upload`), `engine` (Parakeet's audio length and speed, GPU), `timings`, `cost`, `runs`, `reranks`, `failedStage`, and run state: `modalCallId` (the running Modal call, so a killed runner's call can be cancelled), `modalChildCallIds` (the clip renders of that call, cancelled with it), `pendingSave` (rendered clips still on Modal), `keepRemote`. A run adds optional `clipTimings` (per clip: seconds, container start-up, render steps, encoder), `coldStarts` (per container that started: stage, start-up seconds, GPU) and `resumedFrom` (clips of an earlier attempt were downloaded instead of rendered). New `timings` keys of a two-phase run: `audio` (getting and uploading the audio), `download` (the video, in parallel with phase 1), `downloadWait` (how long the run then waited for it), `cut`, `clipUpload`; only `downloadWait` of the download adds to the total. `clipSave` (megabytes, seconds, Mbps and clips at once of the clip download), `keptVideo`, `liveClips` / `liveClipsSet` (clips saved so far, during a run), `plannedClips` (every clip the run will make, published as soon as the ranking is known, so the clips page shows a titled place per clip; cleared when the run ends) and `comparison` (a re-rank's new set waiting for a choice) are also stored. The ledger line of a run carries the same run fields.

## Stats page

The chart icon in the header opens `/stats`, for this month or all time: Modal's bill for the month against the Starter plan's $30 credit, the average new video and re-rank (cost with Gemini, time, cost per podcast hour), the estimated Gemini spend with its calls and tokens, the average run by stage (time and cost), every run with its result and stage tooltip, and the Most replayed / SponsorBlock sentences. A time-per-video chart appears after 10 new runs. It refreshes every few seconds while a video is processing.

- **Modal's bill** comes from `modal billing summary` (works on the Starter plan). The runner saves it in `data/stats/modal-bill.json` after every run, and the page asks for a fresh one when it is older than 15 minutes. Modal's billing months are UTC calendar months.
- **Run costs** are estimates: each container's running time (plus start-up and the idle time before Modal stops it) at Modal's live rates from `modal billing rates`, saved once a day in `data/stats/rates.json`. If Modal's rate names change and can't be read, the built-in prices in `costs.py` are used (`--check-rates` shows which). The Runs card shows how the Modal part of this month's runs compares with what Modal charged for the app.
- **Gemini costs** are estimates too: every Gemini answer reports its tokens (input, cached input, output and thinking), `moments.py` keeps them per call in the ranking report (`ranking.usage` in `job.json`), and the runner prices them with the table in `gemini_costs.py` (Google's Standard paid-tier prices, with their dated changes such as 3.8 and 3.6 Flash doubling on 2027-01-01; thinking is billed as output, cached input at the caching price). At `GEMINI_THINKING_LEVEL=low`, Gemini 3.8 Flash usually reports no thinking tokens at all, so "0 thinking" is normal: its total then equals input plus output (checked 2026-11-22); at `high` the thinking is reported and priced. A run stores the result as `cost.gemini` (`usd`, `calls`, `tokens`, per-model totals, the date the price table was checked); `cost.usd` stays Modal's alone. Runs before 2026-11-22 have no token counts and count as $0 Gemini (they ran on the free tier). Calls that failed before answering are not counted. When Google changes a price, edit `PRICES` and `PRICES_CHECKED` in `gemini_costs.py` (no redeploy); a model missing from the table is listed as "No price for …" on the Gemini tile and counts as $0.
- **Stages:** Download (on the laptop: audio, the video download beyond what phase 1 hid, cutting, uploads and the Most replayed lookup; no Modal cost), Transcribe, Rank (Gemini; its cost is the Gemini API plus the job's own CPU container, split in the bar's popover), Render (time = the slowest clip, cost = all clip GPUs), Save (laptop, no cost) and Overhead (total minus the stages, mostly containers starting).
- **Rupees:** with Settings > Currency on rupees, each run is converted at the USD to INR rate saved when it finished (`fx.py`, `data/stats/fx.json`: Frankfurter, then ExchangeRate-API's open endpoint; no API key). The month's bill uses today's rate.

- **Run ledger.** When a run ends (first run, re-rank or failure), the runner appends one line to `data/stats/runs.jsonl` (`run_history.py`): the job's id, title and source, the mode, result, start and end time, step timings, cost and Parakeet's speed. The stats page reads only this file, so **deleting a video keeps its runs in the stats**; such videos are listed as "Deleted video", without a link. The same run also goes into the job's own `job.json` `runs`.
- **Starting the stats over:** delete `data/stats/runs.jsonl` (or the whole `data/stats` folder). Deleting job folders alone does not reset the stats.
- **Failed runs** show where they stopped. One that ended before reporting a cost shows "—" and counts as $0. Averages count only runs that made clips, and say how many runs they are based on ("early estimate" below 3).

## Interface: motion and touch

- **Motion tokens** live in `src/styles/globals.css` in three layers: primitives (`--duration-100/130/200/320`, `--curve-*`), what they are for (`--motion-press`, `--motion-enter`, `--motion-exit` at about 65% of an enter, `--motion-emphasis`, `--motion-ease-*`) and one component value (`--tooltip-delay`, 400 ms). Tailwind classes: `press` (colour ease plus a 3% shrink while pressed), `ease-enter` / `ease-exit` / `ease-move`, `duration-(--motion-enter)`, and `pop-in`, `icon-swap`, `shimmer` for small one-off animations.
- **The motion library** (`motion`, pinned) is loaded once by `components/motion-provider.tsx` (`LazyMotion`; all features, layout animations included, load in their own chunk after the page; components use `m.*`). `src/lib/motion.ts` mirrors the tokens in seconds; change both together. It is used where CSS can't do the job: popovers fade out after they close, clip places keep their position while clips arrive, and the progress strip fades into the "clips ready" line.
- **Reduced motion:** the CSS animations stop, transitions become instant, and the motion library drops movement but keeps fades (`reducedMotion="user"`).
- **Touch:** with a touch screen (`pointer: coarse`) icon buttons, header links, clip actions, the settings stepper and text buttons grow to 44 px and switches get a 44 px hit area; with a mouse the layout stays as compact as before. The clip score badge grows to 34 px (it sits on the video). Below 360 px wide the header shows the logo without the wordmark.
- **Tooltips** (`components/ui/tooltip.tsx`) name icon-only controls: after 400 ms under a mouse, at once on keyboard focus, and on a 0.5 s long press on a touch screen (that press doesn't click). `components/ui/icon-button.tsx` builds an icon button or link with a required label and its tooltip.
- **Stats bars** name their stage inside a segment when it is wide enough, show only the value when narrower, and nothing when too small (size container queries, no guessing by share).
- **A running video** (`components/video/progress-strip.tsx`, `live-clips.tsx`, `pending-clip.tsx`, `run-finished.tsx`): a rail of the run's steps (every name visible on a phone, done steps checked), the video download as its own row while the cloud transcribes and ranks, and one place per clip with its rank, score and title (`plannedClips`) that turns into the playable clip when it is saved (its frame lights up once; screen readers hear "Clip 2 ready: <title>"). A first run that ends while the page is open turns the strip into "5 clips ready in 6 min 12 s" with a toast in the same words. A failed run shows the rail with the step it stopped at.
- **While a video runs** (`lib/run-signal.ts`, `components/run-signal-effects.tsx`, `header-logo.tsx`): the header logo's bars move (the only looping animation; it stays still under reduced motion), and the tab title shows the step ("Rendering, 2 of 5 ready, Podcast Clipper"), then "Clips ready" (or "No clips" or "Failed") for 30 s. Every page knows (the stats and settings pages too), from the shared job list below; a background tab keeps reading it every 15 s while a job runs (browsers may stretch that to a minute).
- **Adding a video** (`components/new-job/`): a YouTube link is checked as it is typed or pasted (the link icon turns into the YouTube one, and a video added before shows its notice before "Make clips" is pressed); "Make clips" is soft amber until there is a link to add, then solid; a link that isn't YouTube is flagged on paste, on leaving the box or on submit. Ctrl+V anywhere on the home page (outside another field) fills the box with a YouTube link, or starts an MP4 copied in the file explorer. Dragging a file over the page shows a full-page drop target (the logo's dashed 9:16 frame; red for a file that isn't an MP4); the Upload MP4 button stays for anyone who can't drag. While an MP4 uploads, the bar shows the megabytes sent and the time left.
- **Your videos** (`components/video-list.tsx`): videos being processed come first with a compact step rail; a done row shows its clip count and the best clip's score (amber when Gemini scored it, grey for a pick made without Gemini); a failed row says where it stopped ("Failed at Send clips") and why; "Choose clips to keep" marks a re-rank waiting for a choice. Rows that appear while the page is open fade in, and rows that move glide to their place. With more than 10 videos a search box filters by title.
- **Stats page:** the stage bars and each run's time bar open a small table of their parts on hover, keyboard focus or a tap (one at a time; Escape or a press outside closes it; `components/stats/bar-popover.tsx`), and each bar's button reads the whole breakdown to screen readers. Switching This month / All time makes the average tiles glide to their new values (`lib/use-tween.ts`) and the bars glide to their new lengths; a background refresh changes them silently, and the first load shows the real numbers at once (they are in the page's HTML). Failed runs say where they stopped with the step names used everywhere ("Failed at Send clips", `STEP_LABELS` in `lib/format.ts`). Stage colours, checked with the data-viz validator: dark passes every check; light passes but Transcribe and Render are under 3:1 against a white card, which the labels inside the bars and the per-run tables cover.
- **Settings page:** each change is saved on its own and a "Saved" check appears beside that control for a moment; a failed save puts the control back to what is really saved and shows "Couldn't save. Retry" beside it (one live region tells screen readers both). Clips per video is a spinbutton (arrow keys, Home, End) whose number rolls the way it moved; the currency pill slides to the chosen option; Audience weight's slider is read with its description (why it is off). "Remove kept videos" now asks first ("Remove 2 videos, 571 MB?", Remove or Keep them, Escape cancels).
- **Watching clips** (`components/video/clip-viewer.tsx`): a clip card is a poster; a click (or Enter) opens the full-screen viewer, which plays with sound and moves through the clip's set with Up and Down, J and K, the on-screen buttons or a vertical swipe on a phone; Space plays and pauses, M mutes, Escape or Back closes. The address carries `?clip=clip_2` (and `&set=new` for a re-rank's new set), so a clip can be linked to; a linked clip starts muted, as browsers require. The details (score breakdown, why, Copy title, Download) sit beside the clip, or in a Details sheet on a phone. The viewer is always dark. A mouse resting on a card for 400 ms plays a muted preview there (not on touch screens or with reduced motion). Clips picked without Gemini get a neutral title from their place in the video ("Clip 3, 19:18 to 20:11") and a "Picked without Gemini" tag, never a transcript sentence (`components/clip-details.tsx`).
- **Pages in sync** (`lib/job-sync.ts`, `components/jobs-sync.tsx`): the tab keeps one live copy of the jobs that every page reads, so the home list and a video's page always show the same step. It is read again whenever a page opens (Back and Forward too), the tab is shown or focused, or a page comes back from the browser's back-forward cache; and polled every 3 s while a video runs, every 10 s otherwise (15 s in a background tab while one runs; an unchanged answer is an empty 304). Every answer carries the server time it was read at (`X-Served-At`, and `servedAt` in the page's HTML), and the newer copy of each job always wins, so a page Next.js shows again from its cache never puts an old list back. What a video's page reads also updates its row, and a row that changes makes an open video page read again at once. Adding a video, Try again, Re-rank and keeping clips tell the app's other tabs, which read again at once. A timer tick is skipped while the last request is still out, so a slow server never builds a queue.
- **Busy job files:** on Windows another program (runner.py, or OneDrive syncing the data folder) can hold `job.json` open for a moment. The app then tries a write again for up to about 4 s (as `runner.py` does) instead of failing the request, and a read that fails answers the last good copy, so a video never drops out of the list for one poll (`lib/job-store.ts`).
- **Notifications:** Settings > Notifications > "Notify me when a video is done" (off by default; the browser asks for permission when you turn it on). A run that ends while any page of the app is open in a background tab shows a notification; clicking it opens the clips.

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

## Cancel or delete a video

**Cancel** (on a running video's page, beside the timer) runs `runner.py --job-dir <job> --cancel`: it stops the runner and everything it started on this laptop, cancels the job's Modal call and the clip renders it spawned, removes the job's folder from the volume and marks the video "Cancelled at <step>". A re-rank that is cancelled keeps the previous clips. **Try again** then resumes like after any stopped run: a partial download, the saved transcript and clips kept on Modal are reused. Cancel is not offered while the clips are being saved (the run is all but done, and stopping a re-rank's file swap halfway would leave its clip folders mixed). A Modal call that could not be cancelled (offline) stays in `job.json`, and the next run's sweep cancels it.

**Delete** (the bin icon on a finished or failed video's page) removes its job folder, the copy kept for re-ranks and any download left in the temp folder. Its runs stay on the stats page. A running video must be cancelled first. If a clip is open in a player, Windows refuses and nothing is deleted.

From PowerShell, without the app: `python runner.py --job-dir "<data folder>\jobs\<job id>" --cancel`. Never run `modal app stop`: that undeploys the app.

## Test deployment

To try changes without touching the real app, set `CLIPPER_APP=ai-podcast-clipper-test` (and optionally `CLIPPER_SECRET`, which otherwise stays the real secret so the key is shared) in `.env` or the shell, then `modal deploy main.py` and run the runner as usual. The test app gets its own volume (`<app>-jobs`) and progress Dict (`<app>-progress`), and the names reach the containers through the settings secret. Remove the line and the runner talks to the real app again; stop the test app from the Modal dashboard (never `modal app stop` on the real one).

## Tests and CI

```powershell
pip install -r requirements-dev.txt
python -m pytest tests -q
ruff check .
```

`requirements-dev.txt` has everything the offline suite needs, including `imageio-ffmpeg` (through `requirements-local.txt`); the footage tests also need `ffprobe` on the PATH and skip without them. Their exact-match checks run only when the `ffmpeg` on the PATH (or `FFMPEG_BIN`) is 4.4-6.x like Modal's, because it plays the render container; with a newer one they skip. The torch tests (batched S3FD and LR-ASD) skip without torch (CPU wheels: see the file). Settings for both tools are in `pyproject.toml`. [CI](../.github/workflows/ci.yml) runs the backend (ruff, pytest) and the frontend (`tsc`, `npm run lint`, `npm run format:check`, `npm run build`) on every push.

## YouTube cookies

When YouTube asks to "confirm you're not a bot", yt-dlp can use your YouTube login. Set one of these in the **backend** `.env` (they are read by the runner, which checks them once per run and carries on without cookies if they can't be read, with the reason in `runner.log`; a variable already set in the frontend `.env` or the shell wins):

- **`YTDLP_COOKIES_FILE`**: a `cookies.txt` file. This is the way to use **Chrome**: Chrome (and Edge) on Windows encrypt their cookies so that other programs can't read them, so `YTDLP_COOKIES_FROM_BROWSER=chrome` fails ("Failed to decrypt with DPAPI"). Install the "Get cookies.txt LOCALLY" extension, open youtube.com while signed in, export the cookies for youtube.com, and save the file outside OneDrive and the repo, e.g. `C:\Users\<you>\podcast-clipper-cookies\youtube.txt`. The file signs in as you: keep it private. YouTube rotates cookies, so export again if the bot check comes back.
- **`YTDLP_COOKIES_FROM_BROWSER=firefox`**: Firefox's cookies can be read directly, while you are signed in to YouTube in Firefox.

The file wins when both are set.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "YouTube asked to confirm you're not a bot" | Set up [YouTube cookies](#youtube-cookies), then **Try again** |
| A YouTube download keeps timing out ("Read timed out", "connect timeout") | Your connection to YouTube's video servers is dropping. **Try again** resumes the download where it stopped. Check that `YOUTUBE_IPV4` is `on`, and update yt-dlp with `pip install -U "yt-dlp[default]"` |
| "The backend Python environment wasn't found" | Create the backend `.venv` (see the README's setup) or set `RUNNER_PYTHON` in the frontend `.env` |
| App or secret `ai-podcast-clipper` not found | Run `modal deploy main.py`, or create the secret (see the README's setup) |
| No Most replayed graph on a popular video | `runner.log` has the reason. "Sign in to confirm you're not a bot": set up [YouTube cookies](#youtube-cookies), then **Re-rank**. Otherwise YouTube shows no graph for that video yet |
| "Gemini was busy, so these clips were picked without it" | They came from the transcript or were cut around a replay peak. Click **Re-rank** later, or check the model IDs with `rank_moments.py --list-models` |
| HTTP 429 from Hugging Face during the first deploy | Add `HF_TOKEN` to the Modal secret and deploy again |
| Modal auth or token errors | Run `modal setup` again in the backend venv |
| "Model weights missing or damaged" when deploying | Run `pip install gdown; python download_weights.py` |
| Clips rendered but saving failed | Click **Try again**: the clips are still on Modal and are downloaded without rendering again |
