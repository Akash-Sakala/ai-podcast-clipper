"use client";

import { ChevronRight, RotateCcw, Upload, Youtube } from "lucide-react";
import Link from "next/link";
import {
  currentStage,
  relativeTime,
  stageSentence,
  stepsFor,
} from "~/lib/format";
import { isActive, type Job } from "~/lib/job-types";
import { cn } from "~/lib/utils";
import { ActionButton, displayTitle, useNow } from "./video-parts";

/** Share of the whole job that is done, for the slim bar in a row. */
function overallProgress(job: Job) {
  const steps = stepsFor(job);
  const stage = currentStage(job);
  const index = Math.max(
    0,
    steps.findIndex((s) => s.stage === stage),
  );
  const p = job.progress;
  const within =
    stage === "rendering" && p?.total ? (p.done ?? 0) / p.total : 0.35;
  return Math.min(1, (index + within) / steps.length);
}

function RowStatus({
  job,
  busy,
  onRetry,
}: {
  job: Job;
  busy: boolean;
  onRetry: (job: Job) => void;
}) {
  if (isActive(job)) {
    return (
      <div className="flex min-w-0 items-center gap-3">
        <div className="bg-muted h-1.5 w-20 shrink-0 overflow-hidden rounded-full">
          <span
            className="bg-tally block h-full transition-[width] duration-500"
            style={{ width: `${Math.max(6, overallProgress(job) * 100)}%` }}
          />
        </div>
        <span className="truncate text-sm font-medium" aria-live="polite">
          {stageSentence(job)}
        </span>
      </div>
    );
  }
  if (job.status === "failed") {
    return (
      <div className="flex items-center gap-2">
        <span className="text-destructive text-sm font-medium">Failed</span>
        {job.mode !== "rerank" && (
          // Above the row's link so it can be clicked on its own.
          <span className="relative z-10">
            <ActionButton
              icon={<RotateCcw className="size-4" />}
              onClick={() => onRetry(job)}
              busy={busy}
            >
              Try again
            </ActionButton>
          </span>
        )}
      </div>
    );
  }
  const count = job.clips.length;
  return (
    <span
      className={cn(
        "tabular text-sm",
        count ? "text-foreground" : "text-muted-foreground",
      )}
    >
      {count === 0 ? "No clips" : count === 1 ? "1 clip" : `${count} clips`}
    </span>
  );
}

export function VideoList({
  jobs,
  busy,
  onRetry,
}: {
  jobs: Job[];
  busy: string | null;
  onRetry: (job: Job) => void;
}) {
  const now = useNow(jobs.some(isActive));

  return (
    <ul className="bg-card divide-y overflow-hidden rounded-xl border">
      {jobs.map((job) => (
        <li
          key={job.id}
          className="hover:bg-accent/60 focus-within:bg-accent/60 has-[a:focus-visible]:ring-ring relative grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1 px-4 py-3.5 transition-colors has-[a:focus-visible]:ring-2 has-[a:focus-visible]:ring-inset sm:grid-cols-[auto_minmax(0,1fr)_15rem_6.5rem_auto] sm:gap-x-4 sm:px-5"
        >
          {job.source === "youtube" ? (
            <Youtube
              className="text-muted-foreground size-4"
              aria-label="From YouTube"
            />
          ) : (
            <Upload
              className="text-muted-foreground size-4"
              aria-label="Uploaded"
            />
          )}
          <Link
            href={`/videos/${job.id}`}
            className="truncate font-medium after:absolute after:inset-0 focus-visible:outline-none"
            title={job.title}
          >
            {displayTitle(job)}
          </Link>
          <div className="col-start-2 row-start-2 min-w-0 sm:col-start-auto sm:row-start-auto">
            <RowStatus job={job} busy={busy === job.id} onRetry={onRetry} />
          </div>
          <time
            dateTime={job.createdAt}
            suppressHydrationWarning
            className="text-muted-foreground col-start-3 row-start-1 text-right text-sm sm:col-start-auto sm:row-start-auto"
          >
            {relativeTime(job.createdAt, now)}
          </time>
          <ChevronRight
            aria-hidden
            className="text-muted-foreground hidden size-4 sm:block"
          />
        </li>
      ))}
    </ul>
  );
}
