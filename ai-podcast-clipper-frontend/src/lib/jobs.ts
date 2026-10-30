import { spawn } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { env } from "~/env";
import { ACTIVE_STATUSES, type Job } from "./job-types";
import { fingerprintRanges } from "./fingerprint";
import { youtubeVideoId } from "./youtube";

export type { Job, JobStatus } from "./job-types";

/**
 * Jobs are plain folders on disk - no database:
 *
 *   <DATA_DIR>/jobs/<job id>/
 *     job.json         status, written by this app and by runner.py
 *     input.mp4        an uploaded video, only until its run finishes (YouTube videos
 *                      download straight to Modal and never land here)
 *     transcript.json  reused by re-rank
 *     thumbnail.jpg    cover for the video list (YouTube's, or a frame of an upload)
 *     clips/clip_N.mp4
 *     runner.log       output of runner.py
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
  // canRerank and hasThumbnail are derived on read and never stored.
  const stored: Job = { ...job };
  delete stored.canRerank;
  delete stored.rerankNeedsFile;
  delete stored.hasThumbnail;
  delete stored.clipsVersion;
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
  return withDerived(checked);
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
    .map(withDerived)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function thumbnailPath(id: string) {
  return path.join(jobDir(id), "thumbnail.jpg");
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

/** Server: the same fingerprint as fingerprintFile() in the browser, for a file on disk. */
export async function fileFingerprint(file: string): Promise<string | null> {
  let handle: fsp.FileHandle | null = null;
  try {
    handle = await fsp.open(file, "r");
    const { size } = await handle.stat();
    const hash = createHash("sha256").update(`${size}:`);
    for (const [start, end] of fingerprintRanges(size)) {
      const buffer = Buffer.alloc(end - start);
      await handle.read(buffer, 0, buffer.length, start);
      hash.update(buffer);
    }
    return hash.digest("hex");
  } catch {
    return null;
  } finally {
    await handle?.close();
  }
}

/**
 * The newest job for the same video: the same YouTube video id, or an upload with
 * the same content fingerprint. Older uploads get their fingerprint computed (and
 * saved) the first time they are compared.
 */
export async function findDuplicate(query: {
  youtubeUrl?: string;
  fingerprint?: string;
}): Promise<Job | null> {
  const videoId = youtubeVideoId(query.youtubeUrl);
  if (!videoId && !query.fingerprint) return null;
  for (const job of await listJobs()) {
    if (videoId && youtubeVideoId(job.youtubeUrl) === videoId) return job;
    if (query.fingerprint && job.source === "upload") {
      let print = job.fingerprint;
      if (!print) {
        print =
          (await fileFingerprint(path.join(jobDir(job.id), "input.mp4"))) ??
          undefined;
        // Only idle jobs are written to; the runner owns job.json while it works.
        if (print && !ACTIVE_STATUSES.includes(job.status)) {
          await updateJob(job.id, { fingerprint: print });
        }
      }
      if (print === query.fingerprint) return job;
    }
  }
  return null;
}

export function inputPath(id: string) {
  return path.join(jobDir(id), "input.mp4");
}

/**
 * A finished job can be re-ranked while its transcript is on disk. The video is
 * downloaded again (YouTube) or added again (an upload whose copy was deleted).
 */
export function canRerank(job: Job) {
  if (ACTIVE_STATUSES.includes(job.status)) return false;
  return fs.existsSync(path.join(jobDir(job.id), "transcript.json"));
}

/** An uploaded video that is no longer in the job folder: re-rank needs the file again. */
export function rerankNeedsFile(job: Job) {
  return job.source === "upload" && !fs.existsSync(inputPath(job.id));
}

/**
 * On Windows a detached console program always gets its own console window
 * (Node ignores windowsHide for detached children), which pops up over the app.
 * pythonw.exe from the same environment runs the same Python without a console;
 * runner.py hides the windows of anything it starts in turn.
 */
function runnerInterpreter() {
  if (process.platform !== "win32") return RUNNER_PYTHON;
  const pythonw = RUNNER_PYTHON.replace(/python\.exe$/i, "pythonw.exe");
  return pythonw !== RUNNER_PYTHON && fs.existsSync(pythonw)
    ? pythonw
    : RUNNER_PYTHON;
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
    runnerInterpreter(),
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
