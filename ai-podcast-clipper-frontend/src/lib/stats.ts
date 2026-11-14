// The numbers behind the stats page, worked out from the run ledger
// (data/stats/runs.jsonl, see lib/ledger.ts). Job folders only add their current
// title; a deleted video keeps its runs.
// No Node.js imports: the server computes them, the client only renders.

import type { Money } from "./format";
import {
  isActive,
  type CostStage,
  type Job,
  type LedgerRun,
  type UsdInr,
} from "./job-types";
import type { Currency } from "./settings-types";

/** Modal's Starter plan includes $30 of compute a month. */
export const MODAL_MONTHLY_CREDIT = 30;
/** Runs listed before "Show more". */
export const RUNS_PAGE = 20;
/** New runs needed before the time trend appears. */
export const TREND_MIN_RUNS = 10;

export type Period = "month" | "all";
export type TimeStage =
  | "download"
  | "transcribe"
  | "rank"
  | "render"
  | "save"
  | "overhead";
export const TIME_STAGES: TimeStage[] = [
  "download",
  "transcribe",
  "rank",
  "render",
  "save",
  "overhead",
];
export const COST_STAGES: CostStage[] = ["transcribe", "rank", "render"];

export type StageBreakdown = {
  /** Average seconds per stage; they add up to the end-to-end time. */
  time: Record<TimeStage, number>;
  totalSeconds: number;
  /** Average cost per stage (null when no run reported its stages). */
  cost: Record<CostStage, Money> | null;
  totalCost: Money | null;
  runs: number;
};

export type RunRow = {
  key: string;
  jobId: string;
  title: string;
  deleted: boolean;
  startedAt: string;
  mode: "new" | "rerank";
  videoSeconds: number | null;
  status: "done" | "no-clips" | "failed";
  failedStage: string | null;
  clips: number | null;
  seconds: number | null;
  time: Record<TimeStage, number>;
  stageCost: Record<CostStage, Money> | null;
  cost: Money | null;
};

export type ClipSourceStats = {
  youtubeVideos: number;
  graphVideos: number;
  clipsOnGraphVideos: number;
  clipsOnHotspot: number;
  audienceAvg: number | null;
  sponsorVideos: number;
  skippedSeconds: {
    sponsor: number;
    introsOutros: number;
    previews: number;
    other: number;
  };
  dropped: number;
};

export type Stats = {
  period: Period;
  currency: Currency;
  rate: UsdInr | null;
  /** "November 2026" or "All time". */
  periodLabel: string;
  bill: {
    monthLabel: string;
    metered: Money;
    billed: Money;
    apps: Money;
    fetchedAt: string;
  } | null;
  billRefreshing: boolean;
  summary: { videos: number; runs: number; clips: number; failed: number };
  newAvg: {
    cost: Money | null;
    seconds: number;
    perHour: Money | null;
    runs: number;
  } | null;
  rerankAvg: { cost: Money | null; seconds: number; runs: number } | null;
  stages: { new: StageBreakdown | null; rerank: StageBreakdown | null };
  runs: RunRow[];
  /** This month's runs against Modal's "deployed apps" charge (month view only). */
  reconcile: { runs: Money; apps: Money } | null;
  clipSources: ClipSourceStats | null;
  trend: { startedAt: string; seconds: number }[] | null;
  active: boolean;
};

export type BillInput = {
  month: string;
  meteredUsd: number;
  billedUsd: number;
  breakdown: Record<string, number>;
  fetchedAt: string;
} | null;

const num = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
const mean = (values: number[]) =>
  values.length ? sum(values) / values.length : null;

function addMoney(values: (Money | null)[]): Money | null {
  const present = values.filter((v): v is Money => v !== null);
  if (!present.length) return null;
  const inr = present.every((v) => v.inr !== null)
    ? sum(present.map((v) => v.inr ?? 0))
    : null;
  return { usd: sum(present.map((v) => v.usd)), inr };
}

function scaleMoney(value: Money | null, factor: number): Money | null {
  return (
    value && {
      usd: value.usd * factor,
      inr: value.inr === null ? null : value.inr * factor,
    }
  );
}

const madeClips = (run: LedgerRun) =>
  run.status === "done" && (run.clips ?? 0) > 0;

