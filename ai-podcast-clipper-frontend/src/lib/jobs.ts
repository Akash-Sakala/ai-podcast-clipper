import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import {
  ACTIVE_STATUSES,
  toJobSummary,
  type Job,
  type JobSummary,
} from "./job-types";
import { readJob } from "./job-store";
import {
  clipPath,
  inputPath,
  isValidJobId,
  jobDir,
  JOBS_DIR,
  thumbnailPath,
} from "./paths";
import { detectStaleJob } from "./stale-jobs";
import { youtubeVideoId } from "./youtube";

/** One job as the UI sees it: stale runners detected, canRerank derived. */
export async function getJob(id: string): Promise<Job | null> {
  if (!isValidJobId(id)) return null;
  const job = await readJob(id);
  if (!job) return null;
  const checked = await detectStaleJob(job);
  return withDerived(checked);
}

/** Every job with stale runners detected, newest first; nothing derived yet. */
async function loadJobs(): Promise<Job[]> {
  let entries: string[] = [];
  try {
    entries = await fsp.readdir(JOBS_DIR);
  } catch {
    return [];
  }

  const jobs = await Promise.all(
    entries.filter(isValidJobId).map(async (id) => {
      const job = await readJob(id);
      return job ? detectStaleJob(job) : null;
    }),
  );

  return (
    jobs
      .filter((job): job is Job => job !== null)
      // The replay graph is only shown on the video page: keep the list small.
      .map(({ replay: _replay, ...job }) => job)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
  );
}

export async function listJobs(): Promise<Job[]> {
  return (await loadJobs()).map(withDerived);
}

/** What the video list shows, per job (newest first): see JobSummary. */
export async function listJobSummaries(): Promise<JobSummary[]> {
  return (await loadJobs()).map((job) => toJobSummary(job, hasThumbnail(job)));
}

/** Just enough of every job for the stats page: its title and whether it still runs. */
export async function listJobBasics(): Promise<
  Pick<Job, "id" | "title" | "status">[]
> {
  return (await loadJobs()).map(({ id, title, status }) => ({
    id,
    title,
    status,
  }));
}

/** YouTube covers are fetched on first request, so any YouTube job has one. */
function hasThumbnail(job: Job) {
  return (
    !!youtubeVideoId(job.youtubeUrl) || fs.existsSync(thumbnailPath(job.id))
  );
}

/** Newest clip file time: changes when a run or re-rank replaces the clips. */
function clipsVersion(job: Job) {
  let newest = 0;
  for (const clip of job.clips) {
    try {
      newest = Math.max(newest, fs.statSync(clipPath(job.id, clip)).mtimeMs);
    } catch {
      // missing clip: ignore
    }
  }
  return Math.floor(newest).toString(36);
}

function withDerived(job: Job): Job {
  return {
    ...job,
    canRerank: canRerank(job),
    rerankNeedsFile: rerankNeedsFile(job),
    hasThumbnail: hasThumbnail(job),
    clipsVersion: clipsVersion(job),
  };
}

/**
 * A finished job can be re-ranked while its transcript is on disk. The video is
 * downloaded again (YouTube) or added again (an upload whose copy was deleted).
 * The runner now keeps the transcript as soon as it exists, so a job that has never
 * finished (a failed first run: not done, no clips) is not offered Re-rank, only Try again.
 */
export function canRerank(job: Job) {
  if (ACTIVE_STATUSES.includes(job.status)) return false;
  if (job.status !== "done" && !job.clips?.length) return false;
  return fs.existsSync(path.join(jobDir(job.id), "transcript.json"));
}

/** An uploaded video that is no longer in the job folder: re-rank needs the file again. */
export function rerankNeedsFile(job: Job) {
  return job.source === "upload" && !fs.existsSync(inputPath(job.id));
}
