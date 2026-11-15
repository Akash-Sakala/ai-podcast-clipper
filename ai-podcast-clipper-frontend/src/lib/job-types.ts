// Shared between server code and client components (no Node.js imports here).

export type JobStatus =
  | "queued"
  | "downloading"
  | "uploading"
  | "processing" // GPU: transcribe (Parakeet), rank (Gemini), render
  | "transcribing" // legacy (removed Whistle engine)
  | "selecting" // legacy
  | "rendering" // legacy
  | "saving"
  | "done"
  | "failed";

export type ProgressStage =
  | "audio" // two-phase runs: the audio goes to Modal first
  | "downloading"
  | "uploading"
  | "transcribing"
  | "ranking"
  | "clips" // two-phase runs: each chosen clip's footage goes to Modal
  | "rendering"
  | "saving";

export type JobProgress = {
  stage: ProgressStage;
  stageStartedAt?: string;
  done?: number;
  total?: number;
  /** Share done (0-1): of the download, or of all clips (they render at the same time). */
  fraction?: number;
  etaSeconds?: number | null;
  /** While uploading: no data has moved for two minutes (the stuck pieces are retried). */
  stalled?: boolean | null;
  /** Two-phase runs: share of the YouTube video downloaded while the cloud transcribes and ranks. */
  download?: number | null;
};

/**
 * How the current or last run got the video to Modal: downloaded on the laptop and
 * uploaded (YouTube), or uploaded from the job folder; "audio-first": the audio went up
 * first and only each chosen clip's footage followed (two phases).
 */
export type VideoTransfer = "laptop-download" | "upload" | "audio-first";

/** Estimated Modal cost of the last run (see backend costs.py). */
export type JobCost = {
  usd: number;
  gpuSeconds?: number | null;
  gpu?: string | null;
  containers?: number | null;
  /** USD per Modal stage (the job's own CPU container counts as rank). */
  stages?: Partial<Record<CostStage, number>> | null;
  /** "modal": priced with Modal's live rates; "built-in": costs.py's table. */
  rates?: "modal" | "built-in";
};

export type CostStage = "transcribe" | "rank" | "render";

/** 1 USD in INR on the day a run finished (backend fx.py). */
export type UsdInr = {
  rate: number;
  date?: string | null;
  source?: string;
  stale?: boolean;
};

/** Most replayed and SponsorBlock numbers of one run (backend runner.clip_sources). */
export type ClipSources = {
  graph: boolean;
  hotspots: number;
  clips: number;
  clipsOnHotspot: number;
  audienceAvg: number | null;
  sponsorBlock: boolean;
  /** Seconds left out per SponsorBlock category. */
  skippedSeconds: Record<string, number>;
  droppedForSponsor: number;
};

/** One run of a job (first run, re-rank or failed attempt), kept for the stats page. */
export type RunRecord = {
  mode: "full" | "rerank";
  status: "done" | "no-clips" | "failed";
  startedAt: string;
  finishedAt: string;
  clips: number | null;
  videoSeconds: number | null;
  /** Seconds per step; a step that did not run is missing. */
  timings: Partial<
    Record<
      | "download"
      | "upload"
      | "replay"
      | "audio"
      | "downloadWait"
      | "cut"
      | "clipUpload"
      | "save"
      | "gpu_transcribe"
      | "gpu_select"
      | "gpu_render"
      | "gpu_total"
      | "total",
      number
    >
  >;
  cost: JobCost | null;
  /** Where a failed run stopped (a progress stage such as "rendering"). */
  failedStage?: string;
  usdInr?: UsdInr | null;
  clipSources?: ClipSources;
  /** Short hash of the ranking prompts the run used. */
  promptVersion?: string | null;
  maxClips?: number;
};

/**
 * One line of the stats ledger (data/stats/runs.jsonl, written by runner.py): a run
 * plus the job it belongs to. Lines stay when the job folder is deleted.
 */
export type LedgerRun = RunRecord & {
  jobId: string;
  title: string | null;
  source: "youtube" | "upload" | null;
  youtubeUrl?: string | null;
  /** Parakeet's speed for this run (x real time), when it transcribed. */
  realTimeFactor?: number | null;
};

export type ClipDetail = {
  name: string;
  rank: number;
  score: number | null;
  title: string | null;
  hook: string | null;
  reason: string | null;
  start: number | null;
  end: number | null;
  scores?: Record<string, number> | null;
  /**
   * "fallback" = picked from the transcript because Gemini returned too few moments;
   * "replay" = cut around a replay hotspot that Gemini could not score.
   */
  source?: "gemini" | "fallback" | "replay";
  /** Gemini's rubric score (0-100) before the audience score was blended in. */
  gemini?: number | null;
  /** How much YouTube viewers replay this part: 50 = the video's median, 100 = its peak. */
  audience?: number | null;
  /** The replay hotspot (or SponsorBlock highlight) the clip contains. */
  peak?: ClipPeak | null;
};

