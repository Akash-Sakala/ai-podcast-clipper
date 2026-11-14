"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { formatMoney, type Money, stepTime, timecode } from "~/lib/format";
import type { CostStage } from "~/lib/job-types";
import {
  COST_STAGES,
  MODAL_MONTHLY_CREDIT,
  type Period,
  RUNS_PAGE,
  type RunRow,
  type StageBreakdown,
  type Stats,
  TIME_STAGES,
  type TimeStage,
} from "~/lib/stats";
import { cn } from "~/lib/utils";

const ACTIVE_POLL_MS = 5000;
const IDLE_POLL_MS = 30000;

const STAGE_LABELS: Record<TimeStage, string> = {
  download: "Download",
  transcribe: "Transcribe",
  rank: "Rank",
  render: "Render",
  save: "Save",
  overhead: "Overhead",
};

const STAGE_COLORS: Record<TimeStage, string> = {
  download: "bg-stage-download",
  transcribe: "bg-stage-transcribe",
  rank: "bg-stage-rank",
  render: "bg-stage-render",
  save: "bg-stage-save",
  overhead: "stage-hatch",
};

export function StatsPage({ initialStats }: { initialStats: Stats }) {
  const [stats, setStats] = useState(initialStats);
  const [period, setPeriod] = useState<Period>(initialStats.period);

  const refresh = useCallback(async (which: Period) => {
    if (document.visibilityState !== "visible") return;
    try {
      const response = await fetch(`/api/stats?period=${which}`, {
        cache: "no-store",
      });
      if (response.ok) setStats((await response.json()) as Stats);
    } catch {
      // The dev server may be restarting; the next poll tries again.
    }
  }, []);

  useEffect(() => {
    if (period !== stats.period) void refresh(period);
  }, [period, stats.period, refresh]);

  // A finished run (or a fresh Modal bill) shows up within seconds.
  useEffect(() => {
    const timer = setInterval(
      () => void refresh(period),
      stats.active || stats.billRefreshing ? ACTIVE_POLL_MS : IDLE_POLL_MS,
    );
    return () => clearInterval(timer);
  }, [stats.active, stats.billRefreshing, period, refresh]);

  const m = (value: Money | null) =>
    value ? formatMoney(value, stats.currency) : "—";

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="space-y-1.5">
          <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            Stats
          </h1>
          <p className="text-muted-foreground text-sm">
            What each video cost on Modal and how long it took.
          </p>
        </div>
        <Segmented
          label="Period"
          value={period}
          options={[
            ["month", "This month"],
            ["all", "All time"],
          ]}
          onChange={setPeriod}
        />
      </div>

      <div className="grid gap-3 sm:gap-4 lg:grid-cols-3">
        <BillTile stats={stats} />
        <Tile
          label="New video, average"
          value={stats.newAvg ? m(stats.newAvg.cost) : "—"}
          second={stats.newAvg ? stepTime(stats.newAvg.seconds) : undefined}
          note={
            stats.newAvg
              ? `${stats.newAvg.perHour ? `${m(stats.newAvg.perHour)} per podcast hour, ` : ""}${sample(stats.newAvg.runs)}`
              : "No new videos yet"
          }
        />
        <Tile
          label="Re-rank, average"
          value={stats.rerankAvg ? m(stats.rerankAvg.cost) : "—"}
          second={
            stats.rerankAvg ? stepTime(stats.rerankAvg.seconds) : undefined
          }
          note={
            stats.rerankAvg
              ? capitalize(sample(stats.rerankAvg.runs))
              : "No re-ranks yet"
          }
        />
      </div>

      {stats.summary.runs === 0 ? (
        <div className="text-muted-foreground rounded-xl border border-dashed px-6 py-10 text-sm leading-relaxed">
          <p className="text-foreground mb-1 font-medium">
            No runs {stats.period === "month" ? "this month" : "yet"}
          </p>
          Paste a link on{" "}
          <Link href="/" className="underline underline-offset-4">
            Home
          </Link>{" "}
          to process your first video.
        </div>
      ) : (
        <>
          <p className="text-muted-foreground -mt-2 px-0.5 text-sm">
            {summaryLine(stats)}
          </p>
          <StageCard stats={stats} m={m} />
          {stats.trend && <TrendCard points={stats.trend} />}
          <RunsCard stats={stats} m={m} />
          {stats.clipSources && <ClipSourcesCard stats={stats} />}
        </>
      )}
    </div>
  );
}

