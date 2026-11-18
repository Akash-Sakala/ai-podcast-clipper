"use client";

import { Layers, Loader2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { plural } from "~/lib/format";
import type { ClipDetail, ClipSet, Job } from "~/lib/job-types";
import { ClipCard } from "../clip-card";
import {
  matchesOf,
  pickKey,
  type ClipPick,
  type useComparisonSelection,
} from "./compare-selection";

const GRID = "grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-5";

type Selection = ReturnType<typeof useComparisonSelection>;

/** "14 Nov" (the day the previous set was made). */
function shortDate(iso: string | null | undefined) {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

/** Version for a clip's cache key: the file time, per set. */
export function clipVersionOf(job: Job, set: ClipSet, clip: string) {
  const key = set === "new" ? `new/${clip}` : clip;
  return (
    job.clipVersions?.[key] ??
    (set === "new"
      ? (job.comparison?.createdAt ?? job.updatedAt)
      : (job.clipsVersion ?? job.createdAt))
  );
}

export function SectionTitle({
  title,
  note,
}: {
  title: string;
  note?: string | null;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
      <h2 className="font-display text-lg font-semibold tracking-tight">
        {title}
      </h2>
      {note && <span className="text-muted-foreground text-sm">{note}</span>}
    </div>
  );
}

function KeepBox({
  checked,
  title,
  onChange,
}: {
  checked: boolean;
  title: string;
  onChange: (keep: boolean) => void;
}) {
  return (
    <label className="-ml-0.5 flex min-h-11 cursor-pointer items-center gap-2 text-sm font-medium select-none sm:min-h-9">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="accent-tally size-4 shrink-0 cursor-pointer"
      />
      <span>
        Keep<span className="sr-only"> {title}</span>
      </span>
    </label>
  );
}

function SoftBadge({ children }: { children: React.ReactNode }) {
  return (
    <span className="bg-muted text-muted-foreground mt-1 inline-flex max-w-full items-start gap-1 rounded-md px-1.5 py-0.5 text-xs leading-4 font-medium">
      <Layers className="mt-0.5 size-3 shrink-0" aria-hidden />
      <span>{children}</span>
    </span>
  );
}

function OutlineButton({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="border-input hover:bg-accent inline-flex h-11 items-center rounded-md border px-3.5 text-sm font-medium whitespace-nowrap transition-colors disabled:pointer-events-none disabled:opacity-50 sm:h-9"
    >
      {children}
    </button>
  );
}

/**
 * After a re-rank of a job that had clips: the new set and the previous set side by side
 * (one section each), a Keep checkbox per clip and two shortcuts. Nothing changes until
 * the primary button is pressed; the runner then keeps the ticked clips, best first.
 */
export function CompareView({
  job,
  selection,
  onStarted,
}: {
  job: Job;
  selection: Selection;
  onStarted: () => Promise<void>;
}) {
  const comparison = job.comparison!;
  const [sending, setSending] = useState(false);
  const newClips = comparison.new.clips;
  const newDetails = useMemo(
    () => new Map(comparison.new.details.map((d) => [d.name, d])),
    [comparison],
  );
  const previousDetails = useMemo(
    () => new Map((job.clipDetails ?? []).map((d) => [d.name, d])),
    [job.clipDetails],
  );
  const matches = useMemo(
    () =>
      matchesOf(
        newClips.map((c) => newDetails.get(c)).filter((d) => d !== undefined),
        job.clips
          .map((c) => previousDetails.get(c))
          .filter((d) => d !== undefined),
      ),
    [newClips, newDetails, job.clips, previousDetails],
  );
  const matchedPrevious = useMemo(
    () => new Set([...matches.values()].map((d) => d.name)),
    [matches],
  );
  const { picked, toggle, pickAll } = selection;
  const picks: ClipPick[] = [
    ...newClips
      .filter((clip) => picked.has(pickKey("new", clip)))
      .map((clip) => ({ set: "new" as const, clip })),
    ...job.clips
      .filter((clip) => picked.has(pickKey("previous", clip)))
      .map((clip) => ({ set: "previous" as const, clip })),
  ];
  const count = picks.length;

  const confirm = async () => {
    setSending(true);
    try {
      const response = await fetch(`/api/jobs/${job.id}/comparison`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ keep: picks }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as {
          error?: string;
        };
        throw new Error(body.error ?? `Request failed (${response.status})`);
      }
      await onStarted();
    } catch (error) {
      toast.error("Couldn't keep the clips", {
        description: (error as Error).message,
      });
    } finally {
      setSending(false);
    }
  };

  const card = (set: ClipSet, clip: string, detail?: ClipDetail) => {
    const keep = picked.has(pickKey(set, clip));
    const title = detail?.title?.trim()
      ? detail.title.trim()
      : `Clip ${detail?.rank ?? ""}`.trim();
    const match = set === "new" ? matches.get(clip) : undefined;
    const note =
      set === "new" ? (
        match ? (
          <SoftBadge>Same moment as previous #{match.rank}</SoftBadge>
        ) : undefined
      ) : matchedPrevious.has(clip) ? (
        <SoftBadge>Also in the new set</SoftBadge>
      ) : undefined;
    return (
      <ClipCard
        key={`${set}-${clip}-${clipVersionOf(job, set, clip)}`}
        jobId={job.id}
        clip={clip}
        set={set === "new" ? "new" : undefined}
        version={clipVersionOf(job, set, clip)}
        detail={detail}
        replayWeight={job.ranking?.replay?.weight}
        dimmed={!keep}
        outlined={keep}
        selection={
          <KeepBox
            checked={keep}
            title={title}
            onChange={(value) => toggle(set, clip, value)}
          />
        }
        note={note}
      />
    );
  };

  const previousAt = shortDate(comparison.previous.at);

  return (
    <div className="space-y-8">
      <section
        aria-label="Choose the clips to keep"
        className="bg-card flex flex-col gap-4 rounded-xl border p-4 sm:p-5 lg:flex-row lg:items-center"
      >
        <div className="min-w-0 flex-1">
          <p className="font-semibold">Choose the clips to keep</p>
          <p className="text-muted-foreground mt-1 text-sm leading-relaxed">
            The re-rank made {plural(newClips.length, "new clip")}. Your{" "}
            {plural(job.clips.length, "previous clip")}{" "}
            {job.clips.length === 1 ? "is" : "are"} still here. Clips you do not
            keep are deleted when you confirm.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <OutlineButton
            onClick={() => pickAll("new", newClips)}
            disabled={sending}
          >
            Keep the new set
          </OutlineButton>
          <OutlineButton
            onClick={() => pickAll("previous", job.clips)}
            disabled={sending}
          >
            Keep the previous set
          </OutlineButton>
          <button
            type="button"
            onClick={() => void confirm()}
            disabled={count === 0 || sending}
            className="bg-tally text-tally-ink hover:bg-tally/90 focus-visible:ring-tally focus-visible:ring-offset-background inline-flex h-11 items-center gap-1.5 rounded-md px-4 text-sm font-semibold whitespace-nowrap shadow-sm transition-colors focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50 sm:h-9"
          >
            {sending && <Loader2 className="size-4 animate-spin" />}
            Keep {plural(count, "clip")}
          </button>
        </div>
      </section>

      <section aria-label="New clips">
        <SectionTitle
          title="New clips"
          note={`${newClips.length} from this re-rank`}
        />
        <div className={GRID}>
          {newClips.map((clip) => card("new", clip, newDetails.get(clip)))}
        </div>
      </section>

      <section aria-label="Previous clips">
        <SectionTitle
          title="Previous clips"
          note={`${job.clips.length}${previousAt ? ` from ${previousAt}` : ""}`}
        />
        <div className={GRID}>
          {job.clips.map((clip) =>
            card("previous", clip, previousDetails.get(clip)),
          )}
        </div>
      </section>
    </div>
  );
}

/**
 * While a re-rank of a job with clips runs: the new clips appear as they are saved
 * (placeholders for the rest), above the current clips.
 */
export function RerankProgressView({ job }: { job: Job }) {
  const live = job.liveClipsSet === "new" ? (job.liveClips ?? []) : [];
  const total = Math.max(job.progress?.total ?? 0, live.length);
  const previousDetails = new Map(
    (job.clipDetails ?? []).map((d) => [d.name, d]),
  );
  return (
    <div className="space-y-8">
      <section aria-label="New clips">
        <SectionTitle
          title="New clips"
          note={
            total
              ? `${live.length} of ${total} saved`
              : "They appear here as they are saved"
          }
        />
        {total > 0 && (
          <div className={GRID}>
            {live.map((detail) => (
              <ClipCard
                key={`new-${detail.name}-${clipVersionOf(job, "new", detail.name)}`}
                jobId={job.id}
                clip={detail.name}
                set="new"
                version={clipVersionOf(job, "new", detail.name)}
                detail={detail}
                replayWeight={job.ranking?.replay?.weight}
              />
            ))}
            {Array.from({ length: total - live.length }, (_, i) => (
              <div
                key={`pending-${i}`}
                aria-hidden
                className="bg-muted aspect-[9/16] rounded-lg"
              />
            ))}
          </div>
        )}
      </section>
      <section aria-label="Previous clips">
        <SectionTitle
          title="Previous clips"
          note={`${job.clips.length} from ${shortDate(job.rerankedAt ?? job.createdAt) ?? "before"}`}
        />
        <div className={GRID}>
          {job.clips.map((clip) => (
            <ClipCard
              key={`${clip}-${clipVersionOf(job, "previous", clip)}`}
              jobId={job.id}
              clip={clip}
              version={clipVersionOf(job, "previous", clip)}
              detail={previousDetails.get(clip)}
              replayWeight={job.ranking?.replay?.weight}
            />
          ))}
        </div>
      </section>
    </div>
  );
}
