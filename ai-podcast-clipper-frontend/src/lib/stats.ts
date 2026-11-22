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
import { clipSourceStats } from "./clip-source-stats";
import {
  addMoney,
  geminiUsd,
  madeClips,
  mean,
  num,
  runTotalUsd,
  scaleMoney,
  sum,
} from "./stats-math";
import {
  COST_STAGES,
  RUNS_PAGE,
  TIME_STAGES,
  TREND_MIN_RUNS,
  type BillInput,
  type GeminiStats,
  type Period,
  type RankSplit,
  type RunRow,
  type StageBreakdown,
  type Stats,
  type TimeStage,
} from "./stats-types";

/**
 * Seconds of a run spent getting media to Modal. A two-phase run (it has "downloadWait")
 * downloads the YouTube video while the cloud transcribes and ranks, so only the wait
 * for the download after that counts, plus the audio, the cutting and the clip upload.
 */
function transferSeconds(t: LedgerRun["timings"]) {
  const before = (t.replay ?? 0) + (t.upload ?? 0);
  if (typeof t.downloadWait !== "number") return before + (t.download ?? 0);
  return (
    before +
    (t.audio ?? 0) +
    t.downloadWait +
    (t.cut ?? 0) +
    (t.clipUpload ?? 0)
  );
}

/** Seconds per stage; Overhead is whatever the stages don't explain (container starts). */
export function stageTimes(run: LedgerRun): Record<TimeStage, number> {
  const t = run.timings;
  const time: Record<TimeStage, number> = {
    download: transferSeconds(t),
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

/** USD per cost stage of a run: Modal's stages, with the Gemini calls added to Rank. */
function stageUsd(run: LedgerRun): Record<CostStage, number> | null {
  const stages = run.cost?.stages;
  if (!stages) return null;
  return {
    transcribe: stages.transcribe ?? 0,
    rank: (stages.rank ?? 0) + geminiUsd(run.cost),
    render: stages.render ?? 0,
  };
}

/** Rank's cost of these runs split into Gemini and Modal, scaled by `factor`
 *  (null when none of them recorded Gemini tokens). */
function rankSplit(
  runs: LedgerRun[],
  today: UsdInr | null,
  factor = 1,
): RankSplit | null {
  if (!runs.some((r) => r.cost?.gemini)) return null;
  const part = (usd: (r: LedgerRun) => number) =>
    scaleMoney(addMoney(runs.map((r) => runMoney(usd(r), r, today))), factor)!;
  return {
    gemini: part((r) => geminiUsd(r.cost)),
    modal: part((r) => r.cost?.stages?.rank ?? 0),
  };
}

/** The period's Gemini calls (runs of any status that recorded token counts). */
function geminiStats(
  runs: LedgerRun[],
  today: UsdInr | null,
): GeminiStats | null {
  const withTokens = runs.filter((r) => r.cost?.gemini);
  if (!withTokens.length) return null;
  const all = withTokens.map((r) => r.cost!.gemini!);
  return {
    spend: addMoney(
      withTokens.map((r) => runMoney(geminiUsd(r.cost), r, today)),
    ),
    calls: sum(all.map((g) => g.calls ?? 0)),
    tokensIn: sum(all.map((g) => g.tokens?.input ?? 0)),
    tokensOut: sum(
      all.map((g) => (g.tokens?.output ?? 0) + (g.tokens?.thinking ?? 0)),
    ),
    runs: withTokens.length,
    unpriced: [...new Set(all.flatMap((g) => g.unpriced ?? []))].sort(),
  };
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
              withStages.map((r) => runMoney(stageUsd(r)![k], r, today)),
            ),
            1 / withStages.length,
          )!,
        ]),
      ) as Record<CostStage, Money>)
    : null;
  const costs = runs
    .filter((r) => r.cost)
    .map((r) => runMoney(runTotalUsd(r.cost!), r, today));
  return {
    time,
    totalSeconds: sum(TIME_STAGES.map((k) => time[k])),
    cost,
    rankSplit: withStages.length
      ? rankSplit(withStages, today, 1 / withStages.length)
      : null,
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

export function computeStats(
  ledger: LedgerRun[],
  jobs: Pick<Job, "id" | "title" | "status">[],
  options: {
    period?: Period;
    currency?: Currency;
    rate?: UsdInr | null;
    bill?: BillInput;
    billRefreshing?: boolean;
    now?: Date;
    /** How many of the newest runs to list (default RUNS_PAGE). */
    runsLimit?: number;
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
      .map((r) => runMoney(runTotalUsd(r.cost!), r, today));
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
        addMoney(
          perHourRuns.map((r) => runMoney(runTotalUsd(r.cost!), r, today)),
        ),
        1 / podcastHours,
      )
    : null;

  const runsLimit = Math.max(1, options.runsLimit ?? RUNS_PAGE);
  const rows: RunRow[] = [...runs]
    .reverse()
    .slice(0, runsLimit)
    .map((run) => {
      const job = jobsById.get(run.jobId);
      const stagesUsd = stageUsd(run);
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
              COST_STAGES.map((k) => [k, runMoney(stagesUsd[k], run, today)]),
            ) as Record<CostStage, Money>)
          : null,
        rankSplit: stagesUsd ? rankSplit([run], today) : null,
        cost: run.cost ? runMoney(runTotalUsd(run.cost), run, today) : null,
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
    gemini: geminiStats(runs, today),
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
    runsTotal: runs.length,
    runMaxSeconds: Math.max(0, ...runs.map((r) => num(r.timings.total) ?? 0)),
    // Modal's part of the runs only (cost.usd), to compare with Modal's own charge.
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
