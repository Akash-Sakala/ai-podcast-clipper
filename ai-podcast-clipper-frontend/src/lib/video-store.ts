import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { ACTIVE_STATUSES } from "./job-types";
import { readJob } from "./job-store";
import { isValidJobId, STATS_DIR } from "./paths";

/**
 * Videos the runner keeps on this laptop after a run, so a re-rank needs no new
 * download (backend videostore.py). The runner writes where they are and its limits
 * to <DATA_DIR>/stats/video-store.json, so this app never reads the backend's .env.
 * Server only. Only `<dir>/<job id>/input.mp4` is ever read or removed here.
 */

export type VideoStoreStatus = {
  dir: string;
  keepDays: number;
  maxGb: number;
};

export type KeptVideos = {
  /** False until the runner has written its status file (no run since this feature). */
  known: boolean;
  count: number;
  bytes: number;
  keepDays: number;
  maxGb: number;
};

const STATUS_FILE = path.join(STATS_DIR, "video-store.json");
const VIDEO = "input.mp4";

function readStatusSync(): VideoStoreStatus | null {
  try {
    return parseStatus(JSON.parse(fs.readFileSync(STATUS_FILE, "utf-8")));
  } catch {
    return null;
  }
}

async function readStatus(): Promise<VideoStoreStatus | null> {
  try {
    return parseStatus(JSON.parse(await fsp.readFile(STATUS_FILE, "utf-8")));
  } catch {
    return null;
  }
}

function parseStatus(raw: unknown): VideoStoreStatus | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  if (typeof data.dir !== "string" || !path.isAbsolute(data.dir)) return null;
  const num = (value: unknown, fallback: number) =>
    typeof value === "number" && Number.isFinite(value) ? value : fallback;
  return {
    dir: data.dir,
    keepDays: num(data.keepDays, 7),
    maxGb: num(data.maxGb, 10),
  };
}

/** The job's kept video, if the store is known and holds a non-empty one. */
export function keptVideoPath(jobId: string): string | null {
  if (!isValidJobId(jobId)) return null;
  const status = readStatusSync();
  if (!status) return null;
  const file = path.join(status.dir, jobId, VIDEO);
  try {
    return fs.statSync(file).size > 0 ? file : null;
  } catch {
    return null;
  }
}

async function keptEntries(dir: string) {
  let names: string[];
  try {
    names = await fsp.readdir(dir);
  } catch {
    return [];
  }
  const entries: { id: string; bytes: number }[] = [];
  for (const id of names) {
    if (!isValidJobId(id)) continue;
    try {
      const stat = await fsp.stat(path.join(dir, id, VIDEO));
      if (stat.isFile()) entries.push({ id, bytes: stat.size });
    } catch {
      // no video in this folder
    }
  }
  return entries;
}

/** Live count and size of the kept videos, with the runner's limits. */
export async function keptVideosSummary(): Promise<KeptVideos> {
  const status = await readStatus();
  if (!status) {
    return { known: false, count: 0, bytes: 0, keepDays: 7, maxGb: 10 };
  }
  const entries = await keptEntries(status.dir);
  return {
    known: true,
    count: entries.length,
    bytes: entries.reduce((sum, entry) => sum + entry.bytes, 0),
    keepDays: status.keepDays,
    maxGb: status.maxGb,
  };
}

/**
 * Remove every kept video except those of jobs that are running. Only folders named
 * like a job id directly under the store are touched. A video that can't be removed
 * (open in a player on Windows) is skipped.
 */
export async function removeKeptVideos(): Promise<{
  removed: number;
  bytes: number;
}> {
  const status = await readStatus();
  if (!status) return { removed: 0, bytes: 0 };
  let removed = 0;
  let bytes = 0;
  for (const entry of await keptEntries(status.dir)) {
    const job = await readJob(entry.id);
    if (job && ACTIVE_STATUSES.includes(job.status)) continue;
    const folder = path.join(status.dir, entry.id);
    try {
      await fsp.rm(path.join(folder, VIDEO), { force: true });
    } catch {
      continue;
    }
    await fsp
      .rm(folder, { recursive: true, force: true })
      .catch(() => undefined);
    removed += 1;
    bytes += entry.bytes;
  }
  return { removed, bytes };
}
