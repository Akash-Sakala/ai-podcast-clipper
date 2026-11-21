import { Repeat } from "lucide-react";
import { timecode } from "~/lib/format";
import type { ClipDetail } from "~/lib/job-types";
import { cn } from "~/lib/utils";

// What the clip card's score panel and the clip viewer both show about a clip.

/** Picked without Gemini: from the transcript's pacing, or around a replay hotspot. */
export function isFallback(detail?: ClipDetail) {
  return detail?.source === "fallback" || detail?.source === "replay";
}

/**
 * A clip's title. Gemini writes a title for each clip it picks. A clip picked without
 * Gemini would otherwise carry a raw transcript sentence, which never goes on a card;
 * it gets a neutral title from its place in the video instead.
 */
export function clipTitle(detail: ClipDetail | undefined, rank: number) {
  if (isFallback(detail)) {
    return detail?.start != null && detail?.end != null
      ? `Clip ${rank}, ${timecode(detail.start)} to ${timecode(detail.end)}`
      : `Clip ${rank}`;
  }
  return detail?.title?.trim() ? detail.title.trim() : `Clip ${rank}`;
}

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

export function ScoreDetails({
  detail,
  weight,
}: {
  detail: ClipDetail;
  weight: number;
}) {
  const audience = detail.audience ?? null;
  if (isFallback(detail)) {
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
export function PeakBadge({ detail }: { detail?: ClipDetail }) {
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

/** Grey tag for a clip picked without Gemini (its title is neutral, see clipTitle). */
export function FallbackTag({ detail }: { detail?: ClipDetail }) {
  if (!isFallback(detail)) return null;
  return (
    <span className="bg-muted text-muted-foreground mt-1.5 mr-1.5 inline-flex h-5 items-center rounded-md px-1.5 text-xs font-medium">
      Picked without Gemini
    </span>
  );
}
