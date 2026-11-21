"use client";

import {
  ChevronRight,
  RotateCcw,
  Search,
  Upload,
  X,
  Youtube,
} from "lucide-react";
import { m } from "motion/react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { displayTitle, stageSentence, stepsFor, timecode } from "~/lib/format";
import { isActive, type JobSummary } from "~/lib/job-types";
import { fadeRise } from "~/lib/motion";
import { cn } from "~/lib/utils";
import { RelativeTime } from "./relative-time";
import { ActionButton } from "./video/action-button";
import { MiniRail } from "./video/progress-strip";

/** With more videos than this, a search box appears above the list. */
const SEARCH_FROM = 10;

/** "Failed at Send clips" when the step is known, else "Failed". */
function failedLabel(job: JobSummary) {
  const step = stepsFor(job).find((s) => s.stage === job.failedStage);
  return step ? `Failed at ${step.label}` : "Failed";
}

function RowStatus({
  job,
  busy,
  onRetry,
}: {
  job: JobSummary;
  busy: boolean;
  onRetry: (job: JobSummary) => void;
}) {
  if (isActive(job)) {
    return (
      <div className="flex w-full min-w-0 flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
        <MiniRail job={job} />
        <span className="truncate text-sm font-medium">
          {stageSentence(job)}
        </span>
      </div>
    );
  }
  if (job.status === "failed") {
    return (
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-x-1">
          <span className="text-destructive text-sm font-medium whitespace-nowrap">
            {failedLabel(job)}
          </span>
          {job.mode !== "rerank" && (
            // Above the row's link so it can be clicked on its own.
            <span className="relative z-10">
              <ActionButton
                icon={<RotateCcw className="size-4" aria-hidden />}
                onClick={() => onRetry(job)}
                busy={busy}
              >
                Try again
              </ActionButton>
            </span>
          )}
        </div>
        {job.failure && (
          <span className="text-muted-foreground line-clamp-1 text-xs">
            {job.failure}
          </span>
        )}
      </div>
    );
  }
  if (job.choosing) {
    return (
      <span className="bg-tally-soft text-foreground inline-flex h-6 items-center rounded-md px-2 text-xs font-medium whitespace-nowrap">
        Choose clips to keep
      </span>
    );
  }
  const count = job.clipCount;
  return (
    <span className="flex items-center gap-2">
      <span
        className={cn(
          "tabular text-sm",
          count ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {count === 0 ? "No clips" : count === 1 ? "1 clip" : `${count} clips`}
      </span>
      {count > 0 && job.bestScore != null && (
        <span
          className={cn(
            "tabular inline-flex h-5 items-center gap-1 rounded px-1.5 text-xs font-semibold whitespace-nowrap",
            job.bestFromGemini
              ? "bg-tally text-tally-ink"
              : "bg-muted text-foreground",
          )}
        >
          <span aria-hidden className="font-medium">
            Best
          </span>
          <span aria-hidden>{job.bestScore}</span>
          <span className="sr-only">
            {`Best clip scores ${job.bestScore} of 100${job.bestFromGemini ? "" : ", picked without Gemini"}`}
          </span>
        </span>
      )}
    </span>
  );
}

function Thumbnail({ job }: { job: JobSummary }) {
  const [failed, setFailed] = useState(false);
  const seconds = job.durationSeconds;
  const Icon = job.source === "youtube" ? Youtube : Upload;

  return (
    <div className="bg-muted relative aspect-video w-24 shrink-0 overflow-hidden rounded-md sm:w-36">
      {job.hasThumbnail && !failed ? (
        // eslint-disable-next-line @next/next/no-img-element -- local API image, no optimisation needed
        <img
          src={`/api/media/${job.id}/thumbnail`}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          className="size-full object-cover"
        />
      ) : (
        <div className="text-muted-foreground flex size-full items-center justify-center">
          <Icon
            className="size-5"
            aria-label={job.source === "youtube" ? "From YouTube" : "Uploaded"}
          />
        </div>
      )}
      {seconds ? (
        <span className="tabular absolute right-1 bottom-1 rounded bg-black/75 px-1 py-px text-[11px] leading-4 font-medium text-white">
          {timecode(seconds)}
        </span>
      ) : null}
    </div>
  );
}

/**
 * "Your videos": videos being processed first (with their steps), then the rest newest
 * first. A done row shows its clip count and best score; a failed one where it stopped
 * and why. Rows that appear while the page is open fade in; rows that move (a video
 * starting or finishing) glide to their place. With more than 10 videos, a search box
 * filters by title.
 */
export function VideoList({
  jobs,
  busy,
  onRetry,
}: {
  jobs: JobSummary[];
  busy: string | null;
  onRetry: (job: JobSummary) => void;
}) {
  const [query, setQuery] = useState("");
  // Rows on screen when the page opened don't animate in; later ones do.
  const [initial] = useState(() => new Set(jobs.map((job) => job.id)));

  const sorted = useMemo(
    () => [...jobs.filter(isActive), ...jobs.filter((job) => !isActive(job))],
    [jobs],
  );
  const words = query.trim().toLowerCase();
  const shown = words
    ? sorted.filter((job) => displayTitle(job).toLowerCase().includes(words))
    : sorted;

  return (
    <div className="space-y-3">
      {jobs.length > SEARCH_FROM && (
        <label className="bg-card has-[input:focus-visible]:ring-ring relative flex items-center rounded-lg border has-[input:focus-visible]:ring-2">
          <span className="sr-only">Search your videos</span>
          <Search
            className="text-muted-foreground pointer-events-none absolute left-3 size-4"
            aria-hidden
          />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search your videos"
            className="placeholder:text-muted-foreground h-10 w-full rounded-lg bg-transparent pr-9 pl-9 text-base outline-hidden sm:text-sm [&::-webkit-search-cancel-button]:hidden"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              aria-label="Clear search"
              className="text-muted-foreground hover:text-foreground press absolute right-1.5 inline-flex size-7 items-center justify-center rounded-md pointer-coarse:right-0 pointer-coarse:size-11"
            >
              <X className="size-4" aria-hidden />
            </button>
          )}
        </label>
      )}

      {shown.length === 0 ? (
        <div className="text-muted-foreground rounded-xl border border-dashed px-6 py-8 text-sm">
          <p className="text-foreground mb-1 font-medium">
            No videos match &ldquo;{query.trim()}&rdquo;
          </p>
          Try a word from the title, or{" "}
          <button
            type="button"
            onClick={() => setQuery("")}
            className="hover:text-foreground underline underline-offset-4"
          >
            show all videos
          </button>
          .
        </div>
      ) : (
        <ul className="bg-card divide-y overflow-hidden rounded-xl border">
          {shown.map((job) => {
            const time = <RelativeTime iso={job.createdAt} />;
            return (
              <m.li
                key={job.id}
                layout="position"
                variants={fadeRise}
                initial={initial.has(job.id) ? false : "hidden"}
                animate="shown"
                className="hover:bg-accent/60 focus-within:bg-accent/60 has-[a:focus-visible]:ring-ring relative flex items-center gap-3 px-3 py-3 transition-colors has-[a:focus-visible]:ring-2 has-[a:focus-visible]:ring-inset sm:gap-4 sm:px-4"
              >
                <Thumbnail job={job} />
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <Link
                    href={`/videos/${job.id}`}
                    title={job.title}
                    className="block leading-snug font-medium after:absolute after:inset-0 focus-visible:outline-hidden"
                  >
                    <span className="line-clamp-2">{displayTitle(job)}</span>
                  </Link>
                  <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <RowStatus
                      job={job}
                      busy={busy === job.id}
                      onRetry={onRetry}
                    />
                    <span className="text-muted-foreground shrink-0 text-xs sm:hidden">
                      {time}
                    </span>
                  </div>
                </div>
                <span className="text-muted-foreground hidden w-24 shrink-0 text-right text-sm sm:block">
                  {time}
                </span>
                <ChevronRight
                  aria-hidden
                  className="text-muted-foreground hidden size-4 shrink-0 sm:block"
                />
              </m.li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
