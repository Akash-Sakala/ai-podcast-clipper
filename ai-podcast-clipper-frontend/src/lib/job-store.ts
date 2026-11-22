import { randomBytes } from "node:crypto";
import fsp from "node:fs/promises";
import path from "node:path";
import type { Job } from "./job-types";
import { jobDir } from "./paths";

/**
 * Jobs are plain folders on disk - no database:
 *
 *   <DATA_DIR>/jobs/<job id>/
 *     job.json         status, written by this app and by runner.py
 *     input.mp4        an uploaded video, only until its run finishes (a YouTube video
 *                      is downloaded on the laptop and lives in a temp folder instead)
 *     transcript.json  reused by re-rank
 *     thumbnail.jpg    cover for the video list (YouTube's, or a frame of an upload)
 *     clips/clip_N.mp4 (+ clip_N.jpg posters)
 *     clips_new/       a re-rank's new clips: saved there while it runs, then kept until
 *                      the owner chooses what to keep (job.json "comparison"); with
 *                      moments.json.new, their moments
 *     clips_previous/, clips_final/   only for a moment while a set is swapped in
 *     runner.log       output of runner.py
 */

function newJobId() {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-` +
    `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${stamp}-${randomBytes(3).toString("hex")}`;
}

/**
 * Errors Windows gives while another program has the file open: runner.py writing
 * it, or OneDrive syncing the data folder. They pass within moments, so a read or a
 * replace is tried again (runner.py's Job.save does the same).
 */
const BUSY_CODES = new Set(["EPERM", "EBUSY", "EACCES"]);
const ATTEMPTS = 20;
const isBusy = (error: unknown) =>
  BUSY_CODES.has((error as NodeJS.ErrnoException).code ?? "");
const pause = (attempt: number) =>
  new Promise((resolve) => setTimeout(resolve, Math.min(50 * attempt, 250)));

async function replaceFile(tmp: string, file: string) {
  for (let attempt = 1; ; attempt++) {
    try {
      await fsp.rename(tmp, file);
      return;
    } catch (error) {
      if (!isBusy(error) || attempt >= ATTEMPTS) throw error;
      await pause(attempt);
    }
  }
}

export async function writeJob(job: Job) {
  const file = path.join(jobDir(job.id), "job.json");
  // Unique per write: concurrent writers (two polls, or runner.py's job.json.tmp)
  // must never share a temp file.
  const tmp = `${file}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
  // canRerank and hasThumbnail are derived on read and never stored.
  const stored: Job = { ...job };
  delete stored.canRerank;
  delete stored.rerankNeedsFile;
  delete stored.hasThumbnail;
  delete stored.clipsVersion;
  delete stored.clipVersions;
  try {
    await fsp.writeFile(tmp, JSON.stringify(stored, null, 2), "utf-8");
    await replaceFile(tmp, file);
    lastGood.set(job.id, stored);
  } catch (error) {
    await fsp.rm(tmp, { force: true });
    throw error;
  }
}

export async function createJob(input: {
  source: Job["source"];
  title: string;
  youtubeUrl?: string;
}): Promise<Job> {
  const id = newJobId();
  await fsp.mkdir(jobDir(id), { recursive: true });
  const now = new Date().toISOString();
  const job: Job = {
    id,
    source: input.source,
    title: input.title,
    youtubeUrl: input.youtubeUrl ?? null,
    status: "queued",
    message: "Waiting to start",
    error: null,
    clips: [],
    createdAt: now,
    updatedAt: now,
  };
  await writeJob(job);
  return job;
}

export async function updateJob(id: string, fields: Partial<Job>) {
  const job = await readJob(id);
  if (!job) return;
  await writeJob({ ...job, ...fields, updatedAt: new Date().toISOString() });
}

/**
 * The last job.json read or written per job. When the file can't be read for a
 * moment (busy, or half-written by an older runner), this copy is answered instead,
 * so the job never drops out of the list for one poll. Kept on globalThis so every
 * route bundle (and dev hot reloads) share it.
 */
const lastGood = ((
  globalThis as { __clipperJobs?: Map<string, Job> }
).__clipperJobs ??= new Map<string, Job>());

/** job.json of a job, or null when the job is gone (no folder or no job.json). */
export async function readJob(id: string): Promise<Job | null> {
  const file = path.join(jobDir(id), "job.json");
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const job = JSON.parse(await fsp.readFile(file, "utf-8")) as Job;
      lastGood.set(id, job);
      return job;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        lastGood.delete(id);
        return null;
      }
      // Busy, or not whole JSON yet: try again shortly, then use the last good copy.
      if (attempt < 3) await pause(attempt);
    }
  }
  return lastGood.get(id) ?? null;
}
