# Changes vs. upstream

Forked from [Andreaswt/ai-podcast-clipper-saas](https://github.com/Andreaswt/ai-podcast-clipper-saas) (MIT) and turned into a single-user, local-first tool. This file lists the final state; the git log has the narrative.

## What differs from upstream

**Removed**
- The SaaS layer: NextAuth login, Stripe billing and credits, Prisma/Postgres, the Inngest queue, S3 uploads and the bearer-token FastAPI endpoint.
- WhisperX (replaced by Parakeet), AVA training and evaluation files of the vendored LR-ASD, and six unused shadcn components.

**Added**
- `runner.py` and its modules (`jobstore`, `youtube`, `transfer`, `media`, `billing`, `pipeline`, `phases`, `cloud`, `cutter`, `segments`, `maintenance`, `run_history`): downloads with yt-dlp, uploads to a Modal volume, calls the two deployed functions, saves each clip as it renders, and cleans up. Also a standalone CLI.
- A file-based job store (`data/jobs/<id>/job.json`, locked atomic writes) that detects dead runners, so jobs never hang as "processing".
- A Next.js UI bound to 127.0.0.1: home (link, MP4 upload up to 4 GB, drag and drop, live list, duplicate check), clips page, stats page, settings page, light and dark themes. Controls grow to 44 px on touch screens, icon-only buttons have tooltips (hover, keyboard focus, long press), and motion is limited to feedback (press, popovers, the theme icon, loading shimmer) from shared motion tokens, off under reduced motion.
- Re-rank (reuses the saved transcript and the kept video, then compares the new clips with the previous ones so you keep the best of both, clip by clip), Try again (resumes downloads, reuses the transcript, downloads clips kept on Modal), and a ZIP of all clips.
- Most replayed hotspots and SponsorBlock filtering for YouTube videos; the final score blends Gemini (60%) with the audience (40%).
- A run ledger (`data/stats/runs.jsonl`), the stats page (Modal's monthly bill, cost and time per stage, runs) and rupee costs at the rate of the day a run finished.
- Tooling: `benchmark_gpu.py` (GPU comparison, crop parity, clip previews), `rank_moments.py`, `download_weights.py` with a deploy-time weights check, `clipper_names.py` (a test deployment through `CLIPPER_APP`), pinned requirements, `pyproject.toml` (ruff, pytest), CI.

**Changed in the GPU backend (`main.py`)**
- A Modal volume exchange instead of S3 and HTTP. Two functions replace upstream's single call: `transcribe_and_rank` (phase 1) and `render_clips` (phase 2).
- Two-phase upload: the audio goes up first, then only a stream-copied footage piece per chosen clip; the whole video is uploaded only as a fallback. The YouTube download prefers at most 30 fps.
- Transcription with [NVIDIA Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) (ONNX on CUDA, unbatched, English only), chosen over Parakeet v3 and Whistle on this project's own podcasts.
- Moment selection rewritten (`moments.py`, `prompts/`): sentence-line transcripts, a six-part rubric with a code-computed, value-first score (`SCORE_PROFILE`), clips of 30 s to 2 minutes that hold one whole thought (Gemini quotes the payoff line and code checks it is inside the cut, never truncating a proposal), one judge call that re-reads every final clip and may correct its boundaries, complete clips ranked above incomplete ones, overlap removal, top N by score (`clip_0` = best). `rank_moments.py --compare` shows saved picks against new ones before any GPU time. An ordered Gemini model chain with retries, and transcript-based fallback picks so a job is never empty.
- Rendering: one GPU container per clip, all at once, on an A10 by default (`CLIPPER_GPU`); LR-ASD runs in-process with batched S3FD and speaker scoring; one NVENC encode per clip with the original audio; flash-frame removal and a 0.5 s speaker hold. `Columbia_test.py` is kept as the parity reference. Containers check for the GPU and NVENC at start-up, and use a runtime CUDA base image.
- Per-clip error isolation, `max_clips` as a parameter, `torch` and `torchvision` pinned to a set that resolves together, LR-ASD vendored with a NumPy 2 fix.
- Nothing stays on Modal after a run. The video is kept on the laptop (`videostore.py`, `%LOCALAPPDATA%\ai-podcast-clipper\videos`, outside OneDrive) for 7 days after its last use with a 10 GB cap, so a re-rank needs no new download.

**Robustness**
- Modal calls are cancelled on failure or interrupt, and every run sweeps leftover volume folders. Rendered clips stay on Modal when saving fails (`pendingSave`). Keeping chosen clips swaps them in file by file. Downloads resume; the video download starts while the audio uploads; all clips are saved at once and appear in the app as each lands; uploads use 4 pieces at once with a per-piece time limit; waiting for Modal rides out connection drops.

**Frontend**
- Loading, error and not-found screens; a slim job list with ETag/304 and polling that pauses in hidden tabs; accessibility fixes; `src/env.ts` instead of zod and t3-env; components split into `components/{stats,new-job,video}/` and `lib/` modules.

**Unchanged from upstream:** the LR-ASD model and speaker-selection rules, and the subtitle style.

## History

- 2026-10-15 `main`: SaaS layer removed, local runner, file-based job store, YouTube and MP4 intake
- 2026-10-24 `feat/improvement`, `feat/ux-improvement`, `feat/clean-cuts-dedup`: Parakeet and ranked moments, redesigned UI, re-rank, clean cuts, duplicate check
- 2026-10-25 `feat/fast-render`: parallel per-clip GPU rendering, steady crop, flash-frame removal
- 2026-10-25 `chore/repo-cleanup`, `docs/readme-demo`: license, diagram, dead code, README demo
- 2026-10-25 `feat/stats-dashboard`: stats page with Modal cost and time per video
- 2026-10-30 `feat/cloud-download`: videos deleted after each run (the Modal download it added was removed on 2026-11-14)
- 2026-11-14 `feat/most-replayed`: Most replayed hotspots, audience score, SponsorBlock
- 2026-11-14 `feat/stats-ledger`: run ledger, stats page v2, settings page, rupee costs, laptop-only downloads
- 2026-11-14 `feat/optimized-version`: runner and frontend split into modules, two-phase upload and fps-capped download, robustness fixes, pinned dependencies, tooling and CI
- 2026-11-15 `feat/clip-quality-and-rerank`: complete clips up to 2 minutes with a payoff check and a judge call, kept videos for re-ranks, clip-by-clip comparison after a re-rank, video download during the audio upload, all clips saved at once and shown as they land
