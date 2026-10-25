"use client";

import { ChevronDown } from "lucide-react";
import Link from "next/link";
import { Fragment, useCallback, useEffect, useState } from "react";
import { money, stepTime, timecode } from "~/lib/format";
import type { RunRecord } from "~/lib/job-types";
import {
  MODAL_MONTHLY_CREDIT,
  type StageAverages,
  type Stats,
  type TimeShareRow,
  type VideoStats,
} from "~/lib/stats";
import { cn } from "~/lib/utils";

const ACTIVE_POLL_MS = 5000;
const IDLE_POLL_MS = 30000;

export function StatsPage({ initialStats }: { initialStats: Stats }) {
  const [stats, setStats] = useState(initialStats);

  const refresh = useCallback(async () => {
    if (document.visibilityState !== "visible") return;
    try {
      const response = await fetch("/api/stats", { cache: "no-store" });
      if (response.ok) setStats((await response.json()) as Stats);
    } catch {
      // The dev server may be restarting; the next poll tries again.
    }
  }, []);

  // A finished run shows up within seconds while a video is processing.
  useEffect(() => {
    const timer = setInterval(
      () => void refresh(),
      stats.active ? ACTIVE_POLL_MS : IDLE_POLL_MS,
    );
    return () => clearInterval(timer);
  }, [stats.active, refresh]);

  return (
    <div className="mx-auto w-full max-w-6xl space-y-8 px-4 py-8 sm:px-6 sm:py-12">
      <div className="space-y-1.5">
        <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
          Stats
        </h1>
        <p className="text-muted-foreground text-sm">
          What each video cost on Modal and how long it took. Updates when a run
          finishes.
        </p>
      </div>

      {stats.totals.runs === 0 ? (
        <div className="text-muted-foreground rounded-xl border border-dashed px-6 py-10 text-sm leading-relaxed">
          <p className="text-foreground mb-1 font-medium">No runs yet</p>
          Costs and times appear here once your first video is processed.
        </div>
      ) : (
        <>
          {stats.incompleteHistory > 0 && (
            <p className="bg-tally-soft rounded-lg px-3.5 py-3 text-sm leading-relaxed">
              {stats.incompleteHistory === 1
                ? "1 older video shows only its latest run."
                : `${stats.incompleteHistory} older videos show only their latest run.`}{" "}
              To add their earlier runs, run{" "}
              <code className="font-mono text-[13px]">
                python runner.py --backfill-stats
              </code>{" "}
              once in the backend folder.
            </p>
          )}
          <KpiTiles stats={stats} />
          <div className="grid gap-4 lg:grid-cols-5">
            <MonthCard stats={stats} />
            <InsightsCard insights={stats.insights} />
          </div>
          <TimeShareCard stats={stats} />
          <LengthTable stats={stats} />
          <VideosTable videos={stats.videos} />
        </>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //

function Card({
  title,
  description,
  action,
  className,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("bg-card rounded-xl border p-4 sm:p-5", className)}>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 className="font-medium">{title}</h2>
          {description && (
            <p className="text-muted-foreground text-sm leading-relaxed">
              {description}
            </p>
          )}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function Tile({
  label,
  value,
  note,
  hero,
}: {
  label: string;
  value: string;
  note?: string;
  hero?: boolean;
}) {
  return (
    <div className="bg-card flex flex-col gap-1 rounded-xl border p-4 sm:p-5">
      <span className="text-muted-foreground text-sm">{label}</span>
      <span
        className={cn(
          "font-semibold tracking-tight",
          hero ? "text-4xl sm:text-5xl" : "text-2xl sm:text-3xl",
        )}
      >
        {value}
      </span>
      {note && (
        <span className="text-muted-foreground text-xs leading-relaxed">
          {note}
        </span>
      )}
    </div>
  );
}

function KpiTiles({ stats }: { stats: Stats }) {
  const { totals, perVideo } = stats;
  const runs = totals.runs === 1 ? "1 run" : `${totals.runs} runs`;
  return (
    <div className="grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      <Tile
        hero
        label="Spent on Modal"
        value={`$${totals.usd.toFixed(2)}`}
        note={
          totals.estimatedUsd > 0
            ? `${runs}, $${totals.estimatedUsd.toFixed(2)} of it estimated`
            : runs
        }
      />
      <Tile
        label="Videos"
        value={String(totals.videos)}
        note={`${totals.clips} clips, ${totals.reranks === 1 ? "1 re-rank" : `${totals.reranks} re-ranks`}`}
      />
      <Tile
        label="End to end"
        value={perVideo.endToEnd ? stepTime(perVideo.endToEnd) : "—"}
        note={
          perVideo.videos
            ? `Link to clips, average of ${perVideo.videos} ${perVideo.videos === 1 ? "video" : "videos"}`
            : undefined
        }
      />
      <Tile
        label="Cost per video"
        value={perVideo.usd !== null ? money(perVideo.usd) : "—"}
        note={
          perVideo.perClip !== null
            ? `${money(perVideo.perClip)} per clip`
            : undefined
        }
      />
    </div>
  );
}

function MonthCard({ stats }: { stats: Stats }) {
  const month = stats.thisMonth;
  const share = Math.min(1, month.usd / MODAL_MONTHLY_CREDIT);
  const earlier = stats.months.filter((m) => m.key !== month.key);
  return (
    <Card
      title="Monthly credit"
      description={`Modal's Starter plan includes $${MODAL_MONTHLY_CREDIT} of compute a month.`}
      className="lg:col-span-2"
    >
      <div className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm">{month.label}</span>
          <span className="text-sm">
            <span className="text-2xl font-semibold tracking-tight">
              ${month.usd.toFixed(2)}
            </span>
            <span className="text-muted-foreground">
              {" "}
              of ${MODAL_MONTHLY_CREDIT}
            </span>
          </span>
        </div>
        <div
          className="bg-tally-soft h-2 overflow-hidden rounded-full"
          role="meter"
          aria-label={`${month.label}: $${month.usd.toFixed(2)} of $${MODAL_MONTHLY_CREDIT}`}
          aria-valuemin={0}
          aria-valuemax={MODAL_MONTHLY_CREDIT}
          aria-valuenow={Number(month.usd.toFixed(2))}
        >
          <span
            className="bg-tally block h-full rounded-r-[4px]"
            style={{
              width: `${Math.max(month.usd > 0 ? 1 : 0, share * 100)}%`,
            }}
          />
        </div>
        <p className="text-muted-foreground text-xs leading-relaxed">
          {Math.round(share * 1000) / 10}% used.
          {month.estimatedUsd > 0 &&
            ` Includes $${month.estimatedUsd.toFixed(2)} estimated for runs from before cost tracking.`}
        </p>
        {earlier.length > 0 && (
          <dl className="divide-y border-t pt-1 text-sm">
            {earlier.map((m) => (
              <div key={m.key} className="flex justify-between py-1.5">
                <dt className="text-muted-foreground">{m.label}</dt>
                <dd className="tabular">${m.usd.toFixed(2)}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </Card>
  );
}

function InsightsCard({ insights }: { insights: string[] }) {
  if (!insights.length) return null;
  return (
    <Card title="At a glance" className="lg:col-span-3">
      <ul className="space-y-3 text-sm leading-relaxed">
        {insights.map((text) => (
          <li key={text} className="flex gap-3">
            <span
              aria-hidden
              className="bg-tally mt-2 size-1.5 shrink-0 rounded-full"
            />
            {text}
          </li>
        ))}
      </ul>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Where the time goes

const SEGMENTS = [
  {
    key: "download",
    label: "Download",
    where: "Laptop",
    color: "bg-viz-download",
  },
  { key: "upload", label: "Upload", where: "Laptop", color: "bg-viz-upload" },
  { key: "cloud", label: "Cloud work", where: "Modal", color: "bg-viz-cloud" },
  {
    key: "saving",
    label: "Saving clips",
    where: "Laptop",
    color: "bg-viz-saving",
  },
] as const;

function Swatch({ className }: { className: string }) {
  return (
    <span
      aria-hidden
      className={cn("inline-block size-2.5 rounded-[3px]", className)}
    />
  );
}

function SegmentTooltip({
  row,
  segment,
}: {
  row: TimeShareRow;
  segment: (typeof SEGMENTS)[number];
}) {
  const seconds = row[segment.key];
  const detail =
    segment.key === "cloud"
      ? [
          row.transcribe !== null && `Transcribe ${stepTime(row.transcribe)}`,
          row.findMoments !== null &&
            `Find moments ${stepTime(row.findMoments)}`,
          row.render !== null && `Render ${stepTime(row.render)}`,
        ].filter(Boolean)
      : [];
  return (
    <span
      role="tooltip"
      className="bg-popover text-popover-foreground pointer-events-none absolute bottom-full left-1/2 z-10 mb-2 hidden -translate-x-1/2 rounded-md border px-2.5 py-1.5 text-xs whitespace-nowrap shadow-md group-hover:block group-focus-visible:block"
    >
      <span className="block font-semibold">
        {stepTime(seconds)}{" "}
        <span className="text-muted-foreground font-normal">
          {Math.round((seconds / Math.max(1, row.total)) * 100)}%
        </span>
      </span>
      <span className="text-muted-foreground block">
        {segment.label} ({segment.where.toLowerCase()})
      </span>
      {detail.map((line) => (
        <span key={String(line)} className="text-muted-foreground block">
          {line}
        </span>
      ))}
    </span>
  );
}

function TimeShareCard({ stats }: { stats: Stats }) {
  const { rows, laptopShare, cloudShare } = stats.timeShare;
  if (!rows.length) return null;
  const longest = Math.max(...rows.map((r) => r.total), 1);
  return (
    <Card
      title="Where the time goes"
      description={
        laptopShare !== null && cloudShare !== null
          ? `On the current pipeline, your laptop's download and upload take ${Math.round(laptopShare * 100)}% of a full run and Modal's work ${Math.round(cloudShare * 100)}%.`
          : "Each full run, step by step."
      }
    >
      <div className="text-muted-foreground mb-4 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
        {SEGMENTS.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <Swatch className={s.color} />
            {s.label}
            <span className="opacity-70">({s.where.toLowerCase()})</span>
          </span>
        ))}
      </div>
      <ul className="space-y-3">
        {rows.map((row) => (
          <li
            key={row.key}
            className="grid grid-cols-1 gap-1 sm:grid-cols-[minmax(0,14rem)_1fr] sm:items-center sm:gap-4"
          >
            <div className="min-w-0">
              <p className="truncate text-sm" title={row.title}>
                {row.title}
              </p>
              <p className="text-muted-foreground text-xs">
                <time dateTime={row.startedAt} suppressHydrationWarning>
                  {shortDate(row.startedAt)}
                </time>
                {row.pipeline === "single-gpu" && ", one GPU for all clips"}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <div
                className="flex h-4 min-w-0 gap-[2px]"
                style={{ width: `${(row.total / longest) * 100}%` }}
              >
                {SEGMENTS.filter((s) => row[s.key] > 0).map((s, i, shown) => (
                  <span
                    key={s.key}
                    tabIndex={0}
                    aria-label={`${s.label}: ${stepTime(row[s.key])}`}
                    className={cn(
                      "group relative h-full min-w-[3px] outline-offset-2 after:absolute after:inset-0 after:rounded-[inherit] after:transition-colors hover:after:bg-white/25 focus-visible:after:bg-white/25",
                      s.color,
                      i === shown.length - 1 && "rounded-r-[4px]",
                    )}
                    style={{ flexGrow: row[s.key], flexBasis: 0 }}
                  >
                    <SegmentTooltip row={row} segment={s} />
                  </span>
                ))}
              </div>
              <span className="tabular text-muted-foreground shrink-0 text-xs">
                {stepTime(row.total)}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Averages by podcast length

function LengthTable({ stats }: { stats: Stats }) {
  const [scope, setScope] = useState<"current" | "all">("current");
  const cell = (value: number | null) =>
    value === null ? "—" : stepTime(value);
  const columns: { key: keyof StageAverages; label: string }[] = [
    { key: "endToEnd", label: "End to end" },
    { key: "transcribe", label: "Transcribe" },
    { key: "findMoments", label: "Find moments" },
    { key: "render", label: "Render" },
  ];
  return (
    <Card
      title="Average time by podcast length"
      description={
        scope === "current"
          ? "Runs on the current pipeline, where every clip renders on its own GPU. Find moments and Render include re-ranks."
          : "Every run, including the first versions that rendered clips one after another on one GPU."
      }
      action={
        <div
          role="radiogroup"
          aria-label="Runs included"
          className="bg-muted inline-flex rounded-lg p-0.5 text-sm"
        >
          {(
            [
              ["current", "Current pipeline"],
              ["all", "All runs"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={scope === value}
              onClick={() => setScope(value)}
              className={cn(
                "rounded-md px-3 py-1 transition-colors",
                scope === value
                  ? "bg-card text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
      }
    >
      <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
        <table className="w-full min-w-[34rem] text-sm">
          <thead>
            <tr className="text-muted-foreground border-b text-left">
              <th className="py-2 pr-4 font-normal">Podcast length</th>
              <th className="py-2 pr-4 text-right font-normal">Videos</th>
              {columns.map((c) => (
                <th key={c.key} className="py-2 pl-4 text-right font-normal">
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {stats.buckets.map((bucket) => {
              const values = bucket[scope];
              const empty = !values.videos && values.findMoments === null;
              return (
                <tr
                  key={bucket.key}
                  className={cn(empty && "text-muted-foreground")}
                >
                  <td className="py-2.5 pr-4">{bucket.label}</td>
                  <td className="tabular py-2.5 pr-4 text-right">
                    {values.videos || "—"}
                  </td>
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={cn(
                        "tabular py-2.5 pl-4 text-right",
                        c.key === "endToEnd" && !empty && "font-medium",
                      )}
                    >
                      {cell(values[c.key])}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Videos

function shortDate(iso: string) {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function CostBar({ video, max }: { video: VideoStats; max: number }) {
  const width = max ? (video.totalUsd / max) * 100 : 0;
  return (
    <div
      aria-hidden
      className="hidden h-2.5 min-w-0 flex-1 gap-[2px] sm:flex"
      title={`First run ${money(video.firstRunUsd)}, re-ranks ${money(video.rerankUsd)}`}
    >
      <div className="flex h-full gap-[2px]" style={{ width: `${width}%` }}>
        {video.firstRunUsd > 0 && (
          <span
            className={cn(
              "bg-viz-cloud h-full",
              !video.rerankUsd && "rounded-r-[4px]",
            )}
            style={{ flexGrow: video.firstRunUsd, flexBasis: 0 }}
          />
        )}
        {video.rerankUsd > 0 && (
          <span
            className="bg-viz-download h-full rounded-r-[4px]"
            style={{ flexGrow: video.rerankUsd, flexBasis: 0 }}
          />
        )}
      </div>
    </div>
  );
}

function Estimated() {
  return (
    <abbr
      title="Estimated from the logged GPU time (runs from before cost tracking)"
      className="text-muted-foreground ml-1 text-xs no-underline"
    >
      est.
    </abbr>
  );
}

function runLabel(run: RunRecord) {
  return run.mode === "rerank" ? "Re-rank" : "First run";
}

function resultLabel(run: RunRecord) {
  if (run.status === "failed") return "Failed";
  if (run.status === "no-clips" || !run.clips) return "No clips";
  return run.clips === 1 ? "1 clip" : `${run.clips} clips`;
}

function RunsDetail({ video }: { video: VideoStats }) {
  const t = (run: RunRecord, key: keyof RunRecord["timings"]) => {
    const value = run.timings[key];
    return typeof value === "number" && value > 0 ? stepTime(value) : "—";
  };
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[46rem] text-xs">
        <thead>
          <tr className="text-muted-foreground text-left">
            <th className="py-1.5 pr-3 font-normal">Started</th>
            <th className="py-1.5 pr-3 font-normal">Run</th>
            <th className="py-1.5 pr-3 font-normal">Result</th>
            {[
              "Download",
              "Upload",
              "Transcribe",
              "Find moments",
              "Render",
              "Total",
              "Cost",
            ].map((label) => (
              <th key={label} className="py-1.5 pl-3 text-right font-normal">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {video.runs.map((run) => (
            <tr key={run.startedAt} className="border-t">
              <td className="py-1.5 pr-3 whitespace-nowrap">
                <time dateTime={run.startedAt} suppressHydrationWarning>
                  {shortDate(run.startedAt)}
                </time>
              </td>
              <td className="py-1.5 pr-3 whitespace-nowrap">
                {runLabel(run)}
                {run.pipeline === "single-gpu" && (
                  <span className="text-muted-foreground">, one GPU</span>
                )}
              </td>
              <td
                className={cn(
                  "py-1.5 pr-3 whitespace-nowrap",
                  run.status === "failed" && "text-destructive",
                )}
              >
                {resultLabel(run)}
              </td>
              <td className="tabular py-1.5 pl-3 text-right">
                {t(run, "download")}
              </td>
              <td className="tabular py-1.5 pl-3 text-right">
                {t(run, "upload")}
              </td>
              <td className="tabular py-1.5 pl-3 text-right">
                {t(run, "gpu_transcribe")}
              </td>
              <td className="tabular py-1.5 pl-3 text-right">
                {t(run, "gpu_select")}
              </td>
              <td className="tabular py-1.5 pl-3 text-right">
                {t(run, "gpu_render")}
              </td>
              <td className="tabular py-1.5 pl-3 text-right font-medium">
                {t(run, "total")}
              </td>
              <td className="tabular py-1.5 pl-3 text-right whitespace-nowrap">
                {run.cost ? (
                  <>
                    {money(run.cost.usd)}
                    {run.cost.estimated && <Estimated />}
                  </>
                ) : (
                  <span
                    className="text-muted-foreground"
                    title="Unknown: the run ended before reporting its cost"
                  >
                    —
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function VideosTable({ videos }: { videos: VideoStats[] }) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const max = Math.max(...videos.map((v) => v.totalUsd), 0);
  const toggle = (id: string) =>
    setOpen((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <Card
      title="Cost and time per video"
      description="Each video's first run plus its re-ranks. Open a video to see every run step by step."
    >
      <div className="text-muted-foreground mb-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
        <span className="inline-flex items-center gap-1.5">
          <Swatch className="bg-viz-cloud" />
          First run
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Swatch className="bg-viz-download" />
          Re-ranks
        </span>
        <span>
          <span className="text-foreground">est.</span> estimated from GPU time
        </span>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-muted-foreground border-b text-left">
            <th className="py-2 pr-3 font-normal">Video</th>
            <th className="hidden w-20 py-2 pr-3 text-right font-normal sm:table-cell">
              Length
            </th>
            <th className="hidden w-14 py-2 pr-3 text-right font-normal md:table-cell">
              Runs
            </th>
            <th className="hidden w-24 py-2 pr-3 text-right font-normal sm:table-cell">
              End to end
            </th>
            <th className="w-28 py-2 pl-3 text-right font-normal sm:w-[30%]">
              Cost
            </th>
            <th className="w-9 py-2" aria-label="Details" />
          </tr>
        </thead>
        <tbody>
          {videos.map((video) => {
            const expanded = open.has(video.id);
            const detailId = `runs-${video.id}`;
            return (
              <Fragment key={video.id}>
                <tr className={cn("border-b", expanded && "border-b-0")}>
                  <td className="max-w-0 py-2.5 pr-3">
                    <Link
                      href={`/videos/${video.id}`}
                      className="block truncate underline-offset-4 hover:underline"
                      title={video.title}
                    >
                      {video.title}
                    </Link>
                    <span className="text-muted-foreground text-xs">
                      <time dateTime={video.createdAt} suppressHydrationWarning>
                        {shortDate(video.createdAt)}
                      </time>
                      {!video.historyComplete && ", latest run only"}
                    </span>
                  </td>
                  <td className="tabular hidden py-2.5 pr-3 text-right sm:table-cell">
                    {video.videoSeconds ? timecode(video.videoSeconds) : "—"}
                  </td>
                  <td className="tabular hidden py-2.5 pr-3 text-right md:table-cell">
                    {video.runs.length}
                  </td>
                  <td className="tabular hidden py-2.5 pr-3 text-right sm:table-cell">
                    {video.endToEnd ? stepTime(video.endToEnd) : "—"}
                  </td>
                  <td className="py-2.5 pl-3">
                    <div className="flex items-center gap-3">
                      <CostBar video={video} max={max} />
                      <span className="tabular w-[4.5rem] shrink-0 text-right whitespace-nowrap">
                        {video.totalUsd > 0 ? money(video.totalUsd) : "—"}
                        {video.estimated && <Estimated />}
                      </span>
                    </div>
                  </td>
                  <td className="py-2.5 pl-1 text-right">
                    <button
                      type="button"
                      onClick={() => toggle(video.id)}
                      aria-expanded={expanded}
                      aria-controls={detailId}
                      aria-label={`${expanded ? "Hide" : "Show"} runs of ${video.title}`}
                      className="text-muted-foreground hover:text-foreground hover:bg-accent inline-flex size-8 items-center justify-center rounded-md transition-colors"
                    >
                      <ChevronDown
                        className={cn(
                          "size-4 transition-transform",
                          expanded && "rotate-180",
                        )}
                        aria-hidden
                      />
                    </button>
                  </td>
                </tr>
                {expanded && (
                  <tr id={detailId} className="border-b">
                    <td
                      colSpan={6}
                      className="bg-muted/40 rounded-md px-3 pb-3"
                    >
                      <RunsDetail video={video} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
