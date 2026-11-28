import { isActive, type Job } from "./job-types";
import { readJob, writeJob } from "./job-store";

/**
 * Jobs whose upload is still streaming into this server. Such a job is `queued` with
 * no runner pid yet, which the stale check would otherwise take for a dead runner.
 * Kept on globalThis so every route bundle (and dev hot reloads) share one set.
 */
const uploading = ((
  globalThis as { __clipperUploading?: Set<string> }
).__clipperUploading ??= new Set<string>());

/** Marks a job as receiving its upload; call the returned function when it is over. */
export function markUploading(id: string) {
  uploading.add(id);
  return () => void uploading.delete(id);
}

/**
 * Jobs being cancelled (runner.py --cancel): their runner is stopped first and job.json
 * is written a few seconds later, so meanwhile a dead runner is expected, not a crash.
 */
const cancelling = ((
  globalThis as { __clipperCancelling?: Set<string> }
).__clipperCancelling ??= new Set<string>());

/** Marks a job as being cancelled; call the returned function when the cancel is over. */
export function markCancelling(id: string) {
  cancelling.add(id);
  return () => void cancelling.delete(id);
}

export const isCancelling = (id: string) => cancelling.has(id);

function isProcessAlive(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

/** True when nothing is running this job any more (as far as can be told from `job`). */
function runnerGone(job: Job) {
  if (cancelling.has(job.id)) return false;
  if (job.pid) return !isProcessAlive(job.pid);
  // No pid yet: an upload still arriving is fine; otherwise allow a grace period from
  // the last update, so a re-queued (re-rank) job isn't flagged early.
  if (uploading.has(job.id)) return false;
  const ageMs = Date.now() - new Date(job.updatedAt ?? job.createdAt).getTime();
  return ageMs > 2 * 60_000;
}

/**
 * Marks jobs whose runner process died without finishing as failed. The job is read
 * again right before writing: it is only failed if it is still in the same active
 * state with the same pid and last update (the runner may just have finished).
 */
export async function detectStaleJob(job: Job): Promise<Job> {
  if (!isActive(job) || !runnerGone(job)) return job;

  const fresh = await readJob(job.id);
  if (!fresh) return job;
  if (
    fresh.status !== job.status ||
    fresh.pid !== job.pid ||
    fresh.updatedAt !== job.updatedAt ||
    !isActive(fresh) ||
    !runnerGone(fresh)
  ) {
    return fresh; // it moved on meanwhile; a later poll judges it again
  }

  const failed: Job = {
    ...fresh,
    status: "failed",
    message: "Failed",
    error: "The runner stopped unexpectedly - see runner.log in the job folder",
  };
  await writeJob(failed);
  return failed;
}
