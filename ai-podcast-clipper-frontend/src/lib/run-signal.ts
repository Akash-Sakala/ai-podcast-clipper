"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { shortStage } from "./format";
import { isActive, type StageJob } from "./job-types";

/** What a page knows about one job, reported to the run signal. */
export type RunReport = StageJob & {
  id: string;
  title: string;
  clipCount: number;
};

/** A run this tab saw finish. */
export type FinishedRun = {
  id: string;
  title: string;
  status: "done" | "failed";
  clipCount: number;
  at: number;
};

export type RunSignal = {
  /** Jobs running now, from the tab's live list. */
  active: { id: string; title: string; stage: string }[];
  /** The last run this tab saw finish. */
  finished: FinishedRun | null;
};

const EMPTY: RunSignal = { active: [], finished: null };

let snapshot: RunSignal = EMPTY;
const reports = new Map<string, RunReport>();
const wasActive = new Map<string, boolean>();
const listeners = new Set<() => void>();
const finishListeners = new Set<(run: FinishedRun) => void>();

function publish(finished: FinishedRun | null) {
  const active = [...reports.values()]
    .filter(isActive)
    .map((job) => ({ id: job.id, title: job.title, stage: shortStage(job) }));
  const same =
    !finished &&
    active.length === snapshot.active.length &&
    active.every(
      (a, i) =>
        a.id === snapshot.active[i]?.id &&
        a.stage === snapshot.active[i]?.stage,
    );
  if (same) return;
  snapshot = { active, finished: finished ?? snapshot.finished };
  for (const listener of listeners) listener();
}

function report(jobs: RunReport[]) {
  let finished: FinishedRun | null = null;
  for (const job of jobs) {
    const active = isActive(job);
    const before = wasActive.get(job.id);
    wasActive.set(job.id, active);
    reports.set(job.id, job);
    if (before && !active && (job.status === "done" || job.status === "failed"))
      finished = {
        id: job.id,
        title: job.title,
        status: job.status,
        clipCount: job.clipCount,
        at: Date.now(),
      };
  }
  publish(finished);
  if (finished) for (const listener of finishListeners) listener(finished);
}

function forget(ids: string[]) {
  for (const id of ids) {
    reports.delete(id);
    wasActive.delete(id);
  }
  publish(null);
}

/**
 * Tells the run signal about the jobs the tab knows (components/jobs-sync.tsx, with
 * the live list of lib/job-sync.ts, on every page). A job that leaves the list is
 * forgotten; the others keep what they were, so a run that ends in the same update
 * that adds a video is still heard. Everything is forgotten on unmount.
 */
export function useRunReport(jobs: RunReport[]) {
  const [shown] = useState(() => ({ ids: [] as string[] }));
  useEffect(() => {
    const ids = new Set(jobs.map((job) => job.id));
    const gone = shown.ids.filter((id) => !ids.has(id));
    shown.ids = [...ids];
    if (gone.length) forget(gone);
    report(jobs);
  }, [jobs, shown]);
  useEffect(() => () => forget(shown.ids), [shown]);
}

export function useRunSignal(): RunSignal {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => snapshot,
    () => EMPTY,
  );
}

/** Called once for every run this tab sees finish. */
export function onRunFinished(listener: (run: FinishedRun) => void) {
  finishListeners.add(listener);
  return () => {
    finishListeners.delete(listener);
  };
}
