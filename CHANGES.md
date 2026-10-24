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
- One A10 container (instead of an L40S) runs transcription, Gemini ranking and rendering; `CLIPPER_GPU` in `.env` picks the GPU.
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

**Unchanged from upstream:** the LR-ASD active-speaker crop and the crop and subtitle rendering code.
