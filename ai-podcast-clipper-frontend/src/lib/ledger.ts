import fsp from "node:fs/promises";
import path from "node:path";
import type { LedgerRun } from "./job-types";
import { STATS_DIR } from "./paths";

/**
 * The stats ledger: one JSON object per line, appended by runner.py when a run ends
 * (backend run_history.py). Deleting a job folder leaves its lines here, so the stats
 * page keeps what the video cost.
 */
export const LEDGER_FILE = path.join(STATS_DIR, "runs.jsonl");

/** The parsed ledger per file, reused while the file is unchanged (polls are frequent). */
const cache = new Map<
  string,
  { mtimeMs: number; size: number; runs: LedgerRun[] }
>();

/** Every recorded run, oldest first. Lines that are not a run are skipped. */
export async function readLedger(file = LEDGER_FILE): Promise<LedgerRun[]> {
  let text: string;
  let stat: { mtimeMs: number; size: number };
  try {
    const found = await fsp.stat(file);
    stat = { mtimeMs: found.mtimeMs, size: found.size };
    const hit = cache.get(file);
    if (hit && hit.mtimeMs === stat.mtimeMs && hit.size === stat.size) {
      return hit.runs;
    }
    text = await fsp.readFile(file, "utf8");
  } catch {
    return [];
  }
  const runs: LedgerRun[] = [];
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      const value: unknown = JSON.parse(line);
      if (
        value &&
        typeof value === "object" &&
        typeof (value as LedgerRun).jobId === "string" &&
        typeof (value as LedgerRun).startedAt === "string" &&
        (value as LedgerRun).timings &&
        typeof (value as LedgerRun).timings === "object"
      )
        runs.push(value as LedgerRun);
    } catch {
      // A line cut short (for example by a crash mid-write): skip it.
    }
  }
  runs.sort((a, b) => a.startedAt.localeCompare(b.startedAt));
  cache.set(file, { ...stat, runs });
  return runs;
}
