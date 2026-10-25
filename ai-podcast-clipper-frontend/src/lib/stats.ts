// The numbers behind the stats page, worked out from each job's run history.
// No Node.js imports: the server computes them, the client only renders.

import { money } from "./format";
import { isActive, type Job, type RunRecord } from "./job-types";

/** Modal's Starter plan includes $30 of compute a month. */
export const MODAL_MONTHLY_CREDIT = 30;

export type StageAverages = {
  endToEnd: number | null;
  transcribe: number | null;
  findMoments: number | null;
  render: number | null;
  /** Full runs behind the end-to-end average. */
  videos: number;
};

export type LengthBucket = {
  key: string;
  label: string;
  current: StageAverages;
  all: StageAverages;
};

export type TimeShareRow = {
  key: string;
  title: string;
  startedAt: string;
  pipeline: RunRecord["pipeline"];
  /** Laptop download and upload (older runs, or the fallback when YouTube blocks Modal). */
  download: number;
  upload: number;
  /** Everything on Modal, the YouTube download included. */
  cloud: number;
  saving: number;
  total: number;
  cloudDownload: number | null;
  transcribe: number | null;
  findMoments: number | null;
  render: number | null;
};

export type VideoStats = {
  id: string;
  title: string;
  createdAt: string;
  videoSeconds: number | null;
  clips: number;
  runs: RunRecord[];
  totalUsd: number;
  firstRunUsd: number;
  rerankUsd: number;
  estimated: boolean;
  /** A run whose cost is unknown (failed attempts, runs before cost tracking). */
  unknownCost: boolean;
  /** End-to-end time of the latest full run that made clips. */
  endToEnd: number | null;
  /** false: only the latest run is known (run `runner.py --backfill-stats`). */
  historyComplete: boolean;
};

export type MonthSpend = {
  key: string;
  label: string;
  usd: number;
  estimatedUsd: number;
};

export type Stats = {
  totals: {
    usd: number;
    estimatedUsd: number;
    videos: number;
    clips: number;
    runs: number;
    reranks: number;
    failed: number;
  };
  /** Full runs on the current (parallel) pipeline. */
  perVideo: {
    usd: number | null;
    perClip: number | null;
    endToEnd: number | null;
    videos: number;
  };
  months: MonthSpend[];
  thisMonth: MonthSpend;
  timeShare: {
    rows: TimeShareRow[];
    laptopShare: number | null;
    cloudShare: number | null;
  };
  buckets: LengthBucket[];
  videos: VideoStats[];
  insights: string[];
  incompleteHistory: number;
  active: boolean;
};

const BUCKETS = [
  { key: "short", label: "Under 30 min", max: 30 * 60 },
  { key: "medium", label: "30–60 min", max: 60 * 60 },
  { key: "long", label: "60–90 min", max: 90 * 60 },
  { key: "longest", label: "Over 90 min", max: Infinity },
];

const mean = (values: number[]) =>
  values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
const num = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/** A job from before the run history: its latest run, from job.json. */
function latestRunOnly(job: Job): RunRecord[] {
  const t = job.timings ?? {};
  const total = num(t.total);
  if (total === null || isActive(job)) return [];
  const timings: RunRecord["timings"] = {};
  for (const key of [
    "download",
    "upload",
    "cloud_download",
    "gpu_transcribe",
    "gpu_select",
    "gpu_render",
    "gpu_total",
    "total",
  ] as const) {
    const value = num(t[key]);
    if (value !== null) timings[key] = value;
  }
  const finishedAt = job.rerankedAt ?? job.updatedAt;
  return [
    {
      mode: t.rerank === true ? "rerank" : "full",
      status:
        job.status === "failed"
          ? "failed"
          : job.clips.length
            ? "done"
            : "no-clips",
      startedAt: new Date(
        new Date(finishedAt).getTime() - total * 1000,
      ).toISOString(),
      finishedAt,
      clips: job.clips.length,
      pipeline: job.cost ? "parallel" : "single-gpu",
      videoSeconds: videoSeconds(job),
      timings,
      cost: job.cost ? { ...job.cost, estimated: false } : null,
    },
  ];
}

function videoSeconds(job: Job) {
  return job.engine?.parakeet?.audioSeconds ?? job.meta?.duration ?? null;
}

const madeClips = (run: RunRecord) =>
  run.status === "done" && (run.clips ?? 0) > 0;

