// Shapes and constants of the stats page. No Node.js imports: the server fills them
// in (see stats.ts), the client only renders them.

import type { Money } from "./format";
import type { CostStage, UsdInr } from "./job-types";
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
  /** The newest runs, as many as were asked for (see computeStats `runsLimit`). */
  runs: RunRow[];
  /** All runs of the period, of which `runs` shows the newest ones. */
  runsTotal: number;
  /** Longest run of the period (the scale of the time bars). */
  runMaxSeconds: number;
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
