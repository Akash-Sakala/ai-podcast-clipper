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
 *     clips/clip_N.mp4
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
  try {
    await fsp.writeFile(tmp, JSON.stringify(stored, null, 2), "utf-8");
    await fsp.rename(tmp, file);
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

export async function readJob(id: string): Promise<Job | null> {
  try {
    const raw = await fsp.readFile(path.join(jobDir(id), "job.json"), "utf-8");
    return JSON.parse(raw) as Job;
  } catch {
    return null;
  }
}
