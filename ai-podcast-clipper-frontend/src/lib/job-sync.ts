"use client";

import { useEffect, useSyncExternalStore } from "react";
import { isActive, toJobSummary, type Job, type JobSummary } from "./job-types";

/**
 * One live copy of the jobs for the whole tab, shared by every page.
 *
 * Every piece of job data carries the server time it was read at: a page's HTML
 * passes `servedAt`, and the API answers with an `X-Served-At` header. Newer data
 * always wins, per job. So a page shown again from the router cache (Back, Forward)
 * never shows its old copy over what the tab saw since, and the home list and a
 * video's page always agree: what the video page polls also updates the list row,
 * and a list poll that sees a row change re-reads the open video page at once.
 *
 * `JobsSync` (mounted once in the layout) keeps the list fresh: every page opened,
 * the tab shown again, a page restored from the back-forward cache, and a poll
 * (3 s while a video runs, 10 s otherwise; 15 s in a hidden tab while one runs).
 * Changes made in this tab (a new video, Try again, Re-rank, keeping clips) are
 * announced to the app's other tabs, which refresh at once.
 */

/** Data and the server time (ms) it was read at. `at` moves forward in place. */
type Stamped<T> = { data: T; at: number };

export const LIST_POLL_MS = 3000;
/** Also catches runs started outside the app (runner.py from a terminal); an unchanged list is a 304. */
export const IDLE_LIST_POLL_MS = 10_000;
export const HIDDEN_POLL_MS = 15_000;
export const JOB_POLL_MS = 3000;

const summaries = new Map<string, Stamped<JobSummary>>();
const details = new Map<string, Stamped<Job>>();
/** Server time of the newest list read. */
let listAt = -Infinity;
let listSnapshot: JobSummary[] = [];
const EMPTY_LIST: JobSummary[] = [];
const listeners = new Set<() => void>();
/** Jobs whose page is open (how many times), so a list change re-reads them. */
const watched = new Map<string, number>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

const same = (a: unknown, b: unknown) =>
  a === b || JSON.stringify(a) === JSON.stringify(b);

/** Rebuilds the list (newest first), keeping the old array while nothing changed. */
function emit() {
  const next = [...summaries.values()]
    .map((entry) => entry.data)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const changed =
    next.length !== listSnapshot.length ||
    next.some((job, i) => job !== listSnapshot[i]);
  if (changed) listSnapshot = next;
  for (const listener of listeners) listener();
}

/** Stores `data` as of `at` unless newer data is already there; keeps unchanged objects. */
function put<T>(map: Map<string, Stamped<T>>, id: string, data: T, at: number) {
  const current = map.get(id);
  if (current && current.at > at) return false;
  if (current && same(current.data, data)) {
    current.at = at;
    return false;
  }
  map.set(id, { data, at });
  return true;
}

function summaryOf(job: Job): JobSummary {
  return toJobSummary(job, job.hasThumbnail ?? false);
}

/** A full list read at `at`. Jobs gone from it are dropped (unless read later on their own). */
function applyList(jobs: JobSummary[], at: number) {
  if (at < listAt) return;
  listAt = at;
  const seen = new Set<string>();
  const stale: string[] = [];
  for (const job of jobs) {
    seen.add(job.id);
    const before = summaries.get(job.id)?.data;
    put(summaries, job.id, job, at);
    // An open video page whose row changed is read again now, not at its next poll.
    const detail = details.get(job.id);
    if (
      watched.has(job.id) &&
      detail &&
      detail.at < at &&
      before !== summaries.get(job.id)?.data &&
      !same(summaryOf(detail.data), job)
    )
      stale.push(job.id);
  }
  for (const [id, entry] of summaries) {
    if (!seen.has(id) && entry.at <= at) {
      summaries.delete(id);
      details.delete(id);
    }
  }
  emit();
  for (const id of stale) void refreshJob(id);
}

/** One job read at `at`: also updates its row in the list. */
function applyJob(job: Job, at: number) {
  put(details, job.id, job, at);
  put(summaries, job.id, summaryOf(job), at);
  emit();
}

// --- Reading from the server ---------------------------------------------------

const known = new Map<string, { etag: string; body: unknown }>();

/**
 * GET with If-None-Match. A 304 hands back the body kept from the last 200, stamped
 * with the new server time: nothing changed up to then. null: no answer (the server
 * may be restarting) or 404.
 */
async function read<T>(url: string): Promise<Stamped<T> | null> {
  const cached = known.get(url);
  try {
    const response = await fetch(url, {
      cache: "no-store",
      headers: cached ? { "If-None-Match": cached.etag } : {},
    });
    const at = Number(response.headers.get("x-served-at")) || Date.now();
    if (response.status === 304 && cached)
      return { data: cached.body as T, at };
    if (!response.ok) {
      if (response.status === 404) known.delete(url);
      return null;
    }
    const body = (await response.json()) as T;
    const etag = response.headers.get("etag");
    if (etag) known.set(url, { etag, body });
    else known.delete(url);
    return { data: body, at };
  } catch {
    return null;
  }
}

/**
 * Runs `load` once at a time per key. A refresh asked for while one is already out may
 * be about a change made after it started, so one more read follows it.
 */
function serialized(load: (key: string) => Promise<void>) {
  const running = new Map<string, Promise<void>>();
  const queued = new Map<string, Promise<void>>();
  const start = (key: string): Promise<void> => {
    const now = running.get(key);
    if (!now) {
      const run = load(key).finally(() => running.delete(key));
      running.set(key, run);
      return run;
    }
    let next = queued.get(key);
    if (!next) {
      next = now.then(() => {
        queued.delete(key);
        return start(key);
      });
      queued.set(key, next);
    }
    return next;
  };
  return { start, busy: (key: string) => running.has(key) };
}

