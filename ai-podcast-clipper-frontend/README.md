# Podcast Clipper - web UI

Local Next.js app for the podcast clipper. No accounts, payments or database:
- each video is a job folder under `../data/jobs/`;
- `../ai-podcast-clipper-backend/runner.py` processes it on Modal.

```powershell
npm install
npm run dev   # http://localhost:3000
```

See the main [README](../README.md) for full setup.

| Path | Purpose |
|---|---|
| `src/app/page.tsx` | Home: the input bar and **Your videos** |
| `src/app/videos/[jobId]/page.tsx` | Clips page of one video |
| `src/app/stats/page.tsx`, `src/components/stats-page.tsx` | Stats page: Modal bill, averages by stage, runs, clip sources |
| `src/app/settings/page.tsx`, `src/components/settings-page.tsx`, `src/app/api/settings` | Settings page, saved in `data/settings.json` |
| `src/components/dashboard-client.tsx`, `video-list.tsx` | Home page logic and the video list with live status |
| `src/components/new-job-bar.tsx` | YouTube link, MP4 upload, drag and drop, duplicate check |
| `src/components/video-page.tsx`, `clip-card.tsx` | Clips page, clip player, rank and score badge, Most replayed badge, downloads |
| `src/components/replay-wave.tsx` | The Most replayed graph on the clips page, with each clip marked |
| `src/components/video-parts.tsx` | Shared pieces: progress strip, details popover (time and cost), notices, actions |
| `src/app/api/jobs/...` | List and create jobs; get one; re-rank (takes the MP4 again for an upload); retry; duplicate check |
| `src/app/api/media/[jobId]/...` | Clips and posters (seeking, caching), ZIP of all clips, video cover |
| `src/lib/jobs.ts` | Job folders, stale-runner detection, starting `runner.py` |
| `src/lib/format.ts`, `job-types.ts` | Display text (progress, durations, cost) and the `job.json` types |
| `src/lib/ledger.ts`, `src/lib/stats.ts`, `src/lib/stats-server.ts`, `src/app/api/stats` | Stats worked out from the run ledger `data/stats/runs.jsonl` (kept when a video is deleted) |
| `src/lib/modal-bill.ts`, `src/lib/fx.ts`, `src/lib/app-settings.ts`, `src/lib/settings-types.ts` | Modal's monthly bill, the USD to INR rate, and the settings file |
| `src/lib/fingerprint.ts`, `youtube.ts` | Duplicate detection: upload fingerprint and YouTube video ID |
| `src/lib/upload.ts` | MP4 upload with progress, for new videos and for re-ranking an upload |
| `src/env.js` | Optional settings: `DATA_DIR`, `RUNNER_PYTHON` |
| `src/app/icon.svg`, `public/favicon.ico` | App icon (the sound-wave logo) |
