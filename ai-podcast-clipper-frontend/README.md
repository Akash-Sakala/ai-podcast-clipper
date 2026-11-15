# AI Podcast Clipper: web UI

Local Next.js app for the podcast clipper. No accounts, payments or database:

- each video is a job folder under `../data/jobs/`;
- `../ai-podcast-clipper-backend/runner.py` processes it on Modal.

```powershell
npm install
npm run dev                       # development, http://127.0.0.1:3000
npm run build; npm run start      # production build for daily use (npm run preview does both)
npm run lint; npx tsc --noEmit    # checks; also npm run format:check
```

The app listens on 127.0.0.1 only. See the main [README](../README.md) for full setup.

| Path                                                                                                       | Purpose                                                                                                     |
| ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `src/app/page.tsx`, `loading.tsx`, `error.tsx`, `not-found.tsx`                                            | Home: the input bar and **Your videos**, plus the loading, error and not-found screens                      |
| `src/app/videos/[jobId]/page.tsx`, `src/components/video/`                                                 | Clips page of one video: progress strip, details panel, notices, job actions                                |
| `src/components/clip-card.tsx`, `replay-wave.tsx`                                                          | Clip player, rank and score badge, Most replayed badge and graph                                            |
| `src/app/stats/page.tsx`, `src/components/stats/`                                                          | Stats page: Modal bill, averages by stage, runs, clip sources                                               |
| `src/app/settings/page.tsx`, `src/components/settings-page.tsx`, `src/app/api/settings`                    | Settings page, saved in `data/settings.json`                                                                |
| `src/components/new-job/`                                                                                  | YouTube link, MP4 upload, drag and drop, duplicate notice                                                   |
| `src/components/dashboard-client.tsx`, `video-list.tsx`                                                    | Home page logic and the video list with live status                                                         |
| `src/app/api/jobs/...`                                                                                     | List and create jobs; get one; re-rank (takes the MP4 again for an upload); retry; duplicate check          |
| `src/app/api/media/[jobId]/...`                                                                            | Clips and posters (seeking, caching), ZIP of all clips, video cover                                         |
| `src/lib/job-store.ts`, `stale-jobs.ts`, `jobs.ts`, `runner.ts`, `paths.ts`                                | Job folders, stale-runner detection, starting `runner.py`                                                   |
| `src/lib/duplicates.ts`, `fingerprint.ts`, `youtube.ts`                                                    | Duplicate detection: upload fingerprint and YouTube video ID                                                |
| `src/lib/format.ts`, `job-types.ts`                                                                        | Display text (progress steps, durations, cost) and the `job.json` types                                     |
| `src/lib/ledger.ts`, `stats.ts`, `stats-math.ts`, `stats-types.ts`, `stats-server.ts`, `src/app/api/stats` | Stats worked out from the run ledger `data/stats/runs.jsonl` (kept when a video is deleted)                 |
| `src/lib/modal-bill.ts`, `fx.ts`, `app-settings.ts`, `settings-types.ts`                                   | Modal's monthly bill, the USD to INR rate (read from cache, refreshed in the background), the settings file |
| `src/lib/use-polling.ts`, `upload.ts`                                                                      | Polling with ETag/304 that pauses in hidden tabs; MP4 upload with progress                                  |
| `src/env.ts`                                                                                               | Optional settings: `DATA_DIR`, `RUNNER_PYTHON` (YouTube cookies go in the backend `.env`)                   |
| `src/app/icon.svg`, `public/favicon.ico`                                                                   | App icon (the sound-wave logo)                                                                              |
