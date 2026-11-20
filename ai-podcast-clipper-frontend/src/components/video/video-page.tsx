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
import { useMemo, useState } from "react";
import { failureSentence, fallbackCount, displayTitle } from "~/lib/format";
import { isActive, type Job } from "~/lib/job-types";
import type { Currency } from "~/lib/settings-types";
import { usePolling } from "~/lib/use-polling";
import { useStable } from "~/lib/use-stable";
import { cn } from "~/lib/utils";
import { ClipCard } from "../clip-card";
import { Popover } from "../popover";
import { RelativeTime } from "../relative-time";
import { ReplayWave } from "../replay-wave";
import { ActionButton } from "./action-button";
import { useComparisonSelection } from "./compare-selection";
import { CompareView, RerankProgressView } from "./compare-view";
import { DetailsPanel } from "./details-panel";
import { Notice } from "./notice";
import { ProgressStrip } from "./progress-strip";
import { useJobActions } from "./use-job-actions";

const POLL_INTERVAL_MS = 3000;

export function VideoPage({
  initialJob,
  currency = "usd",
  rate = null,
}: {
  initialJob: Job;
  currency?: Currency;
  rate?: number | null;
}) {
  const [job, setJob] = useState(initialJob);
  const active = isActive(job);

  // Polls every 3 s while the job runs. A 304, or a hidden tab, changes nothing.
  const refresh = usePolling<Job>({
    url: `/api/jobs/${job.id}`,
    intervalMs: active ? POLL_INTERVAL_MS : null,
    onData: setJob,
  });

  const { busy, rerank, retry } = useJobActions(refresh);
  const isBusy = busy === job.id;
  // Parts that rarely change keep their identity between polls, so the clip cards
  // and the replay wave (memoised) don't re-render while only the progress moves.
  const clipDetails = useStable(job.clipDetails);
  const replay = useStable(job.replay);
  const details = useMemo(
    () => new Map((clipDetails ?? []).map((d) => [d.name, d])),
    [clipDetails],
  );
  const clipNames = useStable(job.clips);
  const waveClips = useMemo(
    () =>
      clipNames.map((clip) => details.get(clip)).filter((d) => d !== undefined),
    [clipNames, details],
  );
  // Clips of a running first run appear as soon as they are saved. A re-rank of a job with
  // clips shows its new clips as they are saved above the current ones; once it is done,
  // both sets wait side by side until the owner keeps a choice (job.comparison).
  const liveClips = useStable(job.liveClips);
  const live =
    active &&
    job.mode !== "rerank" &&
    job.liveClipsSet !== "new" &&
    !!liveClips?.length;
  const rerankLive = active && job.mode === "rerank" && job.clips.length > 0;
  const comparison = useStable(job.comparison);
  const selection = useComparisonSelection(comparison);
  const comparing = !!comparison && !active;
  const liveTotal = live ? (job.progress?.total ?? 0) : 0;
  const clipVersion = (clip: string) =>
    job.clipVersions?.[clip] ?? job.clipsVersion ?? job.createdAt;
  const fallbacks = fallbackCount(job);
  const done = job.status === "done";

  const rerankButton = !!job.canRerank && !active && !comparing && (
    <ActionButton
      icon={<ListRestart className="size-4" />}
      onClick={() => rerank(job)}
      busy={isBusy}
    >
      Re-rank
    </ActionButton>
  );
  const downloadAll = job.clips.length > 1 && !active && !comparing && (
    <ActionButton
      icon={<Download className="size-4" aria-hidden />}
      href={`/api/media/${job.id}/all`}
      compact
    >
      Download all
    </ActionButton>
  );
  // Re-rank sits in the header only when no notice already offers it.
  const headerRerank =
    done && fallbacks === 0 && job.clips.length > 0 && !comparing;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
      <Link
        href="/"
        className="text-muted-foreground hover:text-foreground press -ml-1.5 inline-flex h-8 items-center gap-1.5 rounded-md px-1.5 text-sm font-medium pointer-coarse:h-11"
      >
        <ArrowLeft className="size-4" aria-hidden />
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
            <RelativeTime iso={job.createdAt} />
          </p>
        </div>
        <div className="-mr-1.5 flex shrink-0 items-center gap-0.5 sm:mr-0 pointer-coarse:gap-1">
          {downloadAll}
          {headerRerank && !!job.canRerank && (
            <ActionButton
              icon={<ListRestart className="size-4" aria-hidden />}
              onClick={() => rerank(job)}
              busy={isBusy}
              compact
            >
              Re-rank
            </ActionButton>
          )}
          <Popover
            label="Details"
            trigger={<Info className="size-4" aria-hidden />}
            align="end"
          >
            {() => <DetailsPanel job={job} currency={currency} rate={rate} />}
          </Popover>
        </div>
      </header>

      <div className="space-y-6">
        {active && (
          <div className="bg-card rounded-xl border p-4 sm:p-5">
            <ProgressStrip job={job} />
          </div>
        )}

        {comparing && job.error && (
          <Notice tone="error">
            Couldn&apos;t keep the chosen clips, so nothing was changed. If a
            clip is open in a player, close it and try again.
          </Notice>
        )}

        {job.status === "failed" && !comparing && (
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

        {done && !comparing && job.clips.length === 0 && (
          <Notice action={rerankButton}>
            No clips came out of this video. Re-rank to ask Gemini again without
            transcribing a second time.
          </Notice>
        )}

        {done && !comparing && fallbacks > 0 && (
          <Notice action={rerankButton}>
            {fallbacks === job.clips.length
              ? "Gemini was busy, so these clips were picked without it."
              : `Gemini was busy, so ${fallbacks} of these clips ${fallbacks === 1 ? "was" : "were"} picked without it.`}{" "}
            Re-rank to try Gemini again.
          </Notice>
        )}

        {replay?.available && job.clips.length > 0 && (
          <ReplayWave replay={replay} clips={waveClips} dimmed={active} />
        )}

        {comparing ? (
          <CompareView job={job} selection={selection} onStarted={refresh} />
        ) : rerankLive ? (
          <RerankProgressView job={job} />
        ) : live ? (
          <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-5">
            {liveClips?.map((detail) => (
              <ClipCard
                key={`${detail.name}-${clipVersion(detail.name)}`}
                jobId={job.id}
                clip={detail.name}
                version={clipVersion(detail.name)}
                detail={detail}
                replayWeight={job.ranking?.replay?.weight}
              />
            ))}
            {Array.from(
              { length: Math.max(0, liveTotal - (liveClips?.length ?? 0)) },
              (_, i) => (
                <div
                  key={`pending-${i}`}
                  aria-hidden
                  className="bg-muted aspect-[9/16] rounded-lg"
                />
              ),
            )}
          </div>
        ) : job.clips.length > 0 ? (
          <div
            className={cn(
              "grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-5",
              active && "opacity-50",
            )}
          >
            {job.clips.map((clip) => (
              <ClipCard
                key={`${clip}-${clipVersion(clip)}`}
                jobId={job.id}
                clip={clip}
                version={clipVersion(clip)}
                detail={details.get(clip)}
                replayWeight={job.ranking?.replay?.weight}
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
