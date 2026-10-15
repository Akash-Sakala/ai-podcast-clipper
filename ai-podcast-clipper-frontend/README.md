# Podcast Clipper - web UI

Local Next.js dashboard for the podcast clipper. No accounts, payments or database:
jobs are folders under `../data/jobs/` and processing is done by
`../ai-podcast-clipper-backend/runner.py` on a Modal GPU.

```powershell
npm install
npm run dev   # http://localhost:3000
```

See the main [README](../README.md) for full setup.

| Path | Purpose |
|---|---|
| `src/app/page.tsx` | Dashboard (server component, lists jobs) |
| `src/components/dashboard-client.tsx` | YouTube form, file upload, jobs table |
| `src/components/clip-display.tsx` | Clip gallery |
| `src/app/api/jobs/route.ts` | `GET` list jobs, `POST` create job (upload or YouTube) |
| `src/app/api/media/[jobId]/[clip]/route.ts` | Streams clip files (supports seeking, `?download`) |
| `src/lib/jobs.ts` | Job folders + starting `runner.py` |
| `src/env.js` | Optional settings: `DATA_DIR`, `RUNNER_PYTHON` |
