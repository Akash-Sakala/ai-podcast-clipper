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
| `src/app/stats/page.tsx`, `src/components/stats-page.tsx` | Stats page: Modal spend, times by podcast length, cost and runs per video |
| `src/components/dashboard-client.tsx`, `video-list.tsx` | Home page logic and the video list with live status |
| `src/components/new-job-bar.tsx` | YouTube link, MP4 upload, drag and drop, duplicate check |
| `src/components/video-page.tsx`, `clip-card.tsx` | Clips page, clip player, rank and score badge, downloads |
| `src/components/video-parts.tsx` | Shared pieces: progress strip, details popover (time and cost), notices, actions |
| `src/app/api/jobs/...` | List and create jobs; get one; re-rank; retry; duplicate check |
| `src/app/api/media/[jobId]/...` | Clips and posters (seeking, caching), ZIP of all clips, video cover |
| `src/lib/jobs.ts` | Job folders, stale-runner detection, starting `runner.py` |
| `src/lib/format.ts`, `job-types.ts` | Display text (progress, durations, cost) and the `job.json` types |
| `src/lib/stats.ts`, `src/app/api/stats` | Stats worked out from each job's run history |
| `src/lib/fingerprint.ts`, `youtube.ts` | Duplicate detection: upload fingerprint and YouTube video ID |
| `src/env.js` | Optional settings: `DATA_DIR`, `RUNNER_PYTHON` |
| `src/app/icon.svg`, `public/favicon.ico` | App icon (the sound-wave logo) |
