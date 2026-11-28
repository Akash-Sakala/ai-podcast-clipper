# Changes vs. upstream

Forked from [Andreaswt/ai-podcast-clipper-saas](https://github.com/Andreaswt/ai-podcast-clipper-saas) (MIT) and turned into a single-user, local-first tool. This file lists the final state; the git log has the narrative.

## What differs from upstream

**Removed**
- The SaaS layer: NextAuth login, Stripe billing and credits, Prisma/Postgres, the Inngest queue, S3 uploads and the bearer-token FastAPI endpoint.
- WhisperX (replaced by Parakeet), AVA training and evaluation files of the vendored LR-ASD, and six unused shadcn components.

**Added**
- `runner.py` and its modules (`jobstore`, `youtube`, `transfer`, `media`, `billing`, `pipeline`, `phases`, `cloud`, `cutter`, `segments`, `maintenance`, `run_history`): downloads with yt-dlp, uploads to a Modal volume, calls the two deployed functions, saves each clip as it renders, and cleans up. Also a standalone CLI.
- A file-based job store (`data/jobs/<id>/job.json`, locked atomic writes) that detects dead runners, so jobs never hang as "processing".
- A Next.js UI bound to 127.0.0.1: home (link, MP4 upload up to 4 GB, drag and drop, live list, duplicate check), clips page, stats page, settings page, light and dark themes. Controls grow to 44 px on touch screens, icon-only buttons have tooltips (hover, keyboard focus, long press), and motion is limited to feedback (press, popovers, the theme icon, loading shimmer) from shared motion tokens, off under reduced motion. While a video runs: a step rail with the video download alongside, a titled place per clip that turns into the clip when it is saved, a "clips ready" line when it ends, a moving logo, the step in the tab title, and an optional browser notification when it is done. On the home page: a link checked as it is typed or pasted (a video added before shows before "Make clips"), paste anywhere, a full-page drop target for MP4s, upload progress with megabytes and time left, and a video list with running videos first, best scores, where and why a run failed, and search past 10 videos. Stats: bars open their breakdown on hover, focus or tap, values glide when the period changes, one set of step names everywhere. Settings: a "Saved" check (or "Couldn't save. Retry") beside each control, a keyboard stepper, a sliding currency pill, and a confirmation before removing kept videos. Clips: a full-screen viewer (keyboard, swipe, linkable, Back closes it), muted hover previews, neutral titles for clips picked without Gemini, and a gentler comparison (the Keep ring eases, the count pops). Ranking fails over faster: a stalled Gemini request or a used-up quota goes straight to the next model, and a request waits 75 s instead of 120 s.
- Re-rank (reuses the saved transcript and the kept video, then compares the new clips with the previous ones so you keep the best of both, clip by clip), Try again (resumes downloads, reuses the transcript, downloads clips kept on Modal), and a ZIP of all clips.
- Most replayed hotspots and SponsorBlock filtering for YouTube videos; the final score blends Gemini (60%) with the audience (40%).
- A run ledger (`data/stats/runs.jsonl`), the stats page (Modal's monthly bill, cost and time per stage, runs, the month's Gemini spend) and rupee costs at the rate of the day a run finished. Each Gemini call's token counts are kept and priced at Google's list prices (`gemini_costs.py`), so a run's cost is Modal plus Gemini; `rank_moments.py` prints the same estimate.
- Tooling: `benchmark_gpu.py` (GPU comparison, crop parity, clip previews), `rank_moments.py`, `download_weights.py` with a deploy-time weights check, `clipper_names.py` (a test deployment through `CLIPPER_APP`), pinned requirements, `pyproject.toml` (ruff, pytest), CI.

**Changed in the GPU backend (`main.py`)**
- A Modal volume exchange instead of S3 and HTTP. Two functions replace upstream's single call: `transcribe_and_rank` (phase 1) and `render_clips` (phase 2).
- Two-phase upload: the audio goes up first, then only a stream-copied footage piece per chosen clip; the whole video is uploaded only as a fallback. The YouTube download prefers at most 30 fps.
- Transcription with [NVIDIA Parakeet TDT 0.6B v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) (ONNX on CUDA, unbatched, English only), chosen over Parakeet v3 and Whistle on this project's own podcasts.
- Moment selection rewritten (`moments.py`, `prompts/`): sentence-line transcripts, a six-part rubric with a code-computed, value-first score (`SCORE_PROFILE`), clips of 30 s to 2 minutes that hold one whole thought (Gemini quotes the payoff line and code checks it is inside the cut, never truncating a proposal), one judge call that re-reads every final clip and may correct its boundaries, complete clips ranked above incomplete ones, overlap removal, top N by score (`clip_0` = best). `rank_moments.py --compare` shows saved picks against new ones before any GPU time. An ordered Gemini model chain with retries, and transcript-based fallback picks so a job is never empty.
- Rendering: one GPU container per clip, all at once, on an A10 by default (`CLIPPER_GPU`); LR-ASD runs in-process with batched S3FD and speaker scoring; one NVENC encode per clip with the original audio; flash-frame removal and a 0.5 s speaker hold. `Columbia_test.py` is kept as the parity reference. Containers check for the GPU and NVENC at start-up, and use a runtime CUDA base image.
- Per-clip error isolation, `max_clips` as a parameter, `torch` and `torchvision` pinned to a set that resolves together, LR-ASD vendored with a NumPy 2 fix.
- Nothing stays on Modal after a run. The video is kept on the laptop (`videostore.py`, `%LOCALAPPDATA%\ai-podcast-clipper\videos`, outside OneDrive) for 7 days after its last use with a 10 GB cap, so a re-rank needs no new download.

**Captions and sound**
- Captions on one line of up to five words (about 20 characters, mostly 3-4 words) that never run across a sentence (phrases split at commas are kept together; a long one is split into the fewest, most even captions; no one-word caption where a neighbour can spare a word), 25% up from the bottom, above the area a long Reels/Shorts title can cover and clear of the buttons on the right; each stays on screen at least 0.8 s when the next one starts later. The word being spoken turns amber (`#F5A524`) with a short pop. Anton 100 px with a heavier outline. Chosen from real Shorts, the platforms' safe zones and subtitle reading-speed guidance.
- An encode that fails with the loudness step is retried without it (the clip keeps the video's own level), so an optional step never costs a clip. **Render again** renders the last run's moments once more (no transcription, no Gemini) when every clip failed to render.
- Every clip's audio is brought to -14 LUFS (`CLIP_LOUDNORM`), so clips from different podcasts play at the same level.

**Daily use**
- `scripts/clipper.ps1` starts the app (rebuilding it when the code changed) and opens it in its own window; `scripts/install-shortcut.ps1` adds desktop and Start-menu shortcuts, optionally starting the server at sign-in. The app is installable from the browser (web manifest and icons).
- Cancel a running video from its page (`runner.py --cancel`: stops the runner, cancels its Modal calls and clip renders, cleans the volume) and delete a finished one (job folder, kept video, temp download; its stats stay).
- The app refuses requests whose Host is not this computer and changes that come from other sites (`src/middleware.ts`).
- Job data lives in `%LOCALAPPDATA%\ai-podcast-clipper\data`, outside OneDrive; `scripts/move-data.ps1` moves an older `data\` folder once.

**Robustness**
- Modal calls are cancelled on failure or interrupt, and every run sweeps leftover volume folders. Rendered clips stay on Modal when saving fails (`pendingSave`). Keeping chosen clips swaps them in file by file. Downloads resume; the video download starts while the audio uploads; all clips are saved at once and appear in the app as each lands; uploads use 4 pieces at once with a per-piece time limit; waiting for Modal rides out connection drops.

**Frontend**
- Loading, error and not-found screens; one live copy of the jobs shared by every page and tab (the newest read wins, so Back never shows an old list), a slim job list with ETag/304, job files written and read safely while Windows or OneDrive holds them open; accessibility fixes; `src/env.ts` instead of zod and t3-env; components split into `components/{stats,new-job,video}/` and `lib/` modules.

**Unchanged from upstream:** the LR-ASD model and speaker-selection rules.

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
- 2026-11-20 `feat/ui-foundation`: motion tokens, tooltips, touch-sized controls, contrast pass
- 2026-11-21 `feat/ui-live-run`: live run view with a titled place per clip, running logo, progress in the tab title, optional done notification
- 2026-11-21 `feat/ui-home`: link checked as it is typed, paste anywhere, full-page drop target, upload progress, video list at a glance
- 2026-11-21 `feat/ui-stats-settings`: stats breakdown popovers and gliding values, per-control "Saved" feedback in settings
- 2026-11-21 `feat/ui-clip-viewer`: full-screen clip viewer, poster cards with hover previews, neutral titles for fallback clips
- 2026-11-21 `fix/gemini-failover`: a stalled request or a used-up quota goes straight to the next Gemini model; 75 s request timeout; default chain `gemini-3.8-flash`, `gemini-3.6-flash`, `gemini-3.5-flash-lite`
- 2026-11-22 `feat/gemini-token-costs`: Gemini token counts per call and their estimated cost in each video's details, the stats page (a Gemini tile, Rank's cost split into Gemini and Modal) and `rank_moments.py`
- 2026-11-22 `fix/live-sync`: the home list and a video's page stay in sync (one shared job store, read again on every page, Back included, and across tabs); job.json writes retry while the file is busy instead of failing the request
- 2026-11-22 `fix/run-timings`: the Modal time of a run no longer counts the wait for the video download; a run that succeeds after a failure no longer keeps "failed at"
- 2026-11-22 `fix/resume-video-transfer`: Try again after a failed save keeps how the video was sent (audio first or whole), so the app names the right steps
- 2026-11-22 `fix/cancel-clip-renders`: a killed or failed run also cancels the clip renders it started on Modal (they kept running and wrote their clips back after the clean-up)
- 2026-11-28 `feat/daily-use-quick-wins`: one-click launcher and desktop shortcut, installable app window, Cancel and Delete in the app, requests from other sites refused, data moved out of OneDrive to `%LOCALAPPDATA%`
- 2026-11-28 `feat/caption-style`: one-line captions of 3-5 words, 25% up, the spoken word in amber; clip audio at -14 LUFS with a fallback; Render again
