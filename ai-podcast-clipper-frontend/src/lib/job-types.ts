// Shared between server code and client components (no Node.js imports here).

export type JobStatus =
  | "queued"
  | "downloading"
  | "uploading"
  | "processing"
  | "saving"
  | "done"
  | "failed";

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
};

export const ACTIVE_STATUSES: JobStatus[] = [
  "queued",
  "downloading",
  "uploading",
  "processing",
  "saving",
];
