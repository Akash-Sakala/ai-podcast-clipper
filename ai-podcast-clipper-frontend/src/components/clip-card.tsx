"use client";

import { Check, Copy, Download, Repeat } from "lucide-react";
import { memo, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { clipLength } from "~/lib/format";
import type { ClipDetail } from "~/lib/job-types";
import { cn } from "~/lib/utils";
import { IconButton } from "./ui/icon-button";

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

/**
 * The score badge and its details panel. Hover and keyboard focus open it (as before);
 * a click or tap opens it and keeps it open (pinned) until a second click, a press
 * outside, or Escape. Opening is idempotent, so the focus/hover events that precede a
 * tap's click can't cancel it. The panel is nudged sideways so it always stays inside
 * the viewport (the right-hand column on a phone would otherwise overflow).
 */
function ScoreBadge({
  detail,
  rank,
  score,
  replayWeight,
}: {
  detail?: ClipDetail;
  rank: number;
  score: number | null;
  replayWeight: number;
}) {
  const [open, setOpen] = useState(false);
  const [shift, setShift] = useState(0);
  // Viewport width measured while the panel is closed: once an overflowing panel is
  // open, a mobile browser widens the layout viewport and the width can't be trusted.
  const viewport = useRef(0);
  const pinned = useRef(false);
  const root = useRef<HTMLDivElement>(null);
  const isOpen = useRef(false);
  isOpen.current = open;
  const tipId = useId();

  useEffect(() => {
    const measure = () => {
      if (!isOpen.current)
        viewport.current = document.documentElement.clientWidth;
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  const close = () => {
    pinned.current = false;
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    const onPress = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("pointerdown", onPress);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPress);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Where the panel goes, worked out BEFORE it is shown, so it never overflows the page
  // even for a moment (a phone would widen the page for good). The panel is as wide as
  // `w-64` (16 rem) but never wider than the viewport minus a margin.
  const show = () => {
    const margin = 8;
    const left = root.current?.getBoundingClientRect().left ?? 0;
    const width = viewport.current || document.documentElement.clientWidth;
    const panelWidth = Math.min(256, width - 2 * margin);
    let next = 0;
    if (left + panelWidth > width - margin)
      next = width - margin - panelWidth - left;
    if (left + next < margin) next = margin - left;
    setShift(next);
    setOpen(true);
  };

  const mouse = (event: React.PointerEvent) => event.pointerType === "mouse";

  return (
    <div
      ref={root}
      className="absolute top-2 left-2"
      onPointerEnter={(e) => mouse(e) && show()}
      onPointerLeave={(e) => mouse(e) && !pinned.current && setOpen(false)}
    >
      <button
        type="button"
        aria-describedby={open ? tipId : undefined}
        aria-expanded={open}
        aria-label={`Rank ${rank}${score != null ? `, score ${score} of 100` : ""}. Show score details`}
        onFocus={show}
        onBlur={(e) => {
          if (!root.current?.contains(e.relatedTarget as Node | null)) close();
        }}
        onClick={() => {
          if (open && pinned.current) return close();
          pinned.current = true;
          show();
        }}
        className={cn(
          // 26 px tall with a mouse, 34 px on a touch screen (it sits on the video, so
          // it stays smaller than a full 44 px button).
          "bg-tally text-tally-ink press flex items-baseline gap-1.5 rounded-md px-2 py-1 shadow-sm pointer-coarse:px-2.5 pointer-coarse:py-2",
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
      {open && detail && (
        <div
          id={tipId}
          role="tooltip"
          // Pressing inside the panel must not move focus off the badge (which would close it).
          onMouseDown={(e) => e.preventDefault()}
          style={{ transform: `translateX(${shift}px)` }}
          className="bg-popover text-popover-foreground pop-in absolute top-full left-0 z-20 mt-2 w-64 max-w-[calc(100vw-1rem)] origin-top-left rounded-lg border p-3 shadow-lg shadow-black/10 dark:shadow-black/40"
        >
          <ScoreDetails detail={detail} weight={replayWeight} />
        </div>
      )}
    </div>
  );
}

export const ClipCard = memo(function ClipCard({
  jobId,
  clip,
  detail,
  version,
  replayWeight = 0.4,
  set,
  selection,
  note,
  dimmed = false,
  outlined = false,
  arrived = false,
}: {
  jobId: string;
  clip: string;
  detail?: ClipDetail;
  /** Changes whenever the clip files are replaced (re-rank), so cached copies stay valid. */
  version: string;
  /** Share of the score from the audience (the job's ranking.replay.weight). */
  replayWeight?: number;
  /** "new": a re-rank's new set (clips_new/), compared with the live clips. */
  set?: "new";
  /** Shown between the player and the title (the comparison's Keep checkbox). */
  selection?: React.ReactNode;
  /** Shown under the title (the comparison's "Same moment" badge). */
  note?: React.ReactNode;
  /** The player at half opacity (a clip that will not be kept). */
  dimmed?: boolean;
  /** A thin amber outline around the player (a clip that will be kept). */
  outlined?: boolean;
  /** Saved during a run while the page was open: the player's frame lights up once. */
  arrived?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const base = `/api/media/${jobId}/${clip}`;
  const v = encodeURIComponent(version);
  const query = set === "new" ? `set=new&v=${v}` : `v=${v}`;
  const src = `${base}?${query}`;
  const poster = `${base.replace(/\.mp4$/, ".jpg")}?${query}`;
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
      id={`${set === "new" ? "new-" : ""}${clip.replace(/\.mp4$/, "")}`}
      className="group flex scroll-mt-6 flex-col gap-3"
    >
      <div
        className={cn(
          "relative rounded-lg",
          outlined && "ring-tally ring-offset-background ring-2 ring-offset-2",
          arrived && "clip-arrive",
        )}
      >
        <video
          src={src}
          poster={poster}
          controls
          preload="none"
          aria-label={title}
          playsInline
          // object-contain, not cover: in fullscreen the player fills the screen and
          // "cover" would crop the vertical clip to the screen's width.
          className={cn(
            "aspect-[9/16] w-full rounded-lg bg-black object-contain transition-opacity [&:fullscreen]:rounded-none",
            dimmed && "opacity-50",
          )}
        />
        <ScoreBadge
          detail={detail}
          rank={rank}
          score={score}
          replayWeight={replayWeight}
        />
      </div>

      <div>
        {selection}
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
          <div className="-mr-1.5 flex shrink-0 pointer-coarse:-mr-2.5 pointer-coarse:gap-2">
            <IconButton
              size="sm"
              label={copied ? "Title copied" : "Copy title"}
              onClick={copyTitle}
            >
              {copied ? (
                <Check className="pop-in size-4" aria-hidden />
              ) : (
                <Copy className="size-4" aria-hidden />
              )}
            </IconButton>
            <IconButton
              size="sm"
              label="Download clip"
              href={`${base}?${set === "new" ? "set=new&" : ""}download`}
              download={`${String(rank).padStart(2, "0")} ${title}.mp4`}
            >
              <Download className="size-4" aria-hidden />
            </IconButton>
          </div>
        </div>
        {note}
      </div>
    </article>
  );
});
