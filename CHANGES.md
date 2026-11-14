# Changes vs. upstream

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
- `torch` and `torchvision` pinned to a set that resolves together (WhisperX, which needed the original pins, has since been replaced by Parakeet).
- LR-ASD vendored into `asd/` with a NumPy 2 fix.

**Parakeet transcription and best-5 ranking**
- WhisperX replaced by [NVIDIA Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) (ONNX Runtime on CUDA, `parakeet_transcriber.py`), chosen over v3 and [Whistle](https://huggingface.co/Cactus-Compute/whistle) (tried, then removed) on this project's own podcasts. The app is English-only for now.
- An A10 (instead of an L40S) by default; `CLIPPER_GPU` in `.env` picks the GPU.
- Pause-aligned ≤28 s chunking (`audio_utils.py`), Parakeet runs chunks unbatched because padded batches changed ~4% of words.
- Moment selection rewritten (`moments.py`, `prompts/viral_moments*.md`): sentence-line transcript (8× smaller prompt), JSON response schema, six-part virality rubric with a code-computed weighted score, snapping to sentence boundaries and the 30-60 s limit, overlap removal, windowed candidates + cross-window re-ranking for long videos. Upstream took Gemini's first five moments in chronological order; clips are now the top five by score (`clip_0` = best).
- No more empty jobs: the prompt requires exactly N ranked candidates (weak ones get low scores instead of being dropped), Gemini calls retry, fall back to plain JSON and a second model, use the model's default temperature, and any shortfall below `MIN_CLIPS` is filled from the transcript (labelled "fallback"); errors are surfaced in the job instead of being swallowed. `rank_moments.py` re-runs ranking locally.
- Gemini resilience: an ordered `GEMINI_MODELS` chain (one quick retry on a busy model, then the next), plain-JSON retry only for format problems, streamed responses with a low thinking level, per-request timeouts and a total ranking budget before the transcript fallback; `rank_moments.py --list-models` shows which model IDs a key can use.
- "Re-rank & re-render" for finished jobs (UI button, `runner.py --rerank`): reuses the saved video and transcript and keeps the old clips until the new ones are downloaded.
- `.env` settings (`settings.py`) and offline tests in `tests/`.

**Redesigned UI**
- Two pages: home (input bar with link, upload button and drag and drop, then a compact list of videos with live status) and a clips page per video (`/videos/<job id>`). No file paths or transcript snippets.
- Live progress from the GPU (`modal.Dict` `ai-podcast-clipper-progress`, polled by `runner.py`): step strip, "Rendering clip 3 of 5" and time left.
- Clip cards: rank and score badge with the score breakdown and Gemini's reason on hover or focus, copy title, download; "Download all" as one ZIP (`/api/media/<job>/all`).
- Failed jobs explain what went wrong in plain language and offer **Try again** (`/api/jobs/<job>/retry`, reuses a downloaded video); a per-video Details popover holds the technical facts.
- Light / Dark / System theme (next-themes), Geist and Bricolage Grotesque type, amber accent, visible keyboard focus and reduced-motion support.
- Video list with covers (YouTube thumbnail, or a frame from an uploaded video, saved in the job folder), video length and titles on up to two lines; logo (a sound wave with a vertical clip frame) in the header and as the browser-tab icon.
- On Windows the runner starts with `pythonw.exe` and hides its child processes' windows, so no console window pops up over the app.
- Clips show instantly: each clip gets a poster still (`clip_N.jpg`, made by the runner), and clip URLs carry a version so the browser caches them (ETag and immutable caching in the media route).
- Fullscreen keeps the whole vertical clip (`object-contain`; `object-cover` cropped it to the screen width). Re-rank uses a "list, redone" icon.
- Clean cuts: transcript lines record where sentences start and end; clips always begin on a sentence start and end on a sentence end (up to `CLIP_GRACE_SECONDS` past the maximum to finish the last sentence, never mid-word), with a short lead-in, tail and audio fade. The prompts ask Gemini to open on a fresh thought and end where it lands.
- Duplicate check: adding a YouTube video (any link form) or an MP4 (content fingerprint, so renamed copies match) that was already processed shows a notice with Open clips, Re-rank and Make clips again (`/api/jobs/duplicate`).

**Faster, parallel rendering**

Rendering took 12-15 minutes of a 20-minute job, five clips one after another (`job.json` timings on this project's videos). Most of that time was spent re-encoding video and moving frames on and off disk, not running models.

- Pipeline split into three steps (`main.py`):
  - `Transcriber`: one GPU container runs Parakeet.
  - Gemini ranks from a CPU function (`process_job`), so no GPU waits on it.
  - `Renderer`: one GPU container per clip, all at the same time.
- Clip cut seeks before decoding: the old cut decoded the whole video up to the clip, about 80 s for a clip 18 minutes in, measured on 2 CPU cores.
- LR-ASD runs in-process (`asd/fast_asd.py`, `render.py`). `Columbia_test.py` is kept unchanged as the reference.
  - Models load once per container.
  - Frames stay in memory: no JPG dump, no XVID face videos, no unused debug video.
  - S3FD and LR-ASD run on batches.
  - Same algorithm and parameters. Batched inference and frames that skip the old JPG round trip can still shift a borderline face detection or round a speaker score by 0.1, so `benchmark_gpu.py --parity` compares the old and new crop decision for every frame of one of your clips.
- One video encode per clip on the GPU's NVENC encoder (libx264 fallback), with subtitles and the audio fade, instead of three lossy encodes. Clips keep the original audio; it used to be 16 kHz mono.
- Flash frames removed (`render.flash_frames`, `FLASH_FRAMES_MAX=3`).
  - 1-3 frames of another shot between two cuts in the source show the frame before them instead, in every pass, so face tracks continue through them.
  - Remote-podcast edits flash single frames of the side-by-side layout or a zoomed-out view; in a vertical clip they read as jumps.
- Steady crop: no speaker switch shorter than `SPEAKER_MIN_HOLD_SECONDS` (0.5 s) inside a shot of the source video (`render.steady_faces`).
  - It removes the split-second flips between two people who score about the same, and the one-frame flashes of the whole frame where a face was briefly untracked.
  - Cuts in the video itself still switch at once.
  - The parity check compares the decisions before this step, so it keeps measuring the speaker-detection port.
- The no-face background blur is computed at quarter size: it looks the same and costs a fraction of the time.
- `CLIPPER_GPU` accepts a fallback list. `RENDER_MAX_CONTAINERS` and `RENDER_CPU` are new settings.
- Estimated Modal cost per job (`costs.py`), shown with the processing time in the job's details. Progress reads "Rendering 5 clips", then "2 of 5 ready".
- `benchmark_gpu.py` compares GPUs on one of your videos, checks old vs new crop decisions (`--parity`) and re-renders chosen clips of a job for a before/after look (`--preview`).

**Stats page**
- `/stats` (chart icon in the header), in the app's own style:
  - Modal spend in total and this month, against the Starter plan's $30 credit;
  - cost per video (first run plus re-ranks) and per clip;
  - end-to-end time, and where a run's time goes (laptop download and upload, cloud work, saving clips);
  - average end-to-end, transcribe, find-moments and render times by podcast length, for the current pipeline or all runs;
  - a few computed insights, and every run of a video step by step.
- Run history: `runner.py` appends each run (first run, re-rank, failure) to `job.json` `runs` (`run_history.py`). `--backfill-stats` rebuilds it from `runner.log` for older videos; their costs are estimated from the logged GPU time and marked "est.".
- The page refreshes every few seconds while a video is processing.

**Videos go straight to Modal, and nothing big is kept**

Downloading a video on the laptop and uploading it to Modal took about 4 minutes of a 6-7 minute job (100-130 s each way on this project's videos), and the job folders held about 6 GB of source videos.

- YouTube videos download on Modal (`download_video` in `main.py`): a small CPU-only image with ffmpeg, yt-dlp and Deno writes `input.mp4` straight into the jobs volume, and reports download progress. `downloader.py` holds the yt-dlp settings for both places.
- If YouTube blocks the cloud download ("confirm you're not a bot", 429) or it fails, the runner downloads on the laptop and uploads, as before. `YOUTUBE_DOWNLOAD=laptop` in `.env` always does.
- After a successful run the video is deleted from the job folder and from the volume. A job keeps `job.json`, the transcript, the moments, the clips and the thumbnail. Failed runs keep a video they already have, for **Try again**. `--keep-video` keeps it (for `benchmark_gpu.py`).
- Re-rank downloads a YouTube video on Modal again. An uploaded MP4 is chosen again (Re-rank opens a file picker, or drop the same file on the home page), checked against its fingerprint and re-ranked.
- `python runner.py --prune-videos [--dry-run]` deletes the videos older versions kept in finished job folders, saving each upload's fingerprint and cover first.
- The download container's cost is part of the job's estimate (`costs.py`: CPU and memory, no GPU). The progress strip skips the upload step for YouTube videos, and the job's details say where the video was downloaded. On the stats page, a cloud download counts as cloud work.
- `YTDLP_VERSION` and `DENO_VERSION` pin the download image; bumping them and redeploying rebuilds only that small image.
- `benchmark_gpu.py` downloads a YouTube job's video on Modal when the job folder no longer has it.
- The running timer in the progress strip no longer causes a hydration warning in the browser.

**Most replayed and SponsorBlock**
- For YouTube videos the runner reads YouTube's "Most replayed" graph on the laptop before every run, re-rank included (`replay.py`: one metadata-only yt-dlp call, no download, browser cookies if set). Cloud IPs often get pages without the graph, so it is not taken from the Modal download.
- Replay hotspots are found in the graph's 100 segments after removing its biases: the opening (everyone starts there), one-segment bumps where a chapter starts (viewers jump to chapters) and sponsor reads; only peaks well above the video's median count, and peaks closer than 90 s merge.
- Each hotspot is offered to Gemini, which returns the cleanest 30-60 s clip containing it (set-up included) and scores it on the transcript alone. Hotspots Gemini skips are cut in code around the peak and scored in the re-scoring call. Every clip gets an audience score (50 = the video's median, 100 = its most replayed part), and the final score is 60% Gemini rubric, 40% audience (`REPLAY_WEIGHT`). Videos without a graph rank exactly as before.
- SponsorBlock (privacy-preserving hashed API): sponsor reads, self-promotion, subscribe reminders, intros, outros and cold-open previews are listed to Gemini as ranges to leave out, and any candidate that runs into them is dropped. A SponsorBlock "highlight" point becomes an extra hotspot.
- Clips page: the replay graph with every clip's span and rank chip (amber when the clip sits on a peak; click to jump to the clip), and the SponsorBlock ranges left out. Clip cards on a peak get a **Most replayed** badge; the score details add an Audience row and how the score was blended. The Details popover lists the hotspots and SponsorBlock parts.
- `python runner.py --backfill-replay [--force]` reads the graph and SponsorBlock for older YouTube videos and reports how many of their clips already sat on a hotspot.
- `.env`: `MOST_REPLAYED`, `SPONSORBLOCK` (on/off) and `REPLAY_WEIGHT`, all read by the runner (no redeploy).
- `render.py` passes the subtitle file to FFmpeg's `ass` filter by name, with FFmpeg started in its folder: a full Windows path (`C:\...`) broke the filter's option parsing, so the CPU render tests failed on Windows. Clips on Modal are unchanged.

**Stats ledger**
- Every run (first run, re-rank, failure) is appended as one line to `data/stats/runs.jsonl` (`run_history.py`), with the job's id, title and source. The stats page reads this ledger instead of the job folders, so deleting a video keeps its cost and times; it is listed as "deleted from the app".
- Removed the support for runs from before the parallel renderer: `--backfill-stats` and its `runner.log` parser, the "est." costs, the single-GPU pipeline labels and the "Current pipeline / All runs" switch on the stats page. The stats start from the ledger's first run.
- The stats page was rebuilt around what is worth checking (decided with an LLM-council review):
  - a This month / All time switch for the whole page;
  - three tiles: Modal's own bill for the month (`modal billing summary`, read by `runner.py --modal-bill` and after every run), the average new video (cost, time, cost per podcast hour) and the average re-rank, each with how many runs it is based on;
  - "By stage": the average new run and re-rank as stacked bars, time and cost side by side. Render time is the slowest clip (wall clock), its cost is all GPUs; Save is laptop time; Overhead is what the stages don't explain (container starts);
  - "Runs": a table of every run (newest first, 20 at a time) with its result ("Failed at render"), a time bar whose tooltip lists each stage's time and cost, and how this month's runs compare with Modal's charge for the app;
  - "Clip sources": two sentences on Most replayed and SponsorBlock;
  - a time-per-new-video dot chart once there are 10 new runs.
  - Removed: "Where the time goes", averages by podcast length, the insights card, earlier months, "est." marks. Render is magenta; amber stays the UI accent.
- Costs: each container reports its stage, and the runner prices the job with Modal's live rates (`modal billing rates`, read once a day into `data/stats/rates.json`; the built-in table in `costs.py` is the fallback). The ledger also records each run's stage costs, the failing stage, the clip-source counts, the clips-per-video setting and a short hash of the ranking prompts.
- Settings page (gear icon, `data/settings.json`, read by the runner before each run and overriding `.env`): currency (dollars or rupees), clips per video (3-8), Most replayed, audience weight (0-60%) and SponsorBlock.
- Rupees: the runner saves the day's USD to INR rate with every run (`fx.py`: Frankfurter, then ExchangeRate-API's open endpoint, then the last saved rate; free, no API key), so a run's rupee cost never changes. The monthly Modal bill uses today's rate. The video page's Details popover follows the currency too.

**YouTube downloads back on the laptop**

YouTube now blocks Modal's download ("Sign in to confirm you're not a bot"), so the cloud download only added a failed attempt to every run.

- Removed `download_video` and its CPU image from `main.py`, `YOUTUBE_DOWNLOAD`, `YTDLP_VERSION` and `DENO_VERSION`. `process_job` no longer takes `extra_usages`. The job details no longer say where the video was downloaded, and the stats page counts Download as laptop time with no Modal cost.
- The runner downloads into `%TEMP%\ai-podcast-clipper\<job id>` (outside OneDrive, which locked a partial file and crashed a run), uploads, and deletes the folder. `--keep-video` moves the video into the job folder instead.
- Flaky connections: a failed attempt keeps its partial file and the next attempt, or **Try again**, resumes it; yt-dlp retries pieces with a growing pause (2 s up to 30 s) and a 30 s socket timeout; IPv4 by default (`YOUTUBE_IPV4`). Cleaning up a partial file can no longer fail a run. Temp folders older than two days are cleared at the next run.
- `benchmark_gpu.py` downloads a YouTube job's video on the laptop and uploads it.
- The upload shows a percentage and the time left (Modal's upload progress callback, which `batch_upload` doesn't expose, so `upload_with_progress` builds the upload context itself; without it, the upload runs as before). Retried pieces are subtracted (the first version counted them twice and sat at 99%). `runner.log` gets the average speed every 10%, stalls (no data for 2 min, also shown in the strip) and the total time.
- Uploads on a slow or flaky connection: 4 pieces in flight instead of Modal's 128, and a 10-minute limit per piece (Modal sets none, so a dead connection could hang a piece forever); stuck pieces are retried by Modal, and a new run only sends the pieces Modal doesn't have.
- Waiting for the GPU rides out connection drops for up to 15 minutes instead of failing the run with "Deadline exceeded" while the job carries on at Modal.
- YouTube cookies: `YTDLP_COOKIES_FILE` (a cookies.txt, the way to use Chrome, whose cookies other programs can't read on Windows) or `YTDLP_COOKIES_FROM_BROWSER=firefox`. The runner checks them once per run and carries on without unreadable ones instead of failing the download. The Most replayed lookup uses them and IPv4 too, and when YouTube still refuses it, the graph comes from the download's own page data.

**Repository cleanup**
- `LICENSE` (was `LICENSE.MD`): template brackets fixed; Andreas Trolle's copyright kept, with a line for this fork's changes.
- `docs/architecture.excalidraw` replaces the upstream SaaS diagram (Stripe checkout, EC2 vs Modal), which described removed features.
- Removed code nothing used:
  - six shadcn components and the three packages only they needed (`@radix-ui/react-slot`, `@radix-ui/react-tabs`, `class-variance-authority`);
  - LR-ASD's AVA training and evaluation files and its unused AVA-trained weights. `asd/README.md` notes what the vendored copy keeps.
- `public/favicon.ico` is now the sound-wave logo (it was the upstream "PC" icon).
- The frontend README's file table matches the current app.

**Unchanged from upstream:** the LR-ASD model and speaker-selection rules, and the subtitle style.