let lastListRead = 0;
const lists = serialized(async () => {
  lastListRead = Date.now();
  const result = await read<JobSummary[]>("/api/jobs");
  if (result) applyList(result.data, result.at);
});

const jobs = serialized(async (id) => {
  const result = await read<Job>(`/api/jobs/${id}`);
  if (result) applyJob(result.data, result.at);
});

/** Reads the list now (after any read already out). */
export function refreshJobs() {
  return lists.start("list");
}

/** Reads one job now (after any read of it already out). */
export function refreshJob(id: string) {
  return jobs.start(id);
}

/** A timer tick: skipped while the last read is still out, so a slow server gets no queue. */
function pollList() {
  if (!lists.busy("list")) void refreshJobs();
}

/** The tab came back (shown, focused, restored): read again unless just read. */
function revalidate() {
  if (Date.now() - lastListRead > 1000) void refreshJobs();
  for (const id of watched.keys()) if (!jobs.busy(id)) void refreshJob(id);
}

// --- Telling the app's other tabs ----------------------------------------------

const CHANNEL = "podcast-clipper-jobs";
let channel: BroadcastChannel | null = null;
function getChannel() {
  if (channel || typeof BroadcastChannel === "undefined") return channel;
  channel = new BroadcastChannel(CHANNEL);
  channel.onmessage = (event: MessageEvent<{ id?: string }>) => {
    void refreshJobs();
    const id = event.data?.id;
    if (id && watched.has(id)) void refreshJob(id);
  };
  return channel;
}

/**
 * After this tab changed a job (added a video, Try again, Re-rank, kept clips): read
 * it again here and tell the other tabs to do the same.
 */
export function jobsChanged(id?: string) {
  getChannel()?.postMessage({ id });
  return Promise.all([refreshJobs(), id ? refreshJob(id) : null]).then(
    () => undefined,
  );
}

// --- Hooks -----------------------------------------------------------------------

/**
 * The video list: the store's, or the page's own (server-rendered at `servedAt`)
 * while the store has nothing as new. The page's list is added to the store.
 */
export function useJobs(initial: JobSummary[], servedAt: number) {
  const list = useSyncExternalStore(
    subscribe,
    () => listSnapshot,
    () => EMPTY_LIST,
  );
  useEffect(() => applyList(initial, servedAt), [initial, servedAt]);
  return listAt >= servedAt ? list : initial;
}

/** Every job the tab knows of (for the header logo, the tab title and notifications). */
export function useAllJobs() {
  return useSyncExternalStore(
    subscribe,
    () => listSnapshot,
    () => EMPTY_LIST,
  );
}

/** True while any video is being processed. */
function useAnyActive() {
  return useSyncExternalStore(
    subscribe,
    () => listSnapshot.some(isActive),
    () => false,
  );
}

/**
 * One job, for its page: the store's copy, or the page's own while the store has
 * nothing as new. Read again when the page opens or the tab comes back, every 3 s
 * while it runs (15 s in a hidden tab), and at once when its row in the list changes.
 */
export function useJob(initial: Job, servedAt: number): Job {
  const id = initial.id;
  const entry = useSyncExternalStore(
    subscribe,
    () => details.get(id) ?? null,
    () => null,
  );
  useEffect(() => applyJob(initial, servedAt), [initial, servedAt]);
  const job = entry && entry.at >= servedAt ? entry.data : initial;
  const active = isActive(job);

  useEffect(() => {
    watched.set(id, (watched.get(id) ?? 0) + 1);
    void refreshJob(id);
    return () => {
      const count = (watched.get(id) ?? 1) - 1;
      if (count > 0) watched.set(id, count);
      else watched.delete(id);
    };
  }, [id]);

  useEffect(() => {
    if (!active) return;
    const tick = (visible: boolean) => () => {
      if ((document.visibilityState === "visible") !== visible) return;
      if (!jobs.busy(id)) void refreshJob(id);
    };
    const timer = setInterval(tick(true), JOB_POLL_MS);
    const hidden = setInterval(tick(false), HIDDEN_POLL_MS);
    return () => {
      clearInterval(timer);
      clearInterval(hidden);
    };
  }, [id, active]);

  return job;
}

/**
 * Keeps the list fresh for the whole tab; mounted once, in the layout. `path` is the
 * page shown: every page opened (Back and Forward too) reads the list again.
 */
export function useJobsSync(path: string) {
  const anyActive = useAnyActive();

  useEffect(() => {
    void refreshJobs();
  }, [path]);

  useEffect(() => {
    const visibleMs = anyActive ? LIST_POLL_MS : IDLE_LIST_POLL_MS;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") pollList();
    }, visibleMs);
    // A hidden tab still hears about a run ending (tab title, notification), slowly.
    const hidden = anyActive
      ? setInterval(() => {
          if (document.visibilityState === "hidden") pollList();
        }, HIDDEN_POLL_MS)
      : null;
    return () => {
      clearInterval(timer);
      if (hidden) clearInterval(hidden);
    };
  }, [anyActive]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") revalidate();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    window.addEventListener("online", onVisible);
    // A page restored from the browser's back-forward cache runs no new effects.
    const onShow = (event: PageTransitionEvent) => {
      if (event.persisted) revalidate();
    };
    window.addEventListener("pageshow", onShow);
    getChannel();
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
      window.removeEventListener("online", onVisible);
      window.removeEventListener("pageshow", onShow);
    };
  }, []);
}
