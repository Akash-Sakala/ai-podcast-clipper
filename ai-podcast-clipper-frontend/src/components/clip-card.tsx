"use client";

import { Check, Copy, Download, Repeat } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { clipLength } from "~/lib/format";
import type { ClipDetail } from "~/lib/job-types";
import { cn } from "~/lib/utils";

const SCORE_LABELS: { key: string; label: string }[] = [
  { key: "hook", label: "Hook" },
  { key: "standalone", label: "Stands alone" },
  { key: "payoff", label: "Payoff" },
  { key: "emotion", label: "Emotion" },
  { key: "value", label: "Value" },
  { key: "shareability", label: "Shareable" },
];

function ScoreRow({ label, value }: { label: string; value: number }) {
  const shown = Math.max(0, Math.min(10, value));
  return (
    <div className="contents">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="bg-muted h-1.5 overflow-hidden rounded-full">
        <span
          className="bg-tally block h-full rounded-full"
          style={{ width: `${shown * 10}%` }}
        />
      </dd>
      <dd className="tabular text-right font-medium">{shown}</dd>
    </div>
  );
}

/** "Audience" row: how often YouTube viewers replay this part (0-10). */
function AudienceRow({ audience }: { audience: number }) {
  return (
    <dl className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1.5 text-sm">
      <ScoreRow label="Audience" value={Math.round(audience / 10)} />
    </dl>
  );
}

function ScoreDetails({
  detail,
  weight,
}: {
  detail: ClipDetail;
  weight: number;
}) {
  const audience = detail.audience ?? null;
  if (detail.source === "fallback" || detail.source === "replay") {
    return (
      <div className="space-y-3">
        {audience != null && <AudienceRow audience={audience} />}
        <p
          className={cn(
            "text-muted-foreground text-sm leading-relaxed",
            audience != null && "border-t pt-3",
          )}
        >
          {detail.source === "replay"
            ? "Gemini was busy, so this clip was cut around a moment YouTube viewers replay most."
            : "Gemini returned too few moments, so this one was picked from the transcript by pacing and pauses."}{" "}
          Re-rank to give Gemini another try.
        </p>
      </div>
    );
  }
  const scores = detail.scores ?? {};
  const viewers = Math.round(weight * 100);
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1.5 text-sm">
        {SCORE_LABELS.map(({ key, label }) => (
          <ScoreRow key={key} label={label} value={scores[key] ?? 0} />
        ))}
      </dl>
      {audience != null && (
        <div className="space-y-2 border-t pt-3">
          <AudienceRow audience={audience} />
          <p className="text-muted-foreground text-xs leading-relaxed">
            The score is {100 - viewers}% Gemini
            {detail.gemini != null
              ? ` (${Math.round(detail.gemini)})`
              : ""} and {viewers}% how often viewers replay this part (
            {Math.round(audience)}).
          </p>
        </div>
      )}
      {detail.reason && (
        <p className="border-t pt-3 text-sm leading-relaxed">{detail.reason}</p>
      )}
    </div>
  );
}

/** "Most replayed" pill for clips that contain a replay hotspot. */
function PeakBadge({ detail }: { detail?: ClipDetail }) {
  if (!detail?.peak) return null;
  const highlight = detail.peak.kind === "highlight";
  return (
    <span
      className="bg-tally-soft text-foreground mt-1.5 inline-flex h-5 items-center gap-1 rounded-md px-1.5 text-xs font-medium"
      title={
        highlight
          ? "Contains the moment SponsorBlock users marked as the highlight"
          : "Contains one of the parts YouTube viewers replay most"
      }
    >
      <Repeat className="size-3" aria-hidden />
      {highlight ? "Viewer highlight" : "Most replayed"}
    </span>
  );
}

