import { spawn } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { env } from "~/env";
import { ACTIVE_STATUSES, type Job } from "./job-types";

export type { Job, JobStatus } from "./job-types";

/**
 * Jobs are plain folders on disk - no database:
 *
 *   <DATA_DIR>/jobs/<job id>/
 *     job.json        status, written by this app and by runner.py
 *     input.mp4       uploaded or downloaded video
 *     clips/clip_N.mp4
 *     runner.log      output of runner.py
 */

const REPO_ROOT = path.resolve(process.cwd(), "..");
const BACKEND_DIR = path.join(REPO_ROOT, "ai-podcast-clipper-backend");

export const DATA_DIR = path.resolve(
  env.DATA_DIR ?? path.join(REPO_ROOT, "data"),
);
export const JOBS_DIR = path.join(DATA_DIR, "jobs");

const RUNNER_PYTHON =
  env.RUNNER_PYTHON ??
  (process.platform === "win32"
    ? path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe")
    : path.join(BACKEND_DIR, ".venv", "bin", "python"));

const JOB_ID_PATTERN = /^\d{8}-\d{6}-[0-9a-f]{6}$/;
const CLIP_PATTERN = /^clip_\d+\.mp4$/;

export function isValidJobId(id: string) {
  return JOB_ID_PATTERN.test(id);
}

export function isValidClipName(name: string) {
  return CLIP_PATTERN.test(name);
}

export function jobDir(id: string) {
  if (!isValidJobId(id)) throw new Error(`Invalid job id: ${id}`);
  return path.join(JOBS_DIR, id);
}

export function clipPath(id: string, clip: string) {
  if (!isValidClipName(clip)) throw new Error(`Invalid clip name: ${clip}`);
  return path.join(jobDir(id), "clips", clip);
}

function newJobId() {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  const stamp =
    `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-` +
    `${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
  return `${stamp}-${randomBytes(3).toString("hex")}`;
}

async function writeJob(job: Job) {
  const file = path.join(jobDir(job.id), "job.json");
  const tmp = `${file}.tmp`;
  // canRerank is derived on read and never stored.
  const stored: Job = { ...job };
  delete stored.canRerank;
  await fsp.writeFile(tmp, JSON.stringify(stored, null, 2), "utf-8");
  await fsp.rename(tmp, file);
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

function isProcessAlive(pid: number) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

/** Marks jobs whose runner process died without finishing as failed. */
async function detectStaleJob(job: Job): Promise<Job> {
  if (!ACTIVE_STATUSES.includes(job.status)) return job;

  // Grace period from the last update, so a re-queued (re-rank) job isn't flagged early.
  const ageMs = Date.now() - new Date(job.updatedAt ?? job.createdAt).getTime();
  const runnerGone = job.pid ? !isProcessAlive(job.pid) : ageMs > 2 * 60_000;
  if (!runnerGone) return job;

  const failed: Job = {
    ...job,
    status: "failed",
    message: "Failed",
    error: "The runner stopped unexpectedly - see runner.log in the job folder",
  };
  await writeJob(failed);
  return failed;
}

/** One job as the UI sees it: stale runners detected, canRerank derived. */
export async function getJob(id: string): Promise<Job | null> {
  if (!isValidJobId(id)) return null;
  const job = await readJob(id);
  if (!job) return null;
  const checked = await detectStaleJob(job);
  return { ...checked, canRerank: canRerank(checked) };
}

export async function listJobs(): Promise<Job[]> {
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

  return jobs
    .filter((job): job is Job => job !== null)
    .map((job) => ({ ...job, canRerank: canRerank(job) }))
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

/** A finished job can be re-ranked when its video and transcript are still on disk. */
export function canRerank(job: Job) {
  if (ACTIVE_STATUSES.includes(job.status)) return false;
  const dir = jobDir(job.id);
  return (
    fs.existsSync(path.join(dir, "input.mp4")) &&
    fs.existsSync(path.join(dir, "transcript.json"))
  );
}

/** Starts runner.py for a job in the background and returns immediately. */
export async function startRunner(id: string, extraArgs: string[] = []) {
  const dir = jobDir(id);
  const runnerScript = path.join(BACKEND_DIR, "runner.py");

  if (!fs.existsSync(RUNNER_PYTHON)) {
    await updateJob(id, {
      status: "failed",
      message: "Failed",
      error: `Python not found at ${RUNNER_PYTHON}. Create the backend venv or set RUNNER_PYTHON in .env`,
    });
    return;
  }

  const logFd = fs.openSync(path.join(dir, "runner.log"), "a");
  const child = spawn(
    RUNNER_PYTHON,
    [runnerScript, "--job-dir", dir, ...extraArgs],
    {
      cwd: BACKEND_DIR,
      detached: true,
      windowsHide: true,
      stdio: ["ignore", logFd, logFd],
      env: {
        ...process.env,
        DATA_DIR,
        PYTHONUTF8: "1",
        PYTHONUNBUFFERED: "1",
      },
    },
  );

  child.on("error", (error) => {
    void updateJob(id, {
      status: "failed",
      message: "Failed",
      error: `Could not start the runner: ${error.message}`,
    });
  });

  child.unref();
  fs.closeSync(logFd);
}