/** Seconds per stage; Overhead is whatever the stages don't explain (container starts). */
export function stageTimes(run: LedgerRun): Record<TimeStage, number> {
  const t = run.timings;
  const time: Record<TimeStage, number> = {
    download: (t.download ?? 0) + (t.upload ?? 0) + (t.replay ?? 0),
    transcribe: t.gpu_transcribe ?? 0,
    rank: t.gpu_select ?? 0,
    render: t.gpu_render ?? 0,
    save: t.save ?? 0,
    overhead: 0,
  };
  const total = num(t.total);
  if (total !== null)
    time.overhead = Math.max(0, total - sum(TIME_STAGES.map((k) => time[k])));
  return time;
}

/** A USD amount of this run, with its rupee value at the run's own rate (or today's). */
function runMoney(usd: number, run: LedgerRun, today: UsdInr | null): Money {
  const rate = num(run.usdInr?.rate) ?? num(today?.rate);
  return { usd, inr: rate === null ? null : usd * rate };
}

function breakdown(
  runs: LedgerRun[],
  today: UsdInr | null,
): StageBreakdown | null {
  if (!runs.length) return null;
  const times = runs.map(stageTimes);
  const time = Object.fromEntries(
    TIME_STAGES.map((k) => [k, mean(times.map((t) => t[k])) ?? 0]),
  ) as Record<TimeStage, number>;
  const withStages = runs.filter((r) => r.cost?.stages);
  const cost = withStages.length
    ? (Object.fromEntries(
        COST_STAGES.map((k) => [
          k,
          scaleMoney(
            addMoney(
              withStages.map((r) =>
                runMoney(r.cost?.stages?.[k] ?? 0, r, today),
              ),
            ),
            1 / withStages.length,
          )!,
        ]),
      ) as Record<CostStage, Money>)
    : null;
  const costs = runs
    .filter((r) => r.cost)
    .map((r) => runMoney(r.cost!.usd, r, today));
  return {
    time,
    totalSeconds: sum(TIME_STAGES.map((k) => time[k])),
    cost,
    totalCost: scaleMoney(addMoney(costs), costs.length ? 1 / costs.length : 0),
    runs: runs.length,
  };
}

function monthKey(iso: string) {
  return iso.slice(0, 7); // UTC month, like Modal's billing cycles
}

