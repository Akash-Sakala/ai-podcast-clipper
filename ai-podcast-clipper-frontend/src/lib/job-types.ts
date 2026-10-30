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
  | "downloading"
  | "uploading"
  | "transcribing"
  | "ranking"
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
  /** While downloading: on Modal, or on this laptop (the fallback). */
  where?: "cloud" | "laptop";
};

/**
 * How the current or last run got the video to Modal: downloaded on Modal (YouTube),
 * downloaded on the laptop and uploaded (fallback), or uploaded from the job folder.
 */
export type VideoTransfer = "cloud-download" | "laptop-download" | "upload";

/** Estimated Modal cost of the last run (see backend costs.py). */
export type JobCost = {
  usd: number;
  gpuSeconds?: number | null;
  gpu?: string | null;
  containers?: number | null;
};

/** One run of a job (first run, re-rank or failed attempt), kept for the stats page. */
export type RunRecord = {
  mode: "full" | "rerank";
  status: "done" | "no-clips" | "failed";
  startedAt: string;
  finishedAt: string;
  clips: number | null;
  /** "single-gpu": the first versions, before every clip got its own GPU. */
  pipeline: "parallel" | "single-gpu";
  videoSeconds: number | null;
  /** Seconds per step; a step that did not run is missing. */
  timings: Partial<
    Record<
      | "download"
      | "upload"
      | "cloud_download"
      | "gpu_transcribe"
      | "gpu_select"
      | "gpu_render"
      | "gpu_total"
      | "total",
      number
    >
  >;
  /** estimated: worked out from logged GPU time (runs before per-container usage). */
  cost: (JobCost & { estimated?: boolean }) | null;
  recordedBy?: "runner" | "log";
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
  /** "fallback" = picked from the transcript because Gemini returned too few moments. */
  source?: "gemini" | "fallback";
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
  /** Why the laptop downloaded the video: YouTube blocked Modal, or the cloud download failed. */
  downloadFallback?: "blocked" | "failed" | null;
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

export const isActive = (job: Job) => ACTIVE_STATUSES.includes(job.status);
