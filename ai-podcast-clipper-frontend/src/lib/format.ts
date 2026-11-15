import type { Job, ProgressStage, StageJob } from "./job-types";

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

export function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** An amount kept in both currencies: INR at the rate of the day it was spent. */
export type Money = { usd: number; inr: number | null };

/** "$0.11" or "₹10.34"; "<$0.01" / "<₹0.01" for tiny amounts. Falls back to dollars
 * when no rupee rate is known. */
export function formatMoney(value: Money, currency: "usd" | "inr") {
  if (currency === "inr" && value.inr !== null) {
    const inr = value.inr;
    if (inr > 0 && inr < 0.01) return "<₹0.01";
    return `₹${inr.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }
  if (value.usd > 0 && value.usd < 0.005) return "<$0.01";
  return `$${value.usd.toFixed(2)}`;
}

/** Share of the rendering step that is done (0-1), or null when unknown. */
export function renderShare(job: StageJob): number | null {
  const p = job.progress;
  if (currentStage(job) !== "rendering" || !p?.total) return null;
  if (typeof p.fraction === "number")
    return Math.min(1, Math.max(0, p.fraction));
  return (p.done ?? 0) / p.total;
}

const TRANSFER_STAGES: (ProgressStage | "queued")[] = [
  "audio",
  "downloading",
  "uploading",
  "clips",
];

/** Share of the download or the upload done (0-1), while it runs. */
export function transferShare(job: StageJob) {
  const p = job.progress;
  if (
    !TRANSFER_STAGES.includes(currentStage(job)) ||
    typeof p?.fraction !== "number"
  )
    return null;
  return Math.min(1, Math.max(0, p.fraction));
}

type Step = { stage: ProgressStage; label: string };

/**
 * Steps shown in the progress strip, in order. A YouTube video is downloaded on the
 * laptop, then uploaded; an uploaded MP4 is only uploaded. A two-phase run ("audio-first")
 * sends the audio, transcribes and ranks while a YouTube video downloads, then sends
 * only the chosen clips' footage.
 */
export function stepsFor(job: StageJob): Step[] {
  if (job.videoTransfer === "audio-first") {
    const steps: Step[] = [
      { stage: "audio", label: "Send audio" },
      { stage: "transcribing", label: "Transcribe" },
      { stage: "ranking", label: "Find moments" },
      { stage: "downloading", label: "Download" },
      { stage: "clips", label: "Send clips" },
      { stage: "rendering", label: "Render" },
      { stage: "saving", label: "Save" },
    ];
    return steps.filter((step) => {
      if (step.stage === "audio" || step.stage === "transcribing")
        return job.mode !== "rerank";
      if (step.stage === "downloading") return job.source === "youtube";
      return true;
    });
  }
  const steps: Step[] = [
    { stage: "downloading", label: "Download" },
    { stage: "uploading", label: "Upload" },
    { stage: "transcribing", label: "Transcribe" },
    { stage: "ranking", label: "Find moments" },
    { stage: "rendering", label: "Render" },
    { stage: "saving", label: "Save" },
  ];
  const transfer =
    job.videoTransfer ??
    (job.source === "youtube" ? "laptop-download" : "upload");
  return steps.filter((step) => {
    if (step.stage === "downloading") return transfer !== "upload";
    if (step.stage === "transcribing") return job.mode !== "rerank";
    return true;
  });
}

/** ", downloading the video meanwhile, 40%" while a two-phase run overlaps the two. */
function meanwhile(job: StageJob) {
  const share = job.progress?.download;
  return typeof share === "number"
    ? `, downloading the video meanwhile, ${Math.round(share * 100)}%`
    : "";
}

function percent(p: StageJob["progress"]) {
  return typeof p?.fraction === "number"
    ? `, ${Math.round(p.fraction * 100)}%`
    : "";
}

/** Current stage of an active job, also for runners that predate `progress`. */
export function currentStage(job: StageJob): ProgressStage | "queued" {
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

export function stageSentence(job: StageJob) {
  const stage = currentStage(job);
  const p = job.progress;
  switch (stage) {
    case "queued":
      return "Starting";
    case "audio":
      return typeof p?.fraction === "number"
        ? `Sending the audio to Modal${percent(p)}`
        : "Getting the audio";
    case "clips":
      return typeof p?.fraction === "number"
        ? `Sending the clips to Modal${percent(p)}`
        : "Cutting the clips";
    case "downloading":
      return typeof p?.fraction === "number"
        ? `Downloading from YouTube, ${Math.round(p.fraction * 100)}%`
        : "Downloading from YouTube";
    case "uploading": {
      const done =
        typeof p?.fraction === "number"
          ? `Uploading to Modal, ${Math.round(p.fraction * 100)}%`
          : "Uploading to Modal";
      return p?.stalled
        ? `${done}: no data moving, retrying the stuck parts`
        : done;
    }
    case "transcribing":
      return `Transcribing${meanwhile(job)}`;
    case "ranking":
      return `Finding the best moments${meanwhile(job)}`;
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
    return "YouTube asked to confirm you're not a bot. Set YTDLP_COOKIES_FILE (or YTDLP_COOKIES_FROM_BROWSER=firefox) in .env and try again.";
  if (error.includes("python not found"))
    return "The backend Python environment wasn't found. Create the backend .venv or set RUNNER_PYTHON in .env.";
  if (error.includes("modal deploy"))
    return "The Modal app is older than this version. Run modal deploy main.py in the backend folder, then try again.";
  if (error.includes("stopped unexpectedly"))
    return "The job stopped unexpectedly. Try again.";
  const where: Record<string, string> = {
    audio: "Couldn't send the audio to Modal.",
    downloading: "Couldn't download this video.",
    uploading: "Couldn't upload the video to Modal.",
    clips: "Couldn't send the clips to Modal.",
    transcribing: "Transcription failed on the GPU.",
    ranking: "Finding moments failed.",
    rendering: "Rendering the clips failed.",
    saving: "Couldn't save the finished clips.",
  };
  return (
    where[job.failedStage ?? ""] ?? "Something went wrong while processing."
  );
}

/** A YouTube job keeps its link as the title until the download names it. */
export function displayTitle(job: { title: string }) {
  return job.title.replace(/^https?:\/\/(www\.)?/, "");
}

export function fallbackCount(job: Job) {
  // Transcript picks, and clips cut around a replay hotspot that Gemini could not score.
  return (job.clipDetails ?? []).filter(
    (d) => d.source === "fallback" || d.source === "replay",
  ).length;
}
