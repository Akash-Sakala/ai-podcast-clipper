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
  etaSeconds?: number | null;
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
  clipDetails?: ClipDetail[];
  ranking?: JobRanking;
  reranks?: number;
  /** What the current or last run did: a full run or a re-rank of the saved transcript. */
  mode?: "full" | "rerank";
  /** When the current or last run was started (createdAt for the first run). */
  queuedAt?: string;
  rerankedAt?: string;
  /** Derived by the server: video + transcript still on disk, job not running. */
  canRerank?: boolean;
  /** Derived by the server: a cover image is available at /api/media/<id>/thumbnail. */
  hasThumbnail?: boolean;
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
