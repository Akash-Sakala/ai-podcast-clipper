"use client";

import { AlertCircle, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  currentStage,
  downloadShare,
  duration,
  money,
  renderShare,
  stageSentence,
  stepsFor,
  timecode,
} from "~/lib/format";
import { fingerprintFile } from "~/lib/fingerprint";
import { isActive, type Job } from "~/lib/job-types";
import { pickVideoFile, uploadVideo } from "~/lib/upload";
import { cn } from "~/lib/utils";
import { toast } from "sonner";

export function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export function ActionButton({
  icon,
  children,
  onClick,
  href,
  busy,
  primary,
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
  onClick?: () => void;
  href?: string;
  busy?: boolean;
  primary?: boolean;
}) {
  const className = cn(
    "inline-flex h-8 items-center gap-1.5 rounded-md whitespace-nowrap px-2.5 text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-60",
    primary
      ? "bg-foreground text-background hover:bg-foreground/85"
      : "text-muted-foreground hover:text-foreground hover:bg-accent",
  );
  const content = (
    <>
      {busy ? <Loader2 className="size-4 animate-spin" /> : icon}
      {children}
    </>
  );
  if (href) {
    return (
      <a href={href} className={className}>
        {content}
      </a>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={className}
    >
      {content}
    </button>
  );
}

export function ProgressStrip({ job, now }: { job: Job; now: number }) {
  const steps = stepsFor(job);
  const stage = currentStage(job);
  const index = steps.findIndex((s) => s.stage === stage);
  const p = job.progress;
  const startedAt = job.queuedAt ?? job.createdAt;
  const since = Math.max(0, (now - new Date(startedAt).getTime()) / 1000);
  const eta = p?.etaSeconds;
  const share = stage === "downloading" ? downloadShare(job) : renderShare(job);

  return (
    <div className="space-y-3">
      <ol className="flex gap-1.5" aria-label="Progress">
        {steps.map((step, i) => {
          const state = i < index ? "done" : i === index ? "current" : "todo";
          const fill =
            state === "current" && share != null
              ? Math.max(8, share * 100)
              : null;
          return (
            <li key={step.stage} className="min-w-0 flex-1">
              <div className="bg-muted relative h-1.5 overflow-hidden rounded-full">
                {state === "done" && (
                  <span className="bg-tally absolute inset-0" />
                )}
                {state === "current" && (
                  <span
                    className={cn(
                      "bg-tally absolute inset-y-0 left-0 transition-[width] duration-500",
                      fill == null && "tally-pulse w-full",
                    )}
                    style={fill != null ? { width: `${fill}%` } : undefined}
                  />
                )}
              </div>
              <span
                className={cn(
                  "mt-1.5 block truncate text-xs",
                  state === "current"
                    ? "text-foreground font-medium"
                    : "text-muted-foreground hidden sm:block",
                )}
                aria-current={state === "current" ? "step" : undefined}
              >
                {step.label}
              </span>
            </li>
          );
        })}
      </ol>
      <div className="flex items-baseline justify-between gap-4 text-sm">
        <p aria-live="polite" className="font-medium">
          {stageSentence(job)}
        </p>
        <p
          className="text-muted-foreground tabular shrink-0"
          suppressHydrationWarning // a live clock: server and browser are a second apart
        >
          {eta != null && eta > 0
            ? `About ${duration(eta)} left`
            : timecode(since)}
        </p>
      </div>
    </div>
  );
}

export function DetailsPanel({ job }: { job: Job }) {
  const rows: { label: string; value: React.ReactNode }[] = [];
  rows.push({
    label: "Added",
    value: new Date(job.createdAt).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    }),
  });
  rows.push({
    label: "Source",
    value: job.youtubeUrl ? (
      <a
        href={job.youtubeUrl}
        target="_blank"
        rel="noreferrer"
        className="underline-offset-4 hover:underline"
      >
        Open on YouTube
      </a>
    ) : (
      "Uploaded video"
    ),
  });
  const audio = job.engine?.parakeet?.audioSeconds;
  if (audio) rows.push({ label: "Video length", value: duration(audio) });
  const speed = job.engine?.parakeet?.realTimeFactor;
  rows.push({
    label: "Transcription",
    value: `Parakeet${job.engine?.gpu ? ` on ${job.engine.gpu}` : ""}${speed ? `, ${Math.round(speed)}× real time` : ""}`,
  });
  const transfer = job.videoTransfer;
  if (job.source === "youtube" && transfer && !isActive(job))
    rows.push({
      label: "Downloaded",
      value:
        transfer === "cloud-download"
          ? "On Modal"
          : transfer === "laptop-download"
            ? job.downloadFallback === "blocked"
              ? "On your laptop (YouTube blocked Modal)"
              : "On your laptop"
            : "Earlier, on your laptop",
    });
  const model = job.ranking?.modelUsed ?? job.ranking?.model;
  if (model) rows.push({ label: "Moments picked by", value: model });
  const total = job.timings?.total;
  if (typeof total === "number" && !isActive(job))
    rows.push({ label: "Processing time", value: duration(total) });
  if (job.cost && !isActive(job))
    rows.push({
      label: "Estimated cost",
      value: `${money(job.cost.usd)}${job.cost.gpu ? ` on ${job.cost.gpu}` : ""}`,
    });
  if (job.reranks)
    rows.push({
      label: "Re-ranked",
      value: job.reranks === 1 ? "Once" : `${job.reranks} times`,
    });

  return (
    <dl className="grid w-80 grid-cols-[auto_1fr] gap-x-4 gap-y-2 p-2 text-sm">
      {rows.map((row) => (
        <div key={row.label} className="contents">
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="min-w-0 text-right break-words">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Notice({
  tone = "neutral",
  children,
  action,
}: {
  tone?: "neutral" | "error";
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-lg px-3.5 py-3 text-sm sm:flex-row sm:items-center",
        tone === "error"
          ? "bg-destructive/10 text-foreground"
          : "bg-tally-soft text-foreground",
      )}
    >
      <AlertCircle
        className={cn(
          "hidden size-4 shrink-0 sm:block",
          tone === "error"
            ? "text-destructive"
            : "text-tally-ink dark:text-tally",
        )}
      />
      <p className="flex-1 leading-relaxed">{children}</p>
      {action && <div className="flex shrink-0 gap-1">{action}</div>}
    </div>
  );
}

/** A YouTube job keeps its link as the title until the download names it. */
export function displayTitle(job: Job) {
  return job.title.replace(/^https?:\/\/(www\.)?/, "");
}

export function fallbackCount(job: Job) {
  return (job.clipDetails ?? []).filter((d) => d.source === "fallback").length;
}

/** Re-rank and Try again, with toasts. `onStarted` refreshes the caller's data. */
export function useJobActions(onStarted: () => Promise<void>) {
  const [busy, setBusy] = useState<string | null>(null);

  const run = useCallback(
    async (job: Job, action: "rerank" | "retry") => {
      // An upload's video is deleted after its run: re-rank asks for the file again.
      let file: File | null = null;
      if (action === "rerank" && job.rerankNeedsFile) {
        file = await pickVideoFile();
        if (!file) return;
        const print = await fingerprintFile(file).catch(() => null);
        if (job.fingerprint && print && print !== job.fingerprint) {
          toast.error("That's a different video", {
            description: "Choose the MP4 these clips were made from.",
          });
          return;
        }
      }
      setBusy(job.id);
      const toastId = file
        ? toast.loading("Uploading the video", { description: "0%" })
        : undefined;
      try {
        if (file) {
          await uploadVideo(`/api/jobs/${job.id}/rerank`, file, (percent) =>
            toast.loading("Uploading the video", {
              id: toastId,
              description: `${percent}%`,
            }),
          );
        } else {
          const response = await fetch(`/api/jobs/${job.id}/${action}`, {
            method: "POST",
          });
          if (!response.ok) {
            const body = (await response.json().catch(() => ({}))) as {
              error?: string;
            };
            throw new Error(
              body.error ?? `Request failed (${response.status})`,
            );
          }
        }
        toast.success(action === "rerank" ? "Re-ranking" : "Trying again", {
          id: toastId,
          description:
            action === "rerank"
              ? "Reusing the transcript. The current clips stay until the new ones are ready."
              : displayTitle(job),
        });
        await onStarted();
      } catch (error) {
        toast.error(
          action === "rerank" ? "Couldn't re-rank" : "Couldn't try again",
          { id: toastId, description: (error as Error).message },
        );
      } finally {
        setBusy(null);
      }
    },
    [onStarted],
  );

  return {
    busy,
    rerank: (job: Job) => void run(job, "rerank"),
    retry: (job: Job) => void run(job, "retry"),
  };
}