function sample(runs: number) {
  const text = `from ${runs} ${runs === 1 ? "run" : "runs"}`;
  return runs < 3 ? `early estimate ${text}` : text;
}

function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function summaryLine(stats: Stats) {
  const s = stats.summary;
  const parts = [
    plural(s.videos, "video"),
    plural(s.runs, "run"),
    plural(s.clips, "clip"),
  ];
  if (s.failed) parts.push(`${plural(s.failed, "failed run")}`);
  return `${stats.periodLabel}: ${parts.join(", ")}.`;
}

// --------------------------------------------------------------------------- //
// Building blocks

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (value: T) => void;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="bg-muted inline-flex rounded-lg p-0.5 text-sm"
    >
      {options.map(([option, text]) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={value === option}
          onClick={() => onChange(option)}
          className={cn(
            "rounded-md px-3 py-1 transition-colors",
            value === option
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

function Card({
  title,
  description,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-card rounded-xl border p-4 sm:p-5">
      <div className="mb-4 space-y-1">
        <h2 className="font-medium">{title}</h2>
        {description && (
          <p className="text-muted-foreground text-sm leading-relaxed">
            {description}
          </p>
        )}
      </div>
      {children}
    </section>
  );
}

function Tile({
  label,
  value,
  second,
  note,
}: {
  label: string;
  value: string;
  second?: string;
  note?: string;
}) {
  return (
    <div className="bg-card flex flex-col gap-1.5 rounded-xl border p-4 sm:p-5">
      <span className="text-muted-foreground text-sm">{label}</span>
      <span className="tabular flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-3xl font-semibold tracking-tight">{value}</span>
        {second && <span className="text-xl font-medium">{second}</span>}
      </span>
      {note && (
        <span className="text-muted-foreground text-xs leading-relaxed">
          {note}
        </span>
      )}
    </div>
  );
}

function BillTile({ stats }: { stats: Stats }) {
  const bill = stats.bill;
  const credit0 =
    stats.currency === "inr" && stats.rate
      ? `₹${Math.round(MODAL_MONTHLY_CREDIT * stats.rate.rate).toLocaleString("en-IN")}`
      : `$${MODAL_MONTHLY_CREDIT}`;
  return (
    <div className="bg-card flex flex-col gap-1.5 rounded-xl border p-4 sm:p-5">
      <span className="text-muted-foreground text-sm">
        Modal bill{bill ? `, ${bill.monthLabel}` : ", this month"}
      </span>
      <span className="tabular flex flex-wrap items-baseline gap-x-2">
        <span className="text-3xl font-semibold tracking-tight">
          {bill ? formatMoney(bill.metered, stats.currency) : "—"}
        </span>
        <span className="text-muted-foreground text-sm">
          of {credit0} credit
        </span>
      </span>
      <span className="text-muted-foreground text-xs leading-relaxed">
        {bill
          ? `${formatMoney(bill.billed, stats.currency)} paid. From Modal's bill.`
          : stats.billRefreshing
            ? "Reading Modal's bill…"
            : "Modal's bill could not be read yet."}
      </span>
    </div>
  );
}

function Swatch({ stage }: { stage: TimeStage }) {
  return (
    <span
      aria-hidden
      className={cn("inline-block size-2.5 rounded-[3px]", STAGE_COLORS[stage])}
    />
  );
}

function compactTime(seconds: number) {
  const s = Math.round(seconds);
  return s < 60
    ? `${s}s`
    : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

// --------------------------------------------------------------------------- //
// By stage

function StageBar({
  values,
  max,
  label,
  total,
  format,
}: {
  values: [TimeStage, number][];
  max: number;
  label: string;
  total: string;
  format: (stage: TimeStage, value: number) => string;
}) {
  const sumValues = values.reduce((a, [, v]) => a + v, 0);
  const shown = values.filter(([, v]) => v > 0);
  return (
    <div className="grid grid-cols-[3.75rem_1fr_auto] items-center gap-3">
      <span className="text-muted-foreground text-xs">{label}</span>
      <div className="min-w-0">
        <div
          className="flex h-6 gap-[2px]"
          style={{ width: `${max ? (sumValues / max) * 100 : 0}%` }}
          role="img"
          aria-label={`${label}: ${shown.map(([k, v]) => `${STAGE_LABELS[k]} ${format(k, v)}`).join(", ")}`}
        >
          {shown.map(([stage, value], i) => {
            const share = value / sumValues;
            return (
              <span
                key={stage}
                title={`${STAGE_LABELS[stage]}: ${format(stage, value)}`}
                className={cn(
                  "flex h-full min-w-[3px] items-center justify-center overflow-hidden text-[11px] font-semibold whitespace-nowrap",
                  STAGE_COLORS[stage],
                  stage === "save" || stage === "overhead"
                    ? "text-foreground"
                    : "text-white",
                  i === shown.length - 1 && "rounded-r-[4px]",
                )}
                style={{ width: `${share * 100}%` }}
              >
                {share >= 0.14 ? format(stage, value) : ""}
              </span>
            );
          })}
        </div>
      </div>
      <span className="tabular w-[5.5rem] text-right text-sm font-semibold">
        {total}
      </span>
    </div>
  );
}

function StageCard({
  stats,
  m,
}: {
  stats: Stats;
  m: (v: Money | null) => string;
}) {
  const rows = (["new", "rerank"] as const)
    .map((key) => [key, stats.stages[key]] as const)
    .filter(
      (entry): entry is readonly ["new" | "rerank", StageBreakdown] =>
        entry[1] !== null,
    );
  if (!rows.length) return null;
  const timeMax = Math.max(...rows.map(([, b]) => b.totalSeconds));
  const costRows = rows.filter(([, b]) => b.cost);
  const costMax = Math.max(
    0,
    ...costRows.map(([, b]) => b.totalCost?.usd ?? 0),
  );
  const label = (key: "new" | "rerank") => (key === "new" ? "New" : "Re-rank");
  return (
    <Card title="By stage" description="An average run, start to finish.">
      <div className="grid gap-6 md:grid-cols-2 md:gap-8">
        <div className="space-y-2.5">
          <h3 className="text-sm font-medium">Time</h3>
          {rows.map(([key, b]) => (
            <StageBar
              key={key}
              label={label(key)}
              max={timeMax}
              total={stepTime(b.totalSeconds)}
              values={TIME_STAGES.map((k) => [k, b.time[k]])}
              format={(_, v) => compactTime(v)}
            />
          ))}
        </div>
        <div className="space-y-2.5">
          <h3 className="text-sm font-medium">Cost</h3>
          {costRows.length ? (
            costRows.map(([key, b]) => (
              <StageBar
                key={key}
                label={label(key)}
                max={costMax}
                total={m(b.totalCost)}
                values={COST_STAGES.map((k) => [k, b.cost![k].usd])}
                format={(stage) => m(b.cost![stage as CostStage])}
              />
            ))
          ) : (
            <p className="text-muted-foreground text-sm">
              Stage costs appear after the next run.
            </p>
          )}
        </div>
      </div>
      <div className="text-muted-foreground mt-4 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
        {TIME_STAGES.map((stage) => (
          <span key={stage} className="inline-flex items-center gap-1.5">
            <Swatch stage={stage} />
            {STAGE_LABELS[stage]}
          </span>
        ))}
      </div>
      <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
        Render runs one GPU per clip at the same time: its time is the slowest
        clip, its cost is all of them. Download (with the upload) and Save run
        on your laptop, so they cost nothing. Overhead is mostly Modal
        containers starting.
      </p>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Trend (from 10 new runs)

function TrendCard({
  points,
}: {
  points: { startedAt: string; seconds: number }[];
}) {
  const W = 1000;
  const H = 180;
  const pad = { l: 44, r: 12, t: 12, b: 24 };
  const times = points.map((p) => new Date(p.startedAt).getTime());
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  // Whole-minute gridlines: every minute up to 6 min, else every 2 or 5 minutes.
  const longest = Math.max(...points.map((p) => p.seconds), 60);
  const step = longest <= 360 ? 60 : longest <= 900 ? 120 : 300;
  const yMax = Math.ceil((longest * 1.05) / step) * step;
  const x = (t: number) =>
    pad.l +
    (t1 > t0
      ? ((t - t0) / (t1 - t0)) * (W - pad.l - pad.r)
      : (W - pad.l - pad.r) / 2);
  const y = (s: number) => H - pad.b - (s / yMax) * (H - pad.t - pad.b);
  const ticks = Array.from({ length: yMax / step + 1 }, (_, i) => i * step);
  const date = (t: number) =>
    new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return (
    <Card
      title="Time per new video"
      description="Each dot is one new video, oldest on the left."
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-44 w-full"
        role="img"
        aria-label="Processing time of each new video over time"
      >
        {ticks.map((s) => (
          <g key={s}>
            <line
              x1={pad.l}
              x2={W - pad.r}
              y1={y(s)}
              y2={y(s)}
              className="stroke-border"
              strokeWidth={1}
            />
            <text
              x={pad.l - 6}
              y={y(s) + 4}
              textAnchor="end"
              className="fill-muted-foreground text-[11px]"
            >
              {s ? `${s / 60} min` : "0"}
            </text>
          </g>
        ))}
        <text x={pad.l} y={H - 6} className="fill-muted-foreground text-[11px]">
          {date(t0)}
        </text>
        <text
          x={W - pad.r}
          y={H - 6}
          textAnchor="end"
          className="fill-muted-foreground text-[11px]"
        >
          {date(t1)}
        </text>
        {points.map((p) => (
          <circle
            key={p.startedAt}
            cx={x(new Date(p.startedAt).getTime())}
            cy={y(p.seconds)}
            r={5}
            className="fill-stage-download stroke-card"
            strokeWidth={2}
          >
            <title>{`${date(new Date(p.startedAt).getTime())}: ${stepTime(p.seconds)}`}</title>
          </circle>
        ))}
      </svg>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Runs

function shortDate(iso: string) {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const FAILED_AT: Record<string, string> = {
  downloading: "download",
  uploading: "upload",
  transcribing: "transcription",
  ranking: "ranking",
  rendering: "render",
  saving: "saving",
};

function result(run: RunRow) {
  if (run.status === "failed")
    return run.failedStage && FAILED_AT[run.failedStage]
      ? `Failed at ${FAILED_AT[run.failedStage]}`
      : "Failed";
  if (run.status === "no-clips" || !run.clips) return "No clips";
  return plural(run.clips, "clip");
}

function RunBar({
  run,
  max,
  m,
}: {
  run: RunRow;
  max: number;
  m: (v: Money | null) => string;
}) {
  const shown = TIME_STAGES.filter((k) => run.time[k] > 0);
  const total = shown.reduce((a, k) => a + run.time[k], 0);
  return (
    <span
      className="group relative inline-flex w-24 shrink-0 align-middle sm:w-28"
      tabIndex={0}
    >
      <span
        className="flex h-2 gap-px"
        style={{ width: `${max ? (total / max) * 100 : 0}%` }}
        aria-hidden
      >
        {shown.map((k, i) => (
          <span
            key={k}
            className={cn(
              "h-full",
              STAGE_COLORS[k],
              i === shown.length - 1 && "rounded-r-[3px]",
            )}
            style={{ flexGrow: run.time[k], flexBasis: 0 }}
          />
        ))}
      </span>
      <span
        role="tooltip"
        className="bg-popover text-popover-foreground pointer-events-none absolute bottom-full left-0 z-10 mb-2 hidden rounded-md border px-3 py-2 text-xs shadow-md group-hover:block group-focus-visible:block"
      >
        <table className="tabular">
          <thead>
            <tr className="text-muted-foreground">
              <th className="pr-4 text-left font-normal">Stage</th>
              <th className="pr-4 text-right font-normal">Time</th>
              <th className="text-right font-normal">Cost</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((k) => (
              <tr key={k}>
                <td className="pr-4 whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    <Swatch stage={k} />
                    {STAGE_LABELS[k]}
                  </span>
                </td>
                <td className="pr-4 text-right whitespace-nowrap">
                  {stepTime(run.time[k])}
                </td>
                <td className="text-right whitespace-nowrap">
                  {run.stageCost && k in run.stageCost
                    ? m(run.stageCost[k as CostStage])
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </span>
    </span>
  );
}

function RunsCard({
  stats,
  m,
}: {
  stats: Stats;
  m: (v: Money | null) => string;
}) {
  const [limit, setLimit] = useState(RUNS_PAGE);
  const rows = stats.runs.slice(0, limit);
  const max = Math.max(0, ...stats.runs.map((r) => r.seconds ?? 0));
  const title = (run: RunRow) =>
    run.deleted ? (
      <span
        className="text-muted-foreground block truncate"
        title={`${run.title} (deleted)`}
      >
        {run.title}
      </span>
    ) : (
      <Link
        href={`/videos/${run.jobId}`}
        className="block truncate underline-offset-4 hover:underline"
        title={run.title}
      >
        {run.title}
      </Link>
    );
  return (
    <Card
      title="Runs"
      description="Newest first. Hover a time bar for each stage's time and cost."
    >
      {/* Wide screens: a table */}
      <table className="hidden w-full table-fixed text-sm sm:table">
        <thead>
          <tr className="text-muted-foreground border-b text-left">
            <th className="w-36 py-2 pr-3 font-normal">Date</th>
            <th className="py-2 pr-3 font-normal">Video</th>
            <th className="w-20 py-2 pr-3 font-normal">Run</th>
            <th className="hidden w-20 py-2 pr-3 text-right font-normal md:table-cell">
              Length
            </th>
            <th className="w-32 py-2 pr-3 font-normal">Result</th>
            <th className="w-56 py-2 pr-3 font-normal">Time</th>
            <th className="w-20 py-2 text-right font-normal">Cost</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((run) => (
            <tr key={run.key} className="border-b">
              <td className="py-2.5 pr-3 whitespace-nowrap">
                <time dateTime={run.startedAt} suppressHydrationWarning>
                  {shortDate(run.startedAt)}
                </time>
              </td>
              <td className="max-w-0 py-2.5 pr-3">{title(run)}</td>
              <td className="text-muted-foreground py-2.5 pr-3">
                {run.mode === "new" ? "New" : "Re-rank"}
              </td>
              <td className="tabular hidden py-2.5 pr-3 text-right md:table-cell">
                {run.videoSeconds ? timecode(run.videoSeconds) : "—"}
              </td>
              <td
                className={cn(
                  "py-2.5 pr-3 whitespace-nowrap",
                  run.status === "failed" && "text-destructive",
                )}
              >
                {result(run)}
              </td>
              <td className="tabular py-2.5 pr-3 whitespace-nowrap">
                <span className="inline-flex items-center gap-3">
                  <RunBar run={run} max={max} m={m} />
                  {run.seconds !== null ? stepTime(run.seconds) : "—"}
                </span>
              </td>
              <td className="tabular py-2.5 text-right whitespace-nowrap">
                {m(run.cost)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Phones: one block per run */}
      <ul className="divide-y sm:hidden">
        {rows.map((run) => (
          <li key={run.key} className="space-y-1.5 py-3 first:pt-0">
            <div className="text-sm font-medium">{title(run)}</div>
            <div className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
              <time dateTime={run.startedAt} suppressHydrationWarning>
                {shortDate(run.startedAt)}
              </time>
              <span>{run.mode === "new" ? "New" : "Re-rank"}</span>
              <span
                className={cn(run.status === "failed" && "text-destructive")}
              >
                {result(run)}
              </span>
            </div>
            <div className="tabular flex items-center justify-between gap-3 text-sm">
              <span className="inline-flex items-center gap-3">
                <RunBar run={run} max={max} m={m} />
                {run.seconds !== null ? stepTime(run.seconds) : "—"}
              </span>
              <span>{m(run.cost)}</span>
            </div>
          </li>
        ))}
      </ul>

      {stats.runs.length > limit && (
        <button
          type="button"
          onClick={() => setLimit((l) => l + RUNS_PAGE)}
          className="text-muted-foreground hover:text-foreground mt-3 text-sm underline-offset-4 hover:underline"
        >
          Show {Math.min(RUNS_PAGE, stats.runs.length - limit)} more
        </button>
      )}
      {stats.reconcile && (
        <p className="text-muted-foreground mt-3 text-xs leading-relaxed">
          This month these runs add up to {m(stats.reconcile.runs)} of the{" "}
          {m(stats.reconcile.apps)} Modal charged for running the app.
        </p>
      )}
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Clip sources

function minutes(seconds: number) {
  return `${Math.max(1, Math.round(seconds / 60))} min`;
}

function ClipSourcesCard({ stats }: { stats: Stats }) {
  const c = stats.clipSources!;
  const skipped = c.skippedSeconds;
  const totalSkipped =
    skipped.sponsor + skipped.introsOutros + skipped.previews + skipped.other;
  const parts = [
    skipped.sponsor > 0 && `sponsor reads ${minutes(skipped.sponsor)}`,
    skipped.introsOutros > 0 &&
      `intros and outros ${minutes(skipped.introsOutros)}`,
    skipped.previews > 0 && `previews ${minutes(skipped.previews)}`,
    skipped.other > 0 && `other ${minutes(skipped.other)}`,
  ].filter(Boolean);
  return (
    <Card
      title="Clip sources"
      description="How YouTube viewers' data shaped the clips."
    >
      <div className="space-y-2.5 text-sm leading-relaxed">
        <p>
          <span className="font-medium">Most replayed.</span>{" "}
          {c.graphVideos
            ? `${c.graphVideos} of ${plural(c.youtubeVideos, "YouTube video")} had YouTube's Most replayed graph. ${c.clipsOnHotspot} of their ${c.clipsOnGraphVideos} clips came from a replay peak${c.audienceAvg !== null ? `, with an average audience score of ${Math.round(c.audienceAvg)} (50 is a typical moment)` : ""}.`
            : `None of the ${plural(c.youtubeVideos, "YouTube video")} had YouTube's Most replayed graph yet.`}
        </p>
        <p>
          <span className="font-medium">SponsorBlock.</span>{" "}
          {totalSkipped > 0
            ? `${minutes(totalSkipped)} skipped in ${plural(c.sponsorVideos, "video")}: ${parts.join(", ")}.${c.dropped ? ` That ruled out ${plural(c.dropped, "clip candidate")}.` : ""}`
            : "No sponsor reads, intros or previews were marked in these videos."}
        </p>
      </div>
    </Card>
  );
}
