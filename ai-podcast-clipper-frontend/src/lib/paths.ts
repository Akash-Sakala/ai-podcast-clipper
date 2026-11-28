import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { env } from "~/env";

/** Where things live on disk. Server only (Node paths). */

export const REPO_ROOT = path.resolve(process.cwd(), "..");
export const BACKEND_DIR = path.join(REPO_ROOT, "ai-podcast-clipper-backend");

/**
 * The app's own folder outside the repo (so outside OneDrive on the laptop), like the
 * backend's jobstore.app_data_root: %LOCALAPPDATA%\ai-podcast-clipper on Windows, else
 * $XDG_DATA_HOME (or ~/.local/share)/ai-podcast-clipper.
 */
export const APP_DATA_ROOT = path.join(
  process.platform === "win32"
    ? (process.env.LOCALAPPDATA ?? path.join(os.homedir(), "AppData", "Local"))
    : (process.env.XDG_DATA_HOME ?? path.join(os.homedir(), ".local", "share")),
  "ai-podcast-clipper",
);

/** The repo's data folder, where versions before the move kept their jobs. */
export const LEGACY_DATA_DIR = path.join(REPO_ROOT, "data");

const holdsData = (dir: string) =>
  ["jobs", "stats", "settings.json"].some((name) =>
    fs.existsSync(path.join(dir, name)),
  );

/**
 * DATA_DIR, else <app data root>/data, except while only the repo's data folder holds
 * jobs (an older version's, not moved yet: scripts/move-data.ps1). The runner gets this
 * folder from the app (runner.ts) and picks the same one on its own (jobstore.py).
 */
function defaultDataDir() {
  const local = path.join(APP_DATA_ROOT, "data");
  return !holdsData(local) && holdsData(LEGACY_DATA_DIR)
    ? LEGACY_DATA_DIR
    : local;
}

export const DATA_DIR = path.resolve(env.DATA_DIR ?? defaultDataDir());
export const JOBS_DIR = path.join(DATA_DIR, "jobs");
export const STATS_DIR = path.join(DATA_DIR, "stats");

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

/** A clip of the new set a re-rank leaves for comparison (see ClipComparison). */
export function newClipPath(id: string, clip: string) {
  if (!isValidClipName(clip)) throw new Error(`Invalid clip name: ${clip}`);
  return path.join(jobDir(id), "clips_new", clip);
}

export function thumbnailPath(id: string) {
  return path.join(jobDir(id), "thumbnail.jpg");
}

export function inputPath(id: string) {
  return path.join(jobDir(id), "input.mp4");
}
