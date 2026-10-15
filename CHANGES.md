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
- `torch`, `torchaudio`, `torchvision` and `whisperx` pinned to a set that resolves together (unpinned installs crash on `torchaudio.AudioMetaData`).
- LR-ASD vendored into `asd/` with a NumPy 2 fix.

**Unchanged from upstream:** the overall pipeline design (WhisperX transcription, Gemini moment selection, LR-ASD active-speaker crop, FFmpeg subtitles) and the crop and subtitle rendering code.
