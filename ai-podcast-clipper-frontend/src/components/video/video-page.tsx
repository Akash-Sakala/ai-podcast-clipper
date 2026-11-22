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
import { AnimatePresence, m } from "motion/react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  currentStage,
  displayTitle,
  failedStepIndex,
  failureSentence,
  fallbackCount,
} from "~/lib/format";
import { jobsChanged, useJob } from "~/lib/job-sync";
import { isActive, type Job } from "~/lib/job-types";
import { duration, ease } from "~/lib/motion";
import type { Currency } from "~/lib/settings-types";
import { useStable } from "~/lib/use-stable";
import { cn } from "~/lib/utils";
import { ClipCard } from "../clip-card";
import { Popover } from "../popover";
import { RelativeTime } from "../relative-time";
import { ReplayWave } from "../replay-wave";
import { ActionButton } from "./action-button";
import { useComparisonSelection } from "./compare-selection";
import { ClipViewerProvider, type ViewerClip } from "./clip-viewer";
import { clipVersionOf, CompareView, RerankProgressView } from "./compare-view";
import { DetailsPanel } from "./details-panel";
import { LiveClips } from "./live-clips";
import { Notice } from "./notice";
import { FailedRail, ProgressStrip } from "./progress-strip";
import { readyText, RunFinished } from "./run-finished";
import { useJobActions } from "./use-job-actions";

export function VideoPage({
  initialJob,
  servedAt,
  currency = "usd",
  rate = null,
}: {
  initialJob: Job;
  /** Server time the job was read at (see lib/job-sync.ts). */
  servedAt: number;
  currency?: Currency;
  rate?: number | null;
}) {
  // The tab's live copy of this job (lib/job-sync.ts): read again when the page opens
  // (also with Back), every 3 s while it runs, and as soon as its row in the list
  // changes; what this page reads also updates the home list.
  const job = useJob(initialJob, servedAt);
  const active = isActive(job);

  // A first run that ends while the page is open: the strip turns into "5 clips ready
  // in 6 min 12 s", with a toast that says the same.
  const [finishedRun, setFinishedRun] = useState<{
    clips: number;
    seconds: number | null;
  } | null>(null);
  const wasActive = useRef(active);
  useEffect(() => {
    const before = wasActive.current;
    wasActive.current = active;
    if (!before || active) return;
    if (job.status !== "done" || job.mode === "rerank" || !job.clips.length)
      return;
    const total = job.timings?.total;
    setFinishedRun({
      clips: job.clips.length,
      seconds: typeof total === "number" ? total : null,
    });
    toast.success(readyText(job.clips.length), { id: `finished-${job.id}` });
  }, [active, job]);

  const { busy, rerank, retry } = useJobActions();
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
  const plannedClips = useStable(job.plannedClips);
  const live =
    active &&
    job.mode !== "rerank" &&
    job.liveClipsSet !== "new" &&
    (!!liveClips?.length || !!plannedClips?.length);
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

  // What the clip viewer moves through: the clips on the page ("main": saved so far
  // during a run, else the live set, which is the previous set in a comparison) and a
  // re-rank's new set ("new").
  const viewerLists = useMemo(() => {
    const main: ViewerClip[] = live
      ? (liveClips ?? []).map((d) => ({
          name: d.name,
          detail: d,
          version:
            job.clipVersions?.[d.name] ?? job.clipsVersion ?? job.createdAt,
        }))
      : clipNames.map((name) => ({
          name,
          detail: details.get(name),
          version:
            job.clipVersions?.[name] ?? job.clipsVersion ?? job.createdAt,
        }));
    const newSet: ViewerClip[] = comparison
      ? comparison.new.clips.map((name) => ({
          name,
          detail: comparison.new.details.find((d) => d.name === name),
          version: clipVersionOf(job, "new", name),
        }))
      : rerankLive && job.liveClipsSet === "new"
        ? (liveClips ?? []).map((d) => ({
            name: d.name,
            detail: d,
            version: clipVersionOf(job, "new", d.name),
          }))
        : [];
    return { main, new: newSet };
  }, [live, liveClips, clipNames, details, comparison, rerankLive, job]);

  return (
    <ClipViewerProvider
      jobId={job.id}
      lists={viewerLists}
      replayWeight={job.ranking?.replay?.weight}
    >
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
          <AnimatePresence initial={false} mode="wait">
            {active ? (
              <m.div
                key="running"
                exit={{
                  opacity: 0,
                  transition: { duration: duration.exit, ease: ease.exit },
                }}
                className="bg-card rounded-xl border p-4 sm:p-5"
              >
                <ProgressStrip job={job} />
              </m.div>
            ) : finishedRun ? (
              <RunFinished
                key="finished"
                clips={finishedRun.clips}
                seconds={finishedRun.seconds}
              />
            ) : null}
          </AnimatePresence>

          {comparing && job.error && (
            <Notice tone="error">
              Couldn&apos;t keep the chosen clips, so nothing was changed. If a
              clip is open in a player, close it and try again.
            </Notice>
          )}

          {job.status === "failed" &&
            !comparing &&
            failedStepIndex(job) >= 0 && (
              <div className="bg-card rounded-xl border p-4 sm:p-5">
                <FailedRail job={job} />
              </div>
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
              No clips came out of this video. Re-rank to ask Gemini again
              without transcribing a second time.
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
            <CompareView
              job={job}
              selection={selection}
              onStarted={() => jobsChanged(job.id)}
            />
          ) : rerankLive ? (
            <RerankProgressView job={job} />
          ) : live ? (
            <LiveClips
              jobId={job.id}
              saved={liveClips ?? []}
              planned={plannedClips ?? null}
              total={liveTotal}
              stage={currentStage(job)}
              versionOf={clipVersion}
              replayWeight={job.ranking?.replay?.weight}
            />
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
                      "bg-muted shimmer aspect-[9/16] rounded-lg",
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
    </ClipViewerProvider>
  );
}