function monthLabel(key: string) {
  const [year, month] = key.split("-").map(Number);
  return new Date(
    Date.UTC(year ?? 2000, (month ?? 1) - 1, 1),
  ).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

function clipSourceStats(runs: LedgerRun[]): ClipSourceStats | null {
  // The latest run that made clips stands for each video.
  const latest = new Map<string, LedgerRun>();
  for (const run of runs) if (madeClips(run)) latest.set(run.jobId, run);
  const youtube = [...latest.values()].filter((r) => r.source === "youtube");
  const withSources = youtube.filter((r) => r.clipSources);
  if (!withSources.length) return null;
  const graph = withSources.filter((r) => r.clipSources!.graph);
  const audiences = graph
    .map((r) => r.clipSources!.audienceAvg)
    .filter((v): v is number => typeof v === "number");
  const skipped = { sponsor: 0, introsOutros: 0, previews: 0, other: 0 };
  let sponsorVideos = 0;
  let dropped = 0;
  for (const run of withSources) {
    const s = run.clipSources!.skippedSeconds ?? {};
    if (Object.values(s).some((v) => v > 0)) sponsorVideos += 1;
    for (const [category, seconds] of Object.entries(s)) {
      if (
        category === "sponsor" ||
        category === "selfpromo" ||
        category === "interaction"
      )
        skipped.sponsor += seconds;
      else if (category === "intro" || category === "outro")
        skipped.introsOutros += seconds;
      else if (category === "preview") skipped.previews += seconds;
      else skipped.other += seconds;
    }
    dropped += run.clipSources!.droppedForSponsor ?? 0;
  }
  return {
    youtubeVideos: youtube.length,
    graphVideos: graph.length,
    clipsOnGraphVideos: sum(graph.map((r) => r.clipSources!.clips)),
    clipsOnHotspot: sum(graph.map((r) => r.clipSources!.clipsOnHotspot)),
    audienceAvg: mean(audiences),
    sponsorVideos,
    skippedSeconds: skipped,
    dropped,
  };
}

export function computeStats(
  ledger: LedgerRun[],
  jobs: Job[],
  options: {
    period?: Period;
    currency?: Currency;
    rate?: UsdInr | null;
    bill?: BillInput;
    billRefreshing?: boolean;
    now?: Date;
  } = {},
): Stats {
  const now = options.now ?? new Date();
  const period = options.period ?? "month";
  const today = options.rate ?? null;
  const thisMonth = monthKey(now.toISOString());
  const jobsById = new Map(jobs.map((job) => [job.id, job]));
  const runs = ledger
    .filter((r) => period === "all" || monthKey(r.startedAt) === thisMonth)
    .sort((a, b) => a.startedAt.localeCompare(b.startedAt));

  const done = (mode: LedgerRun["mode"]) =>
    runs.filter((r) => r.mode === mode && madeClips(r));
  const newDone = done("full");
  const rerankDone = done("rerank");

  const avgCost = (list: LedgerRun[]) => {
    const costs = list
      .filter((r) => r.cost)
      .map((r) => runMoney(r.cost!.usd, r, today));
    return scaleMoney(addMoney(costs), costs.length ? 1 / costs.length : 0);
  };
  const avgSeconds = (list: LedgerRun[]) =>
    mean(
      list
        .map((r) => num(r.timings.total))
        .filter((v): v is number => v !== null),
    ) ?? 0;

  const perHourRuns = newDone.filter((r) => r.cost && num(r.videoSeconds));
  const podcastHours = sum(
    perHourRuns.map((r) => (r.videoSeconds ?? 0) / 3600),
  );
  const perHour = podcastHours
    ? scaleMoney(
        addMoney(perHourRuns.map((r) => runMoney(r.cost!.usd, r, today))),
        1 / podcastHours,
      )
    : null;

  const rows: RunRow[] = [...runs].reverse().map((run) => {
    const job = jobsById.get(run.jobId);
    const stagesUsd = run.cost?.stages;
    return {
      key: `${run.jobId}-${run.startedAt}`,
      jobId: run.jobId,
      title: job?.title ?? run.title ?? "Deleted video",
      deleted: !job,
      startedAt: run.startedAt,
      mode: run.mode === "rerank" ? "rerank" : "new",
      videoSeconds: num(run.videoSeconds),
      status: run.status,
      failedStage: run.failedStage ?? null,
      clips: run.clips,
      seconds: num(run.timings.total),
      time: stageTimes(run),
      stageCost: stagesUsd
        ? (Object.fromEntries(
            COST_STAGES.map((k) => [
              k,
              runMoney(stagesUsd[k] ?? 0, run, today),
            ]),
          ) as Record<CostStage, Money>)
        : null,
      cost: run.cost ? runMoney(run.cost.usd, run, today) : null,
    };
  });

  const bill =
    options.bill && options.bill.month === thisMonth ? options.bill : null;
  const todayMoney = (usd: number): Money => ({
    usd,
    inr: num(today?.rate) === null ? null : usd * today!.rate,
  });
  const monthRuns = ledger.filter(
    (r) => monthKey(r.startedAt) === thisMonth && r.cost,
  );
  const appsUsd = bill ? (bill.breakdown.deployed_apps ?? null) : null;

  const newCount = newDone.length;
  return {
    period,
    currency: options.currency ?? "usd",
    rate: today,
    periodLabel: period === "month" ? monthLabel(thisMonth) : "All time",
    bill: bill && {
      monthLabel: monthLabel(bill.month).split(" ")[0]!,
      metered: todayMoney(bill.meteredUsd),
      billed: todayMoney(bill.billedUsd),
      apps: todayMoney(appsUsd ?? bill.meteredUsd),
      fetchedAt: bill.fetchedAt,
    },
    billRefreshing: options.billRefreshing ?? false,
    summary: {
      videos: new Set(runs.map((r) => r.jobId)).size,
      runs: runs.length,
      clips: sum(runs.filter(madeClips).map((r) => r.clips ?? 0)),
      failed: runs.filter((r) => r.status === "failed").length,
    },
    newAvg: newCount
      ? {
          cost: avgCost(newDone),
          seconds: avgSeconds(newDone),
          perHour,
          runs: newCount,
        }
      : null,
    rerankAvg: rerankDone.length
      ? {
          cost: avgCost(rerankDone),
          seconds: avgSeconds(rerankDone),
          runs: rerankDone.length,
        }
      : null,
    stages: {
      new: breakdown(newDone, today),
      rerank: breakdown(rerankDone, today),
    },
    runs: rows,
    reconcile:
      period === "month" && appsUsd !== null && monthRuns.length
        ? {
            runs: addMoney(
              monthRuns.map((r) => runMoney(r.cost!.usd, r, today)),
            )!,
            apps: todayMoney(appsUsd),
          }
        : null,
    clipSources: clipSourceStats(runs),
    trend:
      newCount >= TREND_MIN_RUNS
        ? newDone
            .filter((r) => num(r.timings.total) !== null)
            .map((r) => ({ startedAt: r.startedAt, seconds: r.timings.total! }))
        : null,
    active: jobs.some(isActive),
  };
}