export type ClipPeak = {
  id: string;
  rank: number;
  kind: "replay" | "highlight";
  center: number;
  value?: number | null;
};

/** One replay hotspot found in the Most replayed graph (backend replay.py). */
export type ReplayPeak = {
  start: number;
  end: number;
  center: number;
  value: number;
  lift: number;
  rank: number;
};

export type SponsorSegment = {
  start: number;
  end: number;
  category: string;
};

/** job.json "replay": YouTube's Most replayed graph and SponsorBlock, read by the runner. */
export type JobReplay = {
  fetchedAt?: string;
  available: boolean;
  /** [start, end, intensity 0-1] for each of the (usually 100) segments. */
  heatmap: [number, number, number][];
  peaks: ReplayPeak[];
  chapters?: { start: number; end: number; title: string }[];
  duration?: number | null;
  sponsor?: { segments: SponsorSegment[]; highlight: number | null } | null;
  errors?: string[];
};

export type JobRanking = {
  model?: string;
  modelUsed?: string;
  returned?: number;
  valid?: number;
  fallback?: number;
  errors?: string[];
  timedOut?: boolean;
  seconds?: number;
  replay?: {
    available?: boolean;
    hotspots?: number;
    skips?: number;
    droppedForSponsor?: number;
    cutInCode?: number;
    chosenOnHotspot?: number;
    weight?: number;
  };
};

export type Job = {
  id: string;
  source: "upload" | "youtube";
  title: string;
  youtubeUrl: string | null;
  status: JobStatus;
  message: string | null;
  error: string | null;
  clips: string[];
  createdAt: string;
  updatedAt: string;
  pid?: number;
  progress?: JobProgress | null;
  failedStage?: string;
  engine?: {
    transcriber?: string;
    gpu?: string | null;
    parakeet?: { audioSeconds?: number; realTimeFactor?: number } | null;
  };
  timings?: Record<string, number | boolean>;
  cost?: JobCost | null;
  clipDetails?: ClipDetail[];
  ranking?: JobRanking;
  reranks?: number;
  /** What the current or last run did: a full run or a re-rank of the saved transcript. */
  mode?: "full" | "rerank";
  /** When the current or last run was started (createdAt for the first run). */
  queuedAt?: string;
  rerankedAt?: string;
  /** Derived by the server: transcript on disk and the video can be had, job not running. */
  canRerank?: boolean;
  /** Derived by the server: an upload whose video was deleted; re-rank asks for the file again. */
  rerankNeedsFile?: boolean;
  videoTransfer?: VideoTransfer | null;
  /** Derived by the server: a cover image is available at /api/media/<id>/thumbnail. */
  hasThumbnail?: boolean;
  /** Derived by the server: changes when the clip files are replaced (for caching). */
  clipsVersion?: string;
  /** Content fingerprint of an uploaded video (see lib/fingerprint.ts). */
  fingerprint?: string;
  /** Every run of this job, oldest first (runner.py; older jobs: --backfill-stats). */
  runs?: RunRecord[];
  /** Video facts saved by the runner (YouTube jobs). */
  meta?: { channel?: string | null; duration?: number | null } | null;
  /** Most replayed graph and SponsorBlock segments (YouTube jobs; left out of job lists). */
  replay?: JobReplay | null;
};

export const ACTIVE_STATUSES: JobStatus[] = [
  "queued",
  "downloading",
  "uploading",
  "processing",
  "transcribing",
  "selecting",
  "rendering",
  "saving",
];

export const isActive = (job: { status: JobStatus }) =>
  ACTIVE_STATUSES.includes(job.status);

/** The fields the progress helpers in format.ts read (a Job or a JobSummary). */
export type StageJob = Pick<
  Job,
  "status" | "progress" | "videoTransfer" | "source" | "mode"
>;

/**
 * What the video list reads of a job: much smaller than a Job (no clip details, runs,
 * ranking or replay graph), so the list poll and the home page's HTML stay small.
 */
export type JobSummary = StageJob & {
  id: string;
  title: string;
  createdAt: string;
  clipCount: number;
  hasThumbnail: boolean;
  /** Length of the video, when known (YouTube metadata or the transcript). */
  durationSeconds: number | null;
};

export function toJobSummary(job: Job, hasThumbnail: boolean): JobSummary {
  return {
    id: job.id,
    source: job.source,
    title: job.title,
    status: job.status,
    createdAt: job.createdAt,
    mode: job.mode,
    progress: job.progress ?? null,
    videoTransfer: job.videoTransfer ?? null,
    clipCount: job.clips.length,
    hasThumbnail,
    durationSeconds:
      job.meta?.duration ?? job.engine?.parakeet?.audioSeconds ?? null,
  };
}
