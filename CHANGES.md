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

**Repository cleanup**
- `LICENSE` (was `LICENSE.MD`): template brackets fixed; Andreas Trolle's copyright kept, with a line for this fork's changes.
- `docs/architecture.excalidraw` replaces the upstream SaaS diagram (Stripe checkout, EC2 vs Modal), which described removed features.
- Removed code nothing used:
  - six shadcn components and the three packages only they needed (`@radix-ui/react-slot`, `@radix-ui/react-tabs`, `class-variance-authority`);
  - LR-ASD's AVA training and evaluation files and its unused AVA-trained weights. `asd/README.md` notes what the vendored copy keeps.
- `public/favicon.ico` is now the sound-wave logo (it was the upstream "PC" icon).
- The frontend README's file table matches the current app.

**Unchanged from upstream:** the LR-ASD model and speaker-selection rules, and the subtitle style.
