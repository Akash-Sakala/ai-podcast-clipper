"use client";

import {
  ChevronDown,
  ChevronUp,
  Download,
  Info,
  Pause,
  Play,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { AnimatePresence, m, useAnimate, useReducedMotion } from "motion/react";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { clipLength, timecode } from "~/lib/format";
import type { ClipDetail } from "~/lib/job-types";
import { duration, ease } from "~/lib/motion";
import { cn } from "~/lib/utils";
import {
  clipTitle,
  FallbackTag,
  isFallback,
  PeakBadge,
  ScoreDetails,
} from "../clip-details";
import { PostPanel, usePostDraft } from "./post-panel";

/** "main": the clips on the page (or the previous set in a comparison); "new": a re-rank's new set. */
export type ViewerList = "main" | "new";

export type ViewerClip = { name: string; detail?: ClipDetail; version: string };

/** Post text in the viewer: the video's default credit line and whether the text can be changed. */
export type ViewerPost = {
  credit: string;
  /** The lists whose clips can be edited and marked as posted (not a running video's). */
  editable: ViewerList[];
};

type Open = {
  list: ViewerList;
  clip: string;
  /** Where the card's poster was on screen, for the opening animation. */
  origin: DOMRect | null;
  /** Opened from a link (no click): the clip starts muted, as browsers require. */
  fromLink: boolean;
};

const ViewerContext = createContext<
  ((list: ViewerList, clip: string, origin: DOMRect | null) => void) | null
>(null);

/** Opens a clip in the viewer (null outside a ClipViewerProvider). */
export function useOpenClip() {
  return useContext(ViewerContext);
}

/** The page address with (or without) a clip open. */
function urlFor(open: { list: ViewerList; clip: string } | null) {
  const url = new URL(window.location.href);
  url.searchParams.delete("clip");
  url.searchParams.delete("set");
  if (open) {
    url.searchParams.set("clip", open.clip.replace(/\.mp4$/, ""));
    if (open.list === "new") url.searchParams.set("set", "new");
  }
  return url.pathname + url.search + url.hash;
}

/** The clip named in the address, if any. */
function fromAddress(): { list: ViewerList; clip: string } | null {
  const params = new URLSearchParams(window.location.search);
  const clip = params.get("clip");
  if (!clip || !/^clip_\d+$/.test(clip)) return null;
  return {
    list: params.get("set") === "new" ? "new" : "main",
    clip: `${clip}.mp4`,
  };
}

/**
 * The full-screen clip viewer for one video page. Cards call useOpenClip(); the viewer
 * shows that clip and lets you move through the rest of its set. The address carries
 * ?clip=clip_2 (and &set=new for a re-rank's new set), so a clip can be linked to and the
 * browser's Back button closes the viewer (next and previous replace the entry instead
 * of adding one). Uses the browser's history directly, which Next.js follows without
 * loading the page again.
 */
export function ClipViewerProvider({
  jobId,
  lists,
  replayWeight,
  post,
  children,
}: {
  jobId: string;
  lists: Record<ViewerList, ViewerClip[]>;
  replayWeight?: number;
  post?: ViewerPost;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState<Open | null>(null);
  // The viewer lives in a portal on <body>, made after the page has hydrated.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  /** The viewer added a history entry, so closing goes Back instead of replacing. */
  const pushed = useRef(false);
  /** What had focus before the viewer opened, to give it back. */
  const opener = useRef<HTMLElement | null>(null);

  // A link with ?clip= opens the viewer; Back and Forward open or close it.
  useEffect(() => {
    const read = () => {
      const target = fromAddress();
      if (!target) pushed.current = false;
      setOpen((current) =>
        target
          ? current?.clip === target.clip && current.list === target.list
            ? current
            : { ...target, origin: null, fromLink: !current }
          : null,
      );
    };
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);

  const openClip = useCallback(
    (list: ViewerList, clip: string, origin: DOMRect | null) => {
      opener.current = document.activeElement as HTMLElement | null;
      window.history.pushState(null, "", urlFor({ list, clip }));
      pushed.current = true;
      setOpen({ list, clip, origin, fromLink: false });
    },
    [],
  );

  const go = (clip: string) => {
    if (!open) return;
    window.history.replaceState(null, "", urlFor({ list: open.list, clip }));
    setOpen({ ...open, clip, origin: null, fromLink: false });
  };

  const close = () => {
    const last = open;
    setOpen(null);
    if (pushed.current) {
      pushed.current = false;
      window.history.back();
    } else {
      window.history.replaceState(null, "", urlFor(null));
    }
    // Focus goes to the card of the clip watched last (in view), else to the opener.
    const card = last
      ? document.querySelector<HTMLElement>(
          `#${last.list === "new" ? "new-" : ""}${last.clip.replace(/\.mp4$/, "")} button[aria-label^="Play clip"]`,
        )
      : null;
    const back = card ?? opener.current;
    window.setTimeout(() => {
      card?.scrollIntoView({ block: "nearest" });
      back?.focus({ preventScroll: true });
    });
  };

  const clips = open ? lists[open.list] : [];
  const index = open ? clips.findIndex((c) => c.name === open.clip) : -1;

  return (
    <ViewerContext.Provider value={openClip}>
      {children}
      {mounted &&
        createPortal(
          <AnimatePresence>
            {open && index >= 0 && (
              <ClipViewer
                key="viewer"
                jobId={jobId}
                list={open.list}
                clips={clips}
                index={index}
                origin={open.origin}
                startMuted={open.fromLink}
                replayWeight={replayWeight ?? 0.4}
                credit={post?.credit ?? ""}
                editable={post?.editable.includes(open.list) ?? false}
                onGo={(i) => {
                  const next = clips[i];
                  if (next) go(next.name);
                }}
                onClose={close}
              />
            )}
          </AnimatePresence>,
          document.body,
        )}
    </ViewerContext.Provider>
  );
}

const FOCUSABLE =
  'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Typing in a text field: the viewer's single-key shortcuts stay out of the way. */
function isTyping(target: EventTarget | null) {
  if (target instanceof HTMLTextAreaElement) return true;
  if (target instanceof HTMLInputElement) return target.type !== "range";
  return target instanceof HTMLElement && target.isContentEditable;
}

/**
 * One clip at full height, with its score, post text and actions beside it (below it
 * on a phone, in a sheet you open). Up and Down (or J and K, or a vertical swipe on a
 * phone) move through the set; Space plays and pauses, M mutes, Escape closes (none of
 * them while typing in a text field, except Escape). Focus stays inside until it
 * closes, then goes back to the card.
 */
function ClipViewer({
  jobId,
  list,
  clips,
  index,
  origin,
  startMuted,
  replayWeight,
  credit,
  editable,
  onGo,
  onClose,
}: {
  jobId: string;
  list: ViewerList;
  clips: ViewerClip[];
  index: number;
  origin: DOMRect | null;
  startMuted: boolean;
  replayWeight: number;
  credit: string;
  editable: boolean;
  onGo: (index: number) => void;
  onClose: () => void;
}) {
  const clip = clips[index]!;
  const next = clips[index + 1];
  const detail = clip.detail;
  const rank = detail?.rank ?? index + 1;
  const title = clipTitle(detail, rank);
  const score = detail?.score != null ? Math.round(detail.score) : null;
  const length =
    detail?.start != null && detail?.end != null
      ? clipLength(detail.end - detail.start)
      : null;
  const media = (name: string, version: string, extra = "") =>
    `/api/media/${jobId}/${name}?${list === "new" ? "set=new&" : ""}${extra}v=${encodeURIComponent(version)}`;
  const src = media(clip.name, clip.version);
  const download = `/api/media/${jobId}/${clip.name}?${list === "new" ? "set=new&" : ""}download`;

  const dialog = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [stage, animateStage] = useAnimate<HTMLDivElement>();
  const reduce = useReducedMotion();
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(startMuted);
  const [time, setTime] = useState(0);
  const [total, setTotal] = useState(0);
  const [sheet, setSheet] = useState(false);
  const post = usePostDraft({
    jobId,
    clip: clip.name,
    detail,
    fallbackTitle: title,
    credit,
    editable,
  });
  const hasNext = index < clips.length - 1;
  const hasPrevious = index > 0;

  // The poster grows from the card into place (a fade with reduced motion).
  useLayoutEffect(() => {
    const el = stage.current;
    if (!el || !origin || reduce) return;
    const end = el.getBoundingClientRect();
    if (!end.width) return;
    void animateStage(
      el,
      {
        x: [origin.left - end.left, 0],
        y: [origin.top - end.top, 0],
        scale: [origin.width / end.width, 1],
      },
      { duration: duration.emphasis, ease: ease.enter },
    );
    // Only on open: later clips swap in place.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Focus moves in, the page behind stops scrolling.
  useEffect(() => {
    closeButton.current?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
    };
  }, []);

  // Each clip starts playing; with sound when the viewer was opened by a click (a
  // browser allows that), muted when it came from a link or the browser refuses.
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    setTime(0);
    v.muted = muted;
    v.play().catch(() => {
      v.muted = true;
      setMuted(true);
      void v.play().catch(() => setPlaying(false));
    });
    // The mute choice carries over from clip to clip; only a new clip restarts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.name]);

  const toggle = useCallback(() => {
    const v = video.current;
    if (!v) return;
    if (v.paused) void v.play();
    else v.pause();
  }, []);
  const toggleMute = useCallback(() => {
    const v = video.current;
    if (!v) return;
    v.muted = !v.muted;
    setMuted(v.muted);
  }, []);

  // Keyboard: move, play, mute, close; Tab stays inside the viewer.
  const latest = useRef({
    onGo,
    onClose,
    toggle,
    toggleMute,
    index,
    hasNext,
    hasPrevious,
  });
  latest.current = {
    onGo,
    onClose,
    toggle,
    toggleMute,
    index,
    hasNext,
    hasPrevious,
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const k = latest.current;
      const target = event.target as HTMLElement;
      const inRange = target instanceof HTMLInputElement;
      if (event.key !== "Escape" && event.key !== "Tab" && isTyping(target))
        return;
      if (event.key === "Escape") {
        event.preventDefault();
        k.onClose();
      } else if (
        (event.key === "ArrowDown" || event.key === "j") &&
        k.hasNext &&
        !inRange
      ) {
        event.preventDefault();
        k.onGo(k.index + 1);
      } else if (
        (event.key === "ArrowUp" || event.key === "k") &&
        k.hasPrevious &&
        !inRange
      ) {
        event.preventDefault();
        k.onGo(k.index - 1);
      } else if (event.key === " " && !(target instanceof HTMLButtonElement)) {
        event.preventDefault();
        k.toggle();
      } else if (event.key === "m") {
        k.toggleMute();
      } else if (event.key === "Tab" && dialog.current) {
        const items = Array.from(
          dialog.current.querySelectorAll<HTMLElement>(FOCUSABLE),
        ).filter((el) => el.offsetParent !== null);
        const first = items[0];
        const last = items[items.length - 1];
        if (!first || !last) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // A phone: swipe up for the next clip, down for the previous one; a tap plays or pauses.
  const touch = useRef<{ y: number; x: number } | null>(null);

  const round =
    "press inline-flex size-11 items-center justify-center rounded-full bg-white/10 text-white hover:bg-white/20 disabled:pointer-events-none disabled:opacity-30";

  const details = (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <div className="flex items-baseline gap-2">
          <span
            className={cn(
              "font-display tabular rounded-md px-2 py-0.5 text-lg leading-tight font-bold",
              isFallback(detail)
                ? "bg-muted text-foreground"
                : "bg-tally text-tally-ink",
            )}
          >
            #{rank}
            {score != null && (
              <span className="ml-1.5 text-xs font-semibold opacity-80">
                {score}
              </span>
            )}
          </span>
          {length && (
            <span className="text-muted-foreground tabular text-sm">
              {length}
            </span>
          )}
        </div>
        <h2 className="font-display text-lg leading-snug font-semibold">
          {title}
        </h2>
        <div>
          <FallbackTag detail={detail} />
          <PeakBadge detail={detail} />
        </div>
      </div>
      <div className="flex gap-2">
        <a
          href={download}
          download={`${String(rank).padStart(2, "0")} ${title}.mp4`}
          className="bg-muted hover:bg-accent press inline-flex h-10 items-center gap-2 rounded-lg px-3 text-sm font-medium pointer-coarse:h-11"
        >
          <Download className="size-4" aria-hidden />
          Download
        </a>
      </div>
      <div className="border-t pt-4">
        <PostPanel
          api={post}
          editable={editable}
          hasPost={Boolean(detail?.post)}
          readOnlyNote={
            list === "new"
              ? "Keep this set to edit the text and mark clips as posted."
              : "You can edit the text once the video is done."
          }
        />
      </div>
      {detail && (
        <div className="border-t pt-4">
          <ScoreDetails detail={detail} weight={replayWeight} />
        </div>
      )}
    </div>
  );

  return (
    <m.div
      ref={dialog}
      role="dialog"
      aria-modal="true"
      aria-label={`Clip ${rank} of ${clips.length}: ${title}`}
      initial={{ opacity: 0 }}
      animate={{
        opacity: 1,
        transition: { duration: duration.enter, ease: ease.enter },
      }}
      exit={{
        opacity: 0,
        transition: { duration: duration.exit, ease: ease.exit },
      }}
      // Always dark, whatever the theme: a screening room for the clip.
      className="dark text-foreground fixed inset-0 z-50 bg-black"
    >
      {/* Top bar: where you are, previous and next, close. */}
      <div className="absolute inset-x-0 top-0 z-20 flex items-center justify-between gap-2 bg-gradient-to-b from-black/80 to-transparent p-3 sm:p-4">
        <span className="tabular px-1 text-sm text-white/80">
          {index + 1} of {clips.length}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Previous clip"
            disabled={!hasPrevious}
            onClick={() => onGo(index - 1)}
            className={round}
          >
            <ChevronUp className="size-5" aria-hidden />
          </button>
          <button
            type="button"
            aria-label="Next clip"
            disabled={!hasNext}
            onClick={() => onGo(index + 1)}
            className={round}
          >
            <ChevronDown className="size-5" aria-hidden />
          </button>
          <button
            ref={closeButton}
            type="button"
            aria-label="Close"
            onClick={onClose}
            className={round}
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>
      </div>

      <div className="flex h-full w-full items-center justify-center sm:gap-8 sm:px-6 sm:py-20">
        {/* The clip, as tall as the screen allows. */}
        <div
          ref={stage}
          style={{ transformOrigin: "0 0" }}
          className="relative aspect-[9/16] h-full max-w-full overflow-hidden bg-black sm:rounded-xl"
        >
          <video
            key={clip.name}
            ref={video}
            src={src}
            poster={src.replace(/\.mp4\?/, ".jpg?")}
            playsInline
            preload="auto"
            aria-label={title}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onEnded={() => setPlaying(false)}
            onLoadedMetadata={(e) => setTotal(e.currentTarget.duration)}
            onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
            onPointerDown={(e) => {
              touch.current = { y: e.clientY, x: e.clientX };
            }}
            onPointerUp={(e) => {
              const start = touch.current;
              touch.current = null;
              if (!start) return;
              const dy = e.clientY - start.y;
              if (e.pointerType !== "mouse" && Math.abs(dy) > 60) {
                if (dy < 0 && hasNext) onGo(index + 1);
                else if (dy > 0 && hasPrevious) onGo(index - 1);
                return;
              }
              if (Math.abs(dy) < 12) toggle();
            }}
            className="size-full touch-none object-contain"
          />
          {!playing && (
            <button
              type="button"
              aria-label="Play"
              onClick={toggle}
              className="pop-in absolute top-1/2 left-1/2 inline-flex size-16 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-black/55 text-white"
            >
              <Play className="ml-1 size-7" aria-hidden />
            </button>
          )}
          {/* Player controls along the bottom of the clip. */}
          <div className="absolute inset-x-0 bottom-0 z-10 flex items-center gap-2 bg-gradient-to-t from-black/80 to-transparent px-2 pt-8 pb-2 text-white max-sm:pb-24">
            <button
              type="button"
              aria-label={playing ? "Pause" : "Play"}
              onClick={toggle}
              className="press inline-flex size-10 items-center justify-center rounded-full hover:bg-white/15 pointer-coarse:size-11"
            >
              {playing ? (
                <Pause className="size-5" aria-hidden />
              ) : (
                <Play className="size-5" aria-hidden />
              )}
            </button>
            <input
              type="range"
              min={0}
              max={total || 0}
              step={0.1}
              value={Math.min(time, total || 0)}
              aria-label="Position in the clip"
              aria-valuetext={`${timecode(time)} of ${timecode(total)}`}
              onChange={(e) => {
                const v = video.current;
                if (v) v.currentTime = Number(e.target.value);
              }}
              className="accent-tally h-1 min-w-0 flex-1 cursor-pointer"
            />
            <span className="tabular shrink-0 text-xs text-white/80">
              {timecode(time)} / {timecode(total)}
            </span>
            <button
              type="button"
              aria-label={muted ? "Turn sound on" : "Mute"}
              onClick={toggleMute}
              className="press inline-flex size-10 items-center justify-center rounded-full hover:bg-white/15 pointer-coarse:size-11"
            >
              {muted ? (
                <VolumeX className="size-5" aria-hidden />
              ) : (
                <Volume2 className="size-5" aria-hidden />
              )}
            </button>
          </div>
        </div>

        {/* Wide screens: the details beside the clip. */}
        <aside className="bg-card hidden max-h-full w-80 shrink-0 overflow-y-auto rounded-xl border p-5 sm:block">
          {details}
        </aside>
      </div>

      {/* Phones: the title and a Details sheet over the bottom of the clip. */}
      <div className="absolute inset-x-0 bottom-0 z-20 sm:hidden">
        <AnimatePresence initial={false} mode="wait">
          {sheet ? (
            <m.div
              key="sheet"
              initial={{ y: 40, opacity: 0 }}
              animate={{
                y: 0,
                opacity: 1,
                transition: { duration: duration.enter, ease: ease.enter },
              }}
              exit={{
                y: 40,
                opacity: 0,
                transition: { duration: duration.exit, ease: ease.exit },
              }}
              className="bg-card max-h-[70vh] overflow-y-auto rounded-t-2xl border-t p-4 pb-6"
            >
              <button
                type="button"
                onClick={() => setSheet(false)}
                className="text-muted-foreground mb-3 inline-flex min-h-11 items-center gap-1.5 text-sm font-medium"
              >
                <ChevronDown className="size-4" aria-hidden />
                Hide details
              </button>
              {details}
            </m.div>
          ) : (
            <m.div
              key="bar"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="flex items-center justify-between gap-3 p-3 pb-5"
            >
              <p className="line-clamp-2 text-sm font-medium text-white">
                <span className="text-tally mr-1.5 font-bold">#{rank}</span>
                {title}
              </p>
              <button
                type="button"
                onClick={() => setSheet(true)}
                className="inline-flex h-11 shrink-0 items-center gap-1.5 rounded-full bg-white/15 px-4 text-sm font-medium text-white"
              >
                <Info className="size-4" aria-hidden />
                Details
              </button>
            </m.div>
          )}
        </AnimatePresence>
      </div>

      {/* The next clip's first seconds load while this one plays. */}
      {next && (
        <video
          aria-hidden
          tabIndex={-1}
          muted
          preload="metadata"
          src={media(next.name, next.version)}
          className="hidden"
        />
      )}
    </m.div>
  );
}
