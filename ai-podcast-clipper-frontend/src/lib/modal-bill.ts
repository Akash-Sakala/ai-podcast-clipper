import fsp from "node:fs/promises";
import path from "node:path";
import { DATA_DIR, startRunnerTask } from "./jobs";

/**
 * This month's Modal bill, as `modal billing summary` reports it. runner.py saves it
 * in <DATA_DIR>/stats/modal-bill.json after every run and when asked here
 * (`runner.py --modal-bill`); amounts are USD.
 */
export type ModalBill = {
  fetchedAt: string;
  /** "2026-11" (Modal's billing cycles are UTC calendar months). */
  month: string;
  meteredUsd: number;
  billedUsd: number;
  breakdown: Record<string, number>;
  adjustments: Record<string, number>;
};

const FILE = path.join(DATA_DIR, "stats", "modal-bill.json");
const MAX_AGE_MS = 15 * 60 * 1000;
let lastStarted = 0;

/** The saved bill (null if never read), refreshed in the background when older than 15 min. */
export async function readModalBill(): Promise<{
  bill: ModalBill | null;
  refreshing: boolean;
}> {
  let bill: ModalBill | null = null;
  try {
    bill = JSON.parse(await fsp.readFile(FILE, "utf8")) as ModalBill;
  } catch {
    bill = null;
  }
  const month = new Date().toISOString().slice(0, 7);
  const stale =
    !bill ||
    bill.month !== month ||
    Date.now() - new Date(bill.fetchedAt).getTime() > MAX_AGE_MS;
  let refreshing = false;
  if (stale && Date.now() - lastStarted > 2 * 60 * 1000) {
    lastStarted = Date.now();
    refreshing = startRunnerTask(["--modal-bill"], "modal-bill.log");
  }
  return { bill: bill && bill.month === month ? bill : null, refreshing };
}
