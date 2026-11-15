import fsp from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { startRunnerTask } from "./runner";
import { STATS_DIR } from "./paths";

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

const FILE = path.join(STATS_DIR, "modal-bill.json");
/** Our own bookkeeping (runner.py never touches it): failed reads and when to retry. */
const ATTEMPTS_FILE = path.join(STATS_DIR, "modal-bill-attempts.json");
const MAX_AGE_MS = 15 * 60 * 1000;
const BASE_BACKOFF_MS = 2 * 60 * 1000;
const MAX_BACKOFF_MS = 60 * 60 * 1000;
/** A reader that has run this long is taken to be stuck. */
const HUNG_AFTER_MS = 5 * 60 * 1000;

type Attempts = { failures: number; nextAttemptAt: number };

// Shared by every route bundle (and dev hot reloads): is a reader running right now?
const live = ((
  globalThis as { __clipperBill?: { startedAt: number } }
).__clipperBill ??= { startedAt: 0 });
const running = () =>
  live.startedAt > 0 && Date.now() - live.startedAt < HUNG_AFTER_MS;

async function readAttempts(): Promise<Attempts> {
  try {
    const a = JSON.parse(await fsp.readFile(ATTEMPTS_FILE, "utf8")) as Attempts;
    return { failures: a.failures || 0, nextAttemptAt: a.nextAttemptAt || 0 };
  } catch {
    return { failures: 0, nextAttemptAt: 0 };
  }
}

async function writeAttempts(attempts: Attempts) {
  try {
    await fsp.mkdir(STATS_DIR, { recursive: true });
    const tmp = `${ATTEMPTS_FILE}.${process.pid}.${randomBytes(3).toString("hex")}.tmp`;
    await fsp.writeFile(tmp, JSON.stringify(attempts));
    await fsp.rename(tmp, ATTEMPTS_FILE);
  } catch {
    // Bookkeeping only: never break the stats page over it.
  }
}

async function readBill(): Promise<ModalBill | null> {
  try {
    return JSON.parse(await fsp.readFile(FILE, "utf8")) as ModalBill;
  } catch {
    return null;
  }
}

/** Starts `runner.py --modal-bill`; failures back off 2 min, 4 min ... up to 1 h. */
async function startRefresh(failures: number) {
  live.startedAt = Date.now(); // before any await, so a second request sees it running
  const previous = (await readBill())?.fetchedAt ?? "";
  const onEnd = (ok: boolean) => {
    live.startedAt = 0;
    void (async () => {
      // Exit code 0 is not enough: the bill file must really have been refreshed.
      const fresh = ok && ((await readBill())?.fetchedAt ?? "") !== previous;
      if (fresh) return writeAttempts({ failures: 0, nextAttemptAt: 0 });
      const next = failures + 1;
      const wait = Math.min(BASE_BACKOFF_MS * 2 ** failures, MAX_BACKOFF_MS);
      await writeAttempts({ failures: next, nextAttemptAt: Date.now() + wait });
    })();
  };
  const started = startRunnerTask(["--modal-bill"], "modal-bill.log", onEnd);
  if (!started) {
    onEnd(false); // the backend Python environment is missing
  }
}

/**
 * The saved bill (null if never read), refreshed in the background when older than
 * 15 min. `refreshing` is true exactly while a reader process is running; after a
 * failure the next try waits (2 min, doubling, up to 1 h).
 */
export async function readModalBill(): Promise<{
  bill: ModalBill | null;
  refreshing: boolean;
}> {
  const bill = await readBill();
  const month = new Date().toISOString().slice(0, 7);
  const stale =
    !bill ||
    bill.month !== month ||
    Date.now() - new Date(bill.fetchedAt).getTime() > MAX_AGE_MS;
  if (stale && !running()) {
    const attempts = await readAttempts();
    if (Date.now() >= attempts.nextAttemptAt)
      await startRefresh(attempts.failures);
  }
  return {
    bill: bill && bill.month === month ? bill : null,
    refreshing: running(),
  };
}
