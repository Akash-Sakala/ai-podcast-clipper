import fsp from "node:fs/promises";
import path from "node:path";
import type { LedgerRun } from "./job-types";
import { DATA_DIR } from "./jobs";

/**
 * The stats ledger: one JSON object per line, appended by runner.py when a run ends
 * (backend run_history.py). Deleting a job folder leaves its lines here, so the stats
 * page keeps what the video cost.
 */
export const LEDGER_FILE = path.join(DATA_DIR, "stats", "runs.jsonl");

/** Every recorded run, oldest first. Lines that are not a run are skipped. */
export async function readLedger(file = LEDGER_FILE): Promise<LedgerRun[]> {
  let text: string;
  try {
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
  return runs.sort((a, b) => a.startedAt.localeCompare(b.startedAt));
}
