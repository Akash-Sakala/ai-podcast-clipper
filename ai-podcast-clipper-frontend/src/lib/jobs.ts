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
  newClipPath,
  thumbnailPath,
} from "./paths";
import { detectStaleJob } from "./stale-jobs";
import { keptVideoPath } from "./video-store";
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

/** Per clip file (finished or still running): its modified time, so one clip keeps its cache
 *  entry (and a card its identity) when the job finishes. Missing files are skipped.
 *  The live set is keyed by clip name; a re-rank's new set (clips_new/: saved while the
 *  re-rank runs, then waiting for a comparison) by "new/<clip name>". */
function clipVersions(job: Job) {
  const versions: Record<string, string> = {};
  const liveNames = (job.liveClips ?? []).map((clip) => clip.name);
  const newSet = job.liveClipsSet === "new";
  const live = new Set([...job.clips, ...(newSet ? [] : liveNames)]);
  const fresh = new Set([
    ...(job.comparison?.new.clips ?? []),
    ...(newSet ? liveNames : []),
  ]);
  const stamp = (file: () => string, key: string) => {
    try {
      versions[key] = Math.floor(fs.statSync(file()).mtimeMs).toString(36);
    } catch {
      // missing (or invalid) clip: ignore
    }
  };
  for (const name of live) stamp(() => clipPath(job.id, name), name);
  for (const name of fresh)
    stamp(() => newClipPath(job.id, name), `new/${name}`);
  return versions;
}

function withDerived(job: Job): Job {
  return {
    ...job,
    canRerank: canRerank(job),
    canRenderAgain: canRenderAgain(job),
    rerankNeedsFile: rerankNeedsFile(job),
    hasThumbnail: hasThumbnail(job),
    clipsVersion: clipsVersion(job),
    clipVersions: clipVersions(job),
  };
}

/**
 * A finished job can be re-ranked while its transcript is on disk. The video kept on
 * the laptop is reused; without one it is downloaded again (YouTube) or added again
 * (an upload).
 * The runner now keeps the transcript as soon as it exists, so a job that has never
 * finished (a failed first run: not done, no clips) is not offered Re-rank, only Try again.
 */
export function canRerank(job: Job) {
  if (ACTIVE_STATUSES.includes(job.status)) return false;
  if (job.status !== "done" && !job.clips?.length) return false;
  return fs.existsSync(path.join(jobDir(job.id), "transcript.json"));
}

/**
 * Render again: a finished job without clips (every clip failed to render, say) whose last
 * run's moments and transcript are on disk, so the same moments can be rendered once more.
 */
export function canRenderAgain(job: Job) {
  if (ACTIVE_STATUSES.includes(job.status) || job.status !== "done")
    return false;
  if (job.clips?.length || job.comparison) return false;
  const dir = jobDir(job.id);
  return (
    fs.existsSync(path.join(dir, "moments.json")) &&
    fs.existsSync(path.join(dir, "transcript.json"))
  );
}

/** An uploaded video that is neither in the job folder nor kept: re-rank needs the file again. */
export function rerankNeedsFile(job: Job) {
  return (
    job.source === "upload" &&
    !fs.existsSync(inputPath(job.id)) &&
    keptVideoPath(job.id) === null
  );
}
