"use client";

import {
  ArrowLeft,
  ListRestart,
  Download,
  Info,
  RotateCcw,
  Upload,
  Youtube,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { failureSentence, relativeTime } from "~/lib/format";
import { isActive, type Job } from "~/lib/job-types";
import { cn } from "~/lib/utils";
import { ClipCard } from "./clip-card";
import { Popover } from "./popover";
import {
  ActionButton,
  DetailsPanel,
  displayTitle,
  fallbackCount,
  Notice,
  ProgressStrip,
  useJobActions,
  useNow,
} from "./video-parts";

const POLL_INTERVAL_MS = 3000;

export function VideoPage({ initialJob }: { initialJob: Job }) {
  const [job, setJob] = useState(initialJob);
  const active = isActive(job);
  const now = useNow(active);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`/api/jobs/${job.id}`, {
        cache: "no-store",
      });
      if (response.ok) setJob((await response.json()) as Job);
    } catch {
      // The dev server may be restarting; the next poll tries again.
    }
  }, [job.id]);

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refresh(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [active, refresh]);

  const { busy, rerank, retry } = useJobActions(refresh);
  const isBusy = busy === job.id;
  const details = new Map((job.clipDetails ?? []).map((d) => [d.name, d]));
  const fallbacks = fallbackCount(job);
  const done = job.status === "done";

  const rerankButton = !!job.canRerank && !active && (
    <ActionButton
      icon={<ListRestart className="size-4" />}
      onClick={() => rerank(job)}
      busy={isBusy}
    >
      Re-rank
    </ActionButton>
  );
  const downloadAll = job.clips.length > 1 && !active && (
    <ActionButton
      icon={<Download className="size-4" />}
      href={`/api/media/${job.id}/all`}
    >
      <span className="sr-only sm:not-sr-only">Download all</span>
    </ActionButton>
  );
  // Re-rank sits in the header only when no notice already offers it.
  const headerRerank = done && fallbacks === 0 && job.clips.length > 0;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
      <Link
        href="/"
        className="text-muted-foreground hover:text-foreground -ml-1.5 inline-flex h-8 items-center gap-1.5 rounded-md px-1.5 text-sm font-medium transition-colors"
      >
        <ArrowLeft className="size-4" />
        All videos
      </Link>

      <header className="mt-3 mb-6 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1
            className="font-display text-2xl leading-tight font-semibold tracking-tight text-balance sm:text-3xl"
            title={job.title}
          >
            {displayTitle(job)}
          </h1>
          <p className="text-muted-foreground mt-2 flex items-center gap-1.5 text-sm">
            {job.source === "youtube" ? (
              <Youtube className="size-3.5" aria-label="From YouTube" />
            ) : (
              <Upload className="size-3.5" aria-label="Uploaded" />
            )}
            <time dateTime={job.createdAt} suppressHydrationWarning>
              {relativeTime(job.createdAt, now)}
            </time>
          </p>
        </div>
        <div className="-mr-1.5 flex shrink-0 items-center gap-0.5 sm:mr-0">
          {downloadAll}
          {headerRerank && !!job.canRerank && (
            <ActionButton
              icon={<ListRestart className="size-4" />}
              onClick={() => rerank(job)}
              busy={isBusy}
            >
              <span className="sr-only sm:not-sr-only">Re-rank</span>
            </ActionButton>
          )}
          <Popover
            label="Details"
            trigger={<Info className="size-4" />}
            align="end"
          >
            {() => <DetailsPanel job={job} />}
          </Popover>
        </div>
      </header>

      <div className="space-y-6">
        {active && (
          <div className="bg-card rounded-xl border p-4 sm:p-5">
            <ProgressStrip job={job} now={now} />
          </div>
        )}

        {job.status === "failed" && (
          <Notice
            tone="error"
            action={
              <>
                {job.mode !== "rerank" && (
                  <ActionButton
                    primary
                    icon={<RotateCcw className="size-4" />}
                    onClick={() => retry(job)}
                    busy={isBusy}
                  >
                    Try again
                  </ActionButton>
                )}
                {rerankButton}
              </>
            }
          >
            {failureSentence(job)}
            {job.clips.length > 0 && " Your previous clips are below."}
          </Notice>
        )}

        {done && job.clips.length === 0 && (
          <Notice action={rerankButton}>
            No clips came out of this video. Re-rank to ask Gemini again without
            transcribing a second time.
          </Notice>
        )}

        {done && fallbacks > 0 && (
          <Notice action={rerankButton}>
            {fallbacks === job.clips.length
              ? "Gemini was busy, so these clips were picked from the transcript."
              : `Gemini was busy, so ${fallbacks} of these clips ${fallbacks === 1 ? "was" : "were"} picked from the transcript.`}{" "}
            Re-rank to try Gemini again.
          </Notice>
        )}

        {job.clips.length > 0 ? (
          <div
            className={cn(
              "grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-5",
              active && "opacity-50",
            )}
          >
            {job.clips.map((clip) => (
              <ClipCard
                key={`${clip}-${job.clipsVersion ?? ""}`}
                jobId={job.id}
                clip={clip}
                version={job.clipsVersion ?? job.createdAt}
                detail={details.get(clip)}
              />
            ))}
          </div>
        ) : (
          active && (
            <div className="grid grid-cols-2 gap-x-4 sm:grid-cols-3 lg:grid-cols-5">
              {Array.from({ length: 5 }, (_, i) => (
                <div
                  key={i}
                  aria-hidden
                  className={cn(
                    "bg-muted aspect-[9/16] rounded-lg",
                    i > 1 && "hidden sm:block",
                    i > 2 && "sm:hidden lg:block",
                  )}
                />
              ))}
            </div>
          )
        )}
      </div>
    </div>
  );
}