function averages(runs: RunRecord[]): StageAverages {
  const full = runs.filter((r) => r.mode === "full");
  const endToEnd = full
    .filter(madeClips)
    .map((r) => num(r.timings.total))
    .filter((v): v is number => v !== null);
  const pick = (list: RunRecord[], key: keyof RunRecord["timings"]) =>
    list
      .map((r) => num(r.timings[key]))
      .filter((v): v is number => v !== null && v > 0);
  return {
    endToEnd: mean(endToEnd),
    transcribe: mean(pick(full, "gpu_transcribe")),
    findMoments: mean(
      pick(
        runs.filter((r) => r.status !== "failed"),
        "gpu_select",
      ),
    ),
    render: mean(pick(runs.filter(madeClips), "gpu_render")),
    videos: endToEnd.length,
  };
}

function monthKey(iso: string) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(key: string) {
  const [year, month] = key.split("-").map(Number);
  return new Date(year ?? 2000, (month ?? 1) - 1, 1).toLocaleDateString(
    "en-US",
    { month: "long", year: "numeric" },
  );
}

function percent(share: number) {
  return `${Math.round(share * 100)}%`;
}

export function computeStats(jobs: Job[], now = new Date()): Stats {
  const videos: VideoStats[] = jobs.map((job) => {
    const complete = Array.isArray(job.runs);
    const runs = complete ? (job.runs ?? []) : latestRunOnly(job);
    const usd = (list: RunRecord[]) => sum(list.map((r) => r.cost?.usd ?? 0));
    const fullRuns = runs.filter((r) => r.mode === "full");
    const lastFull = [...fullRuns].reverse().find(madeClips);
    return {
      id: job.id,
      title: job.title,
      createdAt: job.createdAt,
      videoSeconds: videoSeconds(job),
      clips: job.clips.length,
      runs,
      totalUsd: usd(runs),
      firstRunUsd: usd(fullRuns),
      rerankUsd: usd(runs.filter((r) => r.mode === "rerank")),
      estimated: runs.some((r) => r.cost?.estimated),
      unknownCost: runs.some((r) => !r.cost),
      endToEnd: lastFull ? num(lastFull.timings.total) : null,
      historyComplete: complete,
    };
  });

  const allRuns = videos.flatMap((v) =>
    v.runs.map((run) => ({
      run: { ...run, videoSeconds: run.videoSeconds ?? v.videoSeconds },
      video: v,
    })),
  );
  const runs = allRuns.map((r) => r.run);
  const current = runs.filter((r) => r.pipeline === "parallel");
  const currentFull = current.filter((r) => r.mode === "full" && madeClips(r));

  // Spend by month (by the day a run finished).
  const byMonth = new Map<string, MonthSpend>();
  for (const run of runs) {
    if (!run.cost) continue;
    const key = monthKey(run.finishedAt);
    const entry = byMonth.get(key) ?? {
      key,
      label: monthLabel(key),
      usd: 0,
      estimatedUsd: 0,
    };
    entry.usd += run.cost.usd;
    if (run.cost.estimated) entry.estimatedUsd += run.cost.usd;
    byMonth.set(key, entry);
  }
  const thisKey = monthKey(now.toISOString());
  const months = [...byMonth.values()].sort((a, b) =>
    b.key.localeCompare(a.key),
  );
  const thisMonth = byMonth.get(thisKey) ?? {
    key: thisKey,
    label: monthLabel(thisKey),
    usd: 0,
    estimatedUsd: 0,
  };

  // Where the time goes: the latest full runs that made clips.
  const timeRows: TimeShareRow[] = allRuns
    .filter(({ run }) => run.mode === "full" && madeClips(run))
    .filter(({ run }) => num(run.timings.total) !== null)
    .sort((a, b) => b.run.startedAt.localeCompare(a.run.startedAt))
    .slice(0, 8)
    .map(({ run, video }) => {
      const t = run.timings;
      const download = t.download ?? 0;
      const upload = t.upload ?? 0;
      const total = t.total ?? 0;
      const cloud = Math.min(
        (t.gpu_total ?? 0) + (t.cloud_download ?? 0),
        total - download - upload,
      );
      return {
        key: `${video.id}-${run.startedAt}`,
        title: video.title,
        startedAt: run.startedAt,
        pipeline: run.pipeline,
        download,
        upload,
        cloud: Math.max(0, cloud),
        saving: Math.max(0, total - download - upload - Math.max(0, cloud)),
        total,
        cloudDownload: num(t.cloud_download),
        transcribe: num(t.gpu_transcribe),
        findMoments: num(t.gpu_select),
        render: num(t.gpu_render),
      };
    });
  const shareRuns = currentFull.filter((r) => num(r.timings.total));
  const totalTime = sum(shareRuns.map((r) => r.timings.total ?? 0));
  const laptopShare = totalTime
    ? sum(
        shareRuns.map(
          (r) => (r.timings.download ?? 0) + (r.timings.upload ?? 0),
        ),
      ) / totalTime
    : null;
  const cloudShare = totalTime
    ? sum(
        shareRuns.map(
          (r) => (r.timings.gpu_total ?? 0) + (r.timings.cloud_download ?? 0),
        ),
      ) / totalTime
    : null;

  const buckets: LengthBucket[] = BUCKETS.map((bucket, index) => {
    const min = index ? (BUCKETS[index - 1]?.max ?? 0) : 0;
    const inBucket = (r: RunRecord) =>
      r.videoSeconds !== null &&
      r.videoSeconds >= min &&
      r.videoSeconds < bucket.max;
    return {
      key: bucket.key,
      label: bucket.label,
      current: averages(current.filter(inBucket)),
      all: averages(runs.filter(inBucket)),
    };
  });

  const fullCosts = currentFull
    .map((r) => r.cost?.usd)
    .filter((v): v is number => typeof v === "number");
  const perVideoUsd = mean(fullCosts);
  const perClip = mean(
    currentFull
      .filter((r) => r.cost && r.clips)
      .map((r) => (r.cost?.usd ?? 0) / (r.clips ?? 1)),
  );
  const rerankCosts = current
    .filter((r) => r.mode === "rerank" && r.cost)
    .map((r) => r.cost?.usd ?? 0);

  const insights: string[] = [];
  if (laptopShare !== null && cloudShare !== null)
    insights.push(
      laptopShare > cloudShare
        ? `Downloading and uploading on your laptop take ${percent(laptopShare)} of a full run, more than the cloud work (${percent(cloudShare)}).`
        : laptopShare < 0.05
          ? `Videos download straight to Modal: the cloud work takes ${percent(cloudShare)} of a full run, and your laptop only saves the clips.`
          : `The cloud work takes ${percent(cloudShare)} of a full run; downloading and uploading on your laptop take ${percent(laptopShare)}.`,
    );
  const rerankAvg = mean(rerankCosts);
  if (rerankAvg !== null && perVideoUsd)
    insights.push(
      rerankAvg / perVideoUsd > 0.8
        ? `A re-rank costs about as much as a full run (${money(rerankAvg)} vs ${money(perVideoUsd)}): rendering, not transcription, is most of the GPU bill.`
        : `A re-rank costs ${money(rerankAvg)}, ${percent(1 - rerankAvg / perVideoUsd)} less than a full run (${money(perVideoUsd)}).`,
    );
  if (perVideoUsd)
    insights.push(
      `At ${money(perVideoUsd)} a video, the $${MODAL_MONTHLY_CREDIT} monthly credit covers about ${Math.floor(MODAL_MONTHLY_CREDIT / perVideoUsd)} videos; ${money(thisMonth.usd)} is used this month.`,
    );
  const speeds = jobs
    .map((j) => j.engine?.parakeet?.realTimeFactor)
    .filter((v): v is number => typeof v === "number");
  const speed = mean(speeds);
  if (speed)
    insights.push(
      `Transcription runs at about ${Math.round(speed)}× real time: an hour of podcast takes about ${Math.max(1, Math.round(3600 / speed))} s.`,
    );

  return {
    totals: {
      usd: sum(videos.map((v) => v.totalUsd)),
      estimatedUsd: sum(
        runs.filter((r) => r.cost?.estimated).map((r) => r.cost?.usd ?? 0),
      ),
      videos: videos.filter((v) => v.runs.length).length,
      clips: sum(videos.map((v) => v.clips)),
      runs: runs.length,
      reranks: runs.filter((r) => r.mode === "rerank").length,
      failed: runs.filter((r) => r.status === "failed").length,
    },
    perVideo: {
      usd: perVideoUsd,
      perClip,
      endToEnd: averages(current).endToEnd,
      videos: currentFull.length,
    },
    months,
    thisMonth,
    timeShare: { rows: timeRows, laptopShare, cloudShare },
    buckets,
    videos: videos.filter((v) => v.runs.length),
    insights,
    incompleteHistory: videos.filter((v) => !v.historyComplete && v.runs.length)
      .length,
    active: jobs.some(isActive),
  };
}
