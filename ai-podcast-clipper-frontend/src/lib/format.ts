import type { Job, ProgressStage } from "./job-types";

export function relativeTime(iso: string, now = Date.now()) {
  const seconds = Math.max(0, (now - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
}

export function clipLength(seconds: number) {
  const s = Math.round(seconds);
  return s < 60
    ? `${s} s`
    : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function timecode(seconds: number) {
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
}

export function duration(seconds: number) {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const m = Math.round(seconds / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** Step and run times: "45 s", "6 min 41 s", "20 min". */
export function stepTime(seconds: number) {
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s >= 600) return `${Math.round(s / 60)} min`;
  return s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`;
}

/** "$0.06", or "Under $0.01" for very cheap runs. */
export function money(usd: number) {
  return usd < 0.01 ? "Under $0.01" : `$${usd.toFixed(2)}`;
}

/** Share of the rendering step that is done (0-1), or null when unknown. */
export function renderShare(job: Job): number | null {
  const p = job.progress;
  if (currentStage(job) !== "rendering" || !p?.total) return null;
  if (typeof p.fraction === "number")
    return Math.min(1, Math.max(0, p.fraction));
  return (p.done ?? 0) / p.total;
}

/** Share of the download done (0-1), while downloading. */
export function downloadShare(job: Job) {
  const p = job.progress;
  if (currentStage(job) !== "downloading" || typeof p?.fraction !== "number")
    return null;
  return Math.min(1, Math.max(0, p.fraction));
}

/**
 * Steps shown in the progress strip, in order. A YouTube video downloads on Modal
 * (no upload step) unless the laptop takes over; an upload, or a video still in the
 * job folder, is uploaded.
 */
export function stepsFor(job: Job): { stage: ProgressStage; label: string }[] {
  const steps: { stage: ProgressStage; label: string }[] = [
    { stage: "downloading", label: "Download" },
    { stage: "uploading", label: "Upload" },
    { stage: "transcribing", label: "Transcribe" },
    { stage: "ranking", label: "Find moments" },
    { stage: "rendering", label: "Render" },
    { stage: "saving", label: "Save" },
  ];
  const transfer =
    job.videoTransfer ??
    (job.source === "youtube" ? "cloud-download" : "upload");
  return steps.filter((step) => {
    if (step.stage === "downloading") return transfer !== "upload";
    if (step.stage === "uploading") return transfer !== "cloud-download";
    if (step.stage === "transcribing") return job.mode !== "rerank";
    return true;
  });
}

/** Current stage of an active job, also for runners that predate `progress`. */
export function currentStage(job: Job): ProgressStage | "queued" {
  if (job.progress?.stage) return job.progress.stage;
  switch (job.status) {
    case "downloading":
      return "downloading";
    case "uploading":
      return "uploading";
    case "transcribing":
      return "transcribing";
    case "selecting":
      return "ranking";
    case "rendering":
      return "rendering";
    case "processing":
      return "transcribing";
    case "saving":
      return "saving";
    default:
      return "queued";
  }
}

export function stageSentence(job: Job) {
  const stage = currentStage(job);
  const p = job.progress;
  switch (stage) {
    case "queued":
      return "Starting";
    case "downloading": {
      const where =
        p?.where === "laptop"
          ? job.downloadFallback === "blocked"
            ? "YouTube blocked Modal, downloading on your laptop"
            : job.downloadFallback
              ? "Downloading on your laptop instead"
              : "Downloading from YouTube"
          : "Downloading from YouTube to Modal";
      return typeof p?.fraction === "number"
        ? `${where}, ${Math.round(p.fraction * 100)}%`
        : where;
    }
    case "uploading":
      return "Uploading to the GPU";
    case "transcribing":
      return "Transcribing";
    case "ranking":
      return "Finding the best moments";
    case "rendering":
      if (!p?.total) return "Rendering clips";
      // Clips render at the same time; older runners rendered one after another.
      if (typeof p.fraction === "number") {
        const done = p.done ?? 0;
        if (done === 0)
          return p.total === 1
            ? "Rendering 1 clip"
            : `Rendering ${p.total} clips`;
        return `Rendering clips, ${done} of ${p.total} ready`;
      }
      return `Rendering clip ${Math.min((p.done ?? 0) + 1, p.total)} of ${p.total}`;
    case "saving":
      return "Saving clips";
  }
}

/** Plain-language reason for a failed job. */
export function failureSentence(job: Job) {
  const error = (job.error ?? "").toLowerCase();
  if (error.includes("sign in") || error.includes("bot"))
    return "YouTube asked to confirm you're not a bot. Set YTDLP_COOKIES_FROM_BROWSER in .env and try again.";
  if (error.includes("python not found"))
    return "The backend Python environment wasn't found. Create the backend .venv or set RUNNER_PYTHON in .env.";
  if (error.includes("stopped unexpectedly"))
    return "The job stopped unexpectedly. Try again.";
  const where: Record<string, string> = {
    downloading: "Couldn't download this video.",
    uploading: "Couldn't upload the video to Modal.",
    transcribing: "Transcription failed on the GPU.",
    ranking: "Finding moments failed.",
    rendering: "Rendering the clips failed.",
    saving: "Couldn't save the finished clips.",
  };
  return (
    where[job.failedStage ?? ""] ?? "Something went wrong while processing."
  );
}
