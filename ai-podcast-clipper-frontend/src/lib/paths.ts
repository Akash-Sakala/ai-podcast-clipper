import path from "node:path";
import { env } from "~/env";

/** Where things live on disk. Server only (Node paths). */

export const REPO_ROOT = path.resolve(process.cwd(), "..");
export const BACKEND_DIR = path.join(REPO_ROOT, "ai-podcast-clipper-backend");

export const DATA_DIR = path.resolve(
  env.DATA_DIR ?? path.join(REPO_ROOT, "data"),
);
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