export function ClipCard({
  jobId,
  clip,
  detail,
  version,
  replayWeight = 0.4,
}: {
  jobId: string;
  clip: string;
  detail?: ClipDetail;
  /** Changes whenever the clip files are replaced (re-rank), so cached copies stay valid. */
  version: string;
  /** Share of the score from the audience (the job's ranking.replay.weight). */
  replayWeight?: number;
}) {
  const [copied, setCopied] = useState(false);
  const [showScore, setShowScore] = useState(false);
  const tipId = useId();
  const base = `/api/media/${jobId}/${clip}`;
  const v = encodeURIComponent(version);
  const src = `${base}?v=${v}`;
  const poster = `${base.replace(/\.mp4$/, ".jpg")}?v=${v}`;
  const rank = detail?.rank ?? Number(/\d+/.exec(clip)?.[0] ?? 0) + 1;
  const score = detail?.score != null ? Math.round(detail.score) : null;
  const length =
    detail?.start != null && detail?.end != null
      ? clipLength(detail.end - detail.start)
      : null;
  const title = detail?.title?.trim() ? detail.title.trim() : `Clip ${rank}`;

  const copyTitle = async () => {
    try {
      await navigator.clipboard.writeText(title);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Couldn't copy the title");
    }
  };

  return (
    <article
      id={clip.replace(/\.mp4$/, "")}
      className="group flex scroll-mt-6 flex-col gap-3"
    >
      <div className="relative">
        <video
          src={src}
          poster={poster}
          controls
          preload="metadata"
          playsInline
          // object-contain, not cover: in fullscreen the player fills the screen and
          // "cover" would crop the vertical clip to the screen's width.
          className="aspect-[9/16] w-full rounded-lg bg-black object-contain [&:fullscreen]:rounded-none"
        />
        <div
          className="absolute top-2 left-2"
          onMouseEnter={() => setShowScore(true)}
          onMouseLeave={() => setShowScore(false)}
        >
          <button
            type="button"
            aria-describedby={showScore ? tipId : undefined}
            aria-label={`Rank ${rank}${score != null ? `, score ${score} of 100` : ""}. Show score details`}
            onFocus={() => setShowScore(true)}
            onBlur={() => setShowScore(false)}
            onClick={() => setShowScore((v) => !v)}
            onKeyDown={(e) => e.key === "Escape" && setShowScore(false)}
            className={cn(
              "bg-tally text-tally-ink flex items-baseline gap-1.5 rounded-md px-2 py-1 shadow-sm",
              (detail?.source === "fallback" || detail?.source === "replay") &&
                "bg-card text-foreground",
            )}
          >
            <span className="font-display tabular text-lg leading-none font-bold">
              #{rank}
            </span>
            {score != null && (
              <span className="tabular text-xs leading-none font-semibold opacity-80">
                {score}
              </span>
            )}
          </button>
          {showScore && detail && (
            <div
              id={tipId}
              role="tooltip"
              className="bg-popover text-popover-foreground absolute top-full left-0 z-20 mt-2 w-64 rounded-lg border p-3 shadow-lg shadow-black/10 dark:shadow-black/40"
            >
              <ScoreDetails detail={detail} weight={replayWeight} />
            </div>
          )}
        </div>
      </div>

      <div>
        <h3
          className="line-clamp-2 text-sm leading-snug font-medium"
          title={title}
        >
          {title}
        </h3>
        <PeakBadge detail={detail} />
        <div className="mt-1 flex items-center justify-between gap-2">
          <span className="text-muted-foreground tabular text-xs">
            {length}
          </span>
          <div className="-mr-1.5 flex shrink-0">
            <button
              type="button"
              onClick={copyTitle}
              aria-label="Copy title"
              title="Copy title"
              className="text-muted-foreground hover:text-foreground hover:bg-accent inline-flex size-8 items-center justify-center rounded-md transition-colors"
            >
              {copied ? (
                <Check className="size-4" />
              ) : (
                <Copy className="size-4" />
              )}
            </button>
            <a
              href={`${base}?download`}
              download={`${String(rank).padStart(2, "0")} ${title}.mp4`}
              aria-label="Download clip"
              title="Download clip"
              className="text-muted-foreground hover:text-foreground hover:bg-accent inline-flex size-8 items-center justify-center rounded-md transition-colors"
            >
              <Download className="size-4" />
            </a>
          </div>
        </div>
      </div>
    </article>
  );
}
