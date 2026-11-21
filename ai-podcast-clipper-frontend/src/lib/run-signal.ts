"use client";

import { useEffect, useSyncExternalStore } from "react";
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
  /** Jobs running now, as the pages on screen report them. */
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
 * A page tells the run signal about the jobs it polls (the home list, one video's page).
 * The reports last while the page is mounted, so a page that knows nothing about jobs
 * (stats, settings) never shows a stale "running".
 */
export function useRunReport(jobs: RunReport[]) {
  useEffect(() => {
    report(jobs);
  }, [jobs]);
  const ids = jobs.map((job) => job.id).join(",");
  useEffect(() => () => forget(ids ? ids.split(",") : []), [ids]);
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
