"use client";

import { Check, Copy, Download, Play } from "lucide-react";
import { useReducedMotion } from "motion/react";
import { memo, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { clipLength } from "~/lib/format";
import type { ClipDetail } from "~/lib/job-types";
import { cn } from "~/lib/utils";
import {
  clipTitle,
  FallbackTag,
  isFallback,
  PeakBadge,
  PostedTag,
  ScoreDetails,
} from "./clip-details";
import { IconButton } from "./ui/icon-button";
import { useOpenClip } from "./video/clip-viewer";

/** A hovered poster starts its muted preview after this long (a mouse only). */
const PREVIEW_DELAY_MS = 400;

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
        aria-label={`Rank ${rank}${score != null ? `, score ${score} of 100` : ""}${isFallback(detail) ? ", picked without Gemini" : ""}. Show score details`}
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
          isFallback(detail) && "bg-card text-foreground",
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
  const title = clipTitle(detail, rank);
  const openClip = useOpenClip();
  const poster_ = useRef<HTMLButtonElement>(null);
  const reduce = useReducedMotion();
  // A mouse resting on the poster plays a muted preview; touch screens never do.
  const [preview, setPreview] = useState(false);
  const previewTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(previewTimer.current), []);

  const copyTitle = async () => {
    try {
      const postTitle = detail?.post?.title?.trim() ?? "";
      await navigator.clipboard.writeText(postTitle.length ? postTitle : title);
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
          "ease-move relative rounded-lg transition-[box-shadow] duration-(--motion-enter)",
          outlined && "ring-tally ring-offset-background ring-2 ring-offset-2",
          arrived && "clip-arrive",
        )}
      >
        <button
          ref={poster_}
          type="button"
          aria-label={`Play clip ${rank}: ${title}`}
          onClick={() => {
            setPreview(false);
            openClip?.(
              set === "new" ? "new" : "main",
              clip,
              poster_.current?.getBoundingClientRect() ?? null,
            );
          }}
          onPointerEnter={(event) => {
            if (event.pointerType !== "mouse" || reduce) return;
            window.clearTimeout(previewTimer.current);
            previewTimer.current = window.setTimeout(
              () => setPreview(true),
              PREVIEW_DELAY_MS,
            );
          }}
          onPointerLeave={() => {
            window.clearTimeout(previewTimer.current);
            setPreview(false);
          }}
          className={cn(
            "group/poster relative block aspect-[9/16] w-full overflow-hidden rounded-lg bg-black transition-opacity",
            dimmed && "opacity-50",
          )}
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- local API image */}
          <img
            src={poster}
            alt=""
            loading="lazy"
            className="size-full object-contain"
          />
          {preview && (
            <video
              src={src}
              muted
              autoPlay
              loop
              playsInline
              aria-hidden
              className="pop-in absolute inset-0 size-full object-contain"
            />
          )}
          <span
            aria-hidden
            className={cn(
              "absolute right-2 bottom-2 inline-flex size-9 items-center justify-center rounded-full bg-black/60 text-white transition-opacity duration-(--motion-enter)",
              preview
                ? "opacity-0"
                : "opacity-80 group-hover/poster:opacity-100",
            )}
          >
            <Play className="ml-0.5 size-4" aria-hidden />
          </span>
        </button>
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
        <FallbackTag detail={detail} />
        <PeakBadge detail={detail} />
        <PostedTag detail={detail} />
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
