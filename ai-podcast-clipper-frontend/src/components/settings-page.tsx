"use client";

import { Check, Loader2, Minus, Plus, Trash2 } from "lucide-react";
import { AnimatePresence, m } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import type { UsdInr } from "~/lib/job-types";
import { duration, ease } from "~/lib/motion";
import {
  type AppSettings,
  DEFAULT_SETTINGS,
  MAX_CLIPS_RANGE,
  REPLAY_WEIGHT_MAX,
} from "~/lib/settings-types";
import { cn } from "~/lib/utils";
import type { KeptVideos } from "~/lib/video-store";
import { Segmented } from "./ui/segmented";

type Key = keyof AppSettings;

/** What the last save did, shown next to the rows it touched. */
type SaveStatus = {
  keys: Key[];
  state: "saved" | "error";
  change: Partial<AppSettings>;
  at: number;
};

/** How long "Saved" stays next to a row. */
const SAVED_FOR_MS = 1600;

const NAMES: Record<Key, string> = {
  currency: "Currency",
  notifyWhenDone: "Notifications",
  maxClips: "Clips per video",
  mostReplayed: "Most replayed",
  replayWeight: "Audience weight",
  sponsorBlock: "SponsorBlock",
};

/** "Clips per video, 6" or "Most replayed, on": what a screen reader hears after a save. */
function spoken(key: Key, settings: AppSettings) {
  const value = settings[key];
  if (typeof value === "boolean")
    return `${NAMES[key]}, ${value ? "on" : "off"}`;
  if (key === "replayWeight")
    return `${NAMES[key]}, ${Math.round(settings.replayWeight * 100)}%`;
  if (key === "currency")
    return `${NAMES[key]}, ${settings.currency === "inr" ? "rupees" : "dollars"}`;
  return `${NAMES[key]}, ${String(value)}`;
}

export function SettingsPage({
  initialSettings,
  rate,
  initialKept,
}: {
  initialSettings: AppSettings;
  rate: UsdInr | null;
  initialKept: KeptVideos;
}) {
  const [settings, setSettings] = useState(initialSettings);
  /** What the server last confirmed: a failed save puts its rows back to this. */
  const saved = useRef(initialSettings);
  const [status, setStatus] = useState<SaveStatus | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pending = useRef<Partial<AppSettings>>({});

  // Saves shortly after the last change (the slider fires many changes).
  const update = (change: Partial<AppSettings>) => {
    setSettings((s) => ({ ...s, ...change }));
    pending.current = { ...pending.current, ...change };
    setStatus(null);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(), 400);
  };

  const save = async () => {
    const change = pending.current;
    pending.current = {};
    const keys = Object.keys(change) as Key[];
    const at = Date.now();
    try {
      const response = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
      });
      if (!response.ok) throw new Error(String(response.status));
      const confirmed = (await response.json()) as AppSettings;
      saved.current = confirmed;
      // Newer changes made while this request was in flight stay as they are.
      if (Object.keys(pending.current).length === 0) setSettings(confirmed);
      setStatus({ keys, state: "saved", change, at });
      setAnnouncement(
        `Saved: ${keys.map((key) => spoken(key, confirmed)).join("; ")}.`,
      );
      window.setTimeout(
        () => setStatus((s) => (s?.at === at ? null : s)),
        SAVED_FOR_MS,
      );
    } catch {
      // The rows go back to what is really saved, and say so next to them.
      const back: Partial<AppSettings> = {};
      for (const key of keys)
        Object.assign(back, { [key]: saved.current[key] });
      setSettings((s) => ({ ...s, ...back }));
      setStatus({ keys, state: "error", change, at });
      setAnnouncement(
        `Couldn't save ${keys.map((key) => NAMES[key]).join(", ")}. Is the app still running?`,
      );
    }
  };

  /** The mark for the row of `key`: "Saved" for a moment, or the error with Retry. */
  const statusFor = (key: Key) =>
    status?.keys.includes(key) ? (
      <SaveMark
        key={`${status.at}-${status.state}`}
        state={status.state}
        onRetry={() => update(status.change)}
      />
    ) : null;

  // Leaving the page (a link, the back button, closing the tab) must not lose a change
  // made in the last 400 ms: send what is pending with keepalive, which outlives the page.
  useEffect(() => {
    const flush = () => {
      if (timer.current) clearTimeout(timer.current);
      const change = pending.current;
      if (Object.keys(change).length === 0) return;
      pending.current = {};
      void fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
        keepalive: true,
      }).catch(() => undefined);
    };
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, []);

  // The browser asks for permission only when the switch is turned on, never on load.
  const [blocked, setBlocked] = useState<"blocked" | "unsupported" | null>(
    null,
  );
  useEffect(() => {
    if (!("Notification" in window)) setBlocked("unsupported");
    else if (Notification.permission === "denied") setBlocked("blocked");
  }, []);
  const setNotify = async (on: boolean) => {
    if (!on) return update({ notifyWhenDone: false });
    if (!("Notification" in window)) return setBlocked("unsupported");
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return setBlocked("blocked");
    setBlocked(null);
    update({ notifyWhenDone: true });
  };
  const notifyText =
    blocked === "unsupported"
      ? "This browser can't show notifications."
      : blocked === "blocked"
        ? "Notifications are blocked for this site. Allow them in the browser's site settings, then turn this on."
        : "A browser notification when a video finishes or fails while Podcast Clipper is open in a background tab.";

  const weight = Math.round(settings.replayWeight * 100);
  const rateText = rate
    ? `Today $1 = ₹${rate.rate.toFixed(2)}${rate.stale ? ` (saved on ${rate.date ?? "an earlier day"})` : ""}, rates by ${rate.source === "exchangerate-api" ? "ExchangeRate-API (exchangerate-api.com)" : "Frankfurter (frankfurter.dev)"}.`
    : "The exchange rate could not be read right now; costs show in dollars until it can.";

  return (
    <div className="mx-auto w-full max-w-2xl space-y-5 px-4 py-8 sm:px-6 sm:py-12">
      <div className="space-y-1.5">
        <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
          Settings
        </h1>
        <p className="text-muted-foreground text-sm">
          Changes are saved as you make them and apply to your next video or
          re-rank.
        </p>
      </div>

      <Group title="Display">
        <Row
          title="Currency"
          description={`Rupee costs use the dollar rate on the day each run finished. ${rateText}`}
          stackOnPhone
          status={statusFor("currency")}
        >
          <Segmented
            label="Currency"
            value={settings.currency}
            options={[
              ["usd", "Dollars ($)"],
              ["inr", "Rupees (₹)"],
            ]}
            onChange={(currency) => update({ currency })}
          />
        </Row>
      </Group>

      <Group title="Notifications">
        <Row
          title="Notify me when a video is done"
          description={notifyText}
          status={statusFor("notifyWhenDone")}
        >
          <Toggle
            label="Notify me when a video is done"
            checked={settings.notifyWhenDone}
            onChange={(on) => void setNotify(on)}
          />
        </Row>
      </Group>

      <Group title="Choosing clips">
        <Row
          title="Clips per video"
          description="How many clips a new video or re-rank makes."
          status={statusFor("maxClips")}
        >
          <Stepper
            label="Clips per video"
            value={settings.maxClips}
            min={MAX_CLIPS_RANGE[0]}
            max={MAX_CLIPS_RANGE[1]}
            onChange={(maxClips) => update({ maxClips })}
          />
        </Row>
        <Row
          title="Most replayed"
          description="Use the parts YouTube viewers rewatch most as clip candidates."
          status={statusFor("mostReplayed")}
        >
          <Toggle
            label="Most replayed"
            checked={settings.mostReplayed}
            onChange={(on) => update({ mostReplayed: on })}
          />
        </Row>
        <Row
          title="Audience weight"
          description={
            settings.mostReplayed
              ? `How much rewatching counts in a clip's score. At ${weight}%, the score is ${100 - weight}% Gemini and ${weight}% viewers.`
              : "Turn on Most replayed to use it."
          }
          muted={!settings.mostReplayed}
          stackOnPhone
          id="audience-weight"
          status={statusFor("replayWeight")}
        >
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={0}
              max={REPLAY_WEIGHT_MAX * 100}
              step={5}
              value={weight}
              disabled={!settings.mostReplayed}
              aria-label="Audience weight"
              // Read with the slider: what it does, or why it is off.
              aria-describedby="audience-weight-description"
              onChange={(e) =>
                update({ replayWeight: Number(e.target.value) / 100 })
              }
              className="accent-tally w-40 disabled:opacity-40"
            />
            <span className="tabular w-10 text-right text-sm font-semibold">
              {weight}%
            </span>
          </div>
        </Row>
        <Row
          title="SponsorBlock"
          description="Keep sponsor reads, intros, outros and previews out of clips."
          status={statusFor("sponsorBlock")}
        >
          <Toggle
            label="SponsorBlock"
            checked={settings.sponsorBlock}
            onChange={(on) => update({ sponsorBlock: on })}
          />
        </Row>
      </Group>

      <Group title="Storage">
        <KeptVideosRow initial={initialKept} />
      </Group>

      <div className="text-muted-foreground flex items-center justify-end px-1 text-sm">
        {/* One place where screen readers hear every save (the marks are visual). */}
        <p className="sr-only" aria-live="polite">
          {announcement}
        </p>
        <button
          type="button"
          onClick={() =>
            update({
              ...DEFAULT_SETTINGS,
              currency: settings.currency,
              notifyWhenDone: settings.notifyWhenDone,
            })
          }
          className="hover:text-foreground underline underline-offset-4 pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center"
        >
          Reset clip settings
        </button>
      </div>
    </div>
  );
}

function Group({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h2 className="text-muted-foreground px-1 text-sm font-medium">
        {title}
      </h2>
      <div className="bg-card divide-y rounded-xl border px-4 sm:px-5">
        {children}
      </div>
    </section>
  );
}

function Row({
  title,
  description,
  children,
  muted,
  stackOnPhone,
  id,
  status,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  muted?: boolean;
  stackOnPhone?: boolean;
  /** Gives the description the id `<id>-description`, for aria-describedby. */
  id?: string;
  /** The save mark ("Saved" or an error), shown beside the control. */
  status?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex justify-between gap-4 py-4",
        stackOnPhone ? "flex-col sm:flex-row sm:items-center" : "items-center",
      )}
    >
      <div className={cn("min-w-0 space-y-0.5", muted && "opacity-60")}>
        <div className="text-sm font-medium">{title}</div>
        <p
          id={id ? `${id}-description` : undefined}
          className="text-muted-foreground text-sm leading-relaxed"
        >
          {description}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <AnimatePresence>{status}</AnimatePresence>
        {children}
      </div>
    </div>
  );
}

/**
 * Beside a control after a save: "Saved" with a check for a moment, then gone; or,
 * when the save failed, what happened and Retry (the control is back to the saved
 * value). Visual only: the page's live region says the same to screen readers.
 */
function SaveMark({
  state,
  onRetry,
}: {
  state: "saved" | "error";
  onRetry: () => void;
}) {
  return (
    <m.span
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{
        opacity: 1,
        scale: 1,
        transition: { duration: duration.enter, ease: ease.enter },
      }}
      exit={{
        opacity: 0,
        transition: { duration: duration.emphasis, ease: ease.exit },
      }}
      className={cn(
        "inline-flex items-center gap-1 text-xs whitespace-nowrap",
        state === "saved" ? "text-muted-foreground" : "text-destructive",
      )}
    >
      {state === "saved" ? (
        <>
          <Check className="text-foreground size-3.5" aria-hidden />
          <span aria-hidden>Saved</span>
        </>
      ) : (
        <>
          <span aria-hidden>Couldn&apos;t save.</span>
          <button
            type="button"
            onClick={onRetry}
            className="text-foreground press rounded underline underline-offset-4 pointer-coarse:inline-flex pointer-coarse:min-h-11 pointer-coarse:items-center"
          >
            Retry
          </button>
        </>
      )}
    </m.span>
  );
}

/** The number rolls the way it changed: up for more, down for fewer. */
const roll = {
  enter: (direction: number) => ({ y: direction * 10, opacity: 0 }),
  center: {
    y: 0,
    opacity: 1,
    transition: { duration: duration.enter, ease: ease.enter },
  },
  exit: (direction: number) => ({
    y: direction * -10,
    opacity: 0,
    transition: { duration: duration.exit, ease: ease.exit },
  }),
};

/**
 * A number between `min` and `max` with minus and plus buttons. The number itself is a
 * spinbutton: arrow keys step it, Home and End jump to the ends.
 */
function Stepper({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  const [direction, setDirection] = useState(1);
  const set = (next: number) => {
    if (next < min || next > max || next === value) return;
    setDirection(next > value ? 1 : -1);
    onChange(next);
  };
  const step: Record<string, number> = {
    ArrowUp: value + 1,
    ArrowRight: value + 1,
    ArrowDown: value - 1,
    ArrowLeft: value - 1,
    Home: min,
    End: max,
  };
  const button =
    "text-muted-foreground hover:text-foreground press inline-flex size-8 items-center justify-center disabled:opacity-40 pointer-coarse:size-11";
  return (
    <div className="inline-flex items-center rounded-lg border">
      <button
        type="button"
        aria-label="Fewer clips"
        disabled={value <= min}
        onClick={() => set(value - 1)}
        className={button}
      >
        <Minus className="size-4" aria-hidden />
      </button>
      <div
        role="spinbutton"
        tabIndex={0}
        aria-label={label}
        aria-valuenow={value}
        aria-valuemin={min}
        aria-valuemax={max}
        onKeyDown={(event) => {
          const next = step[event.key];
          if (next === undefined) return;
          event.preventDefault();
          set(next);
        }}
        className="tabular relative grid h-8 w-8 place-items-center overflow-hidden rounded text-sm font-semibold pointer-coarse:h-11"
      >
        <AnimatePresence initial={false} mode="popLayout" custom={direction}>
          <m.span
            key={value}
            custom={direction}
            variants={roll}
            initial="enter"
            animate="center"
            exit="exit"
          >
            {value}
          </m.span>
        </AnimatePresence>
      </div>
      <button
        type="button"
        aria-label="More clips"
        disabled={value >= max}
        onClick={() => set(value + 1)}
        className={button}
      >
        <Plus className="size-4" aria-hidden />
      </button>
    </div>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-10 shrink-0 items-center rounded-full transition-colors pointer-coarse:before:absolute pointer-coarse:before:-inset-2.5 pointer-coarse:before:content-['']",
        // On: the amber is only 2:1 against a white card, so a darker amber edge (the
        // ring colour, 3.8:1) outlines it; in dark mode the two are the same amber.
        checked ? "bg-tally ring-ring ring-1 ring-inset" : "bg-switch-off",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "inline-block size-[18px] rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-[19px]" : "translate-x-[3px]",
        )}
      />
    </button>
  );
}

function formatSize(bytes: number) {
  const gb = bytes / 1024 ** 3;
  if (gb >= 1) return `${gb.toFixed(1)} GB`;
  return `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;
}

function keptText(kept: KeptVideos) {
  const days = kept.keepDays === 1 ? "1 day" : `${kept.keepDays} days`;
  const rule = `A video is kept for ${days} after its last use, so a re-rank needs no new download.`;
  if (!kept.known) return "No videos kept yet.";
  if (kept.keepDays <= 0) return "Videos are not kept after a run.";
  if (kept.count === 0) return `No videos kept right now. ${rule}`;
  const count = kept.count === 1 ? "1 video" : `${kept.count} videos`;
  const cap = kept.maxGb > 0 ? ` of ${kept.maxGb} GB` : "";
  return `${count}, ${formatSize(kept.bytes)}${cap}. ${rule}`;
}

function KeptVideosRow({ initial }: { initial: KeptVideos }) {
  const [kept, setKept] = useState(initial);
  const [busy, setBusy] = useState(false);
  // Removing can't be undone, so the button asks once more, saying what goes.
  const [confirming, setConfirming] = useState(false);
  const keep = useRef<HTMLButtonElement>(null);
  const start = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (confirming) keep.current?.focus();
  }, [confirming]);
  const cancel = () => {
    setConfirming(false);
    window.setTimeout(() => start.current?.focus());
  };

  const refresh = async () => {
    const response = await fetch("/api/videos-kept", { cache: "no-store" });
    if (response.ok) setKept((await response.json()) as KeptVideos);
  };

  const remove = async () => {
    setConfirming(false);
    setBusy(true);
    try {
      const response = await fetch("/api/videos-kept", { method: "DELETE" });
      if (!response.ok) throw new Error(String(response.status));
      const { removed, bytes } = (await response.json()) as {
        removed: number;
        bytes: number;
      };
      toast.success(
        removed === 0
          ? "No videos removed"
          : `Removed ${removed === 1 ? "1 video" : `${removed} videos`}, ${formatSize(bytes)}`,
        removed === 0
          ? { description: "A video that is in use is kept." }
          : undefined,
      );
    } catch {
      toast.error("Couldn't remove the kept videos", {
        description: "Is the app still running?",
      });
    } finally {
      await refresh().catch(() => undefined);
      setBusy(false);
    }
  };

  return (
    <Row title="Kept videos" description={keptText(kept)} stackOnPhone>
      {confirming ? (
        <div
          role="group"
          aria-label="Remove the kept videos?"
          onKeyDown={(event) => {
            if (event.key === "Escape") cancel();
          }}
          className="pop-in flex flex-wrap items-center gap-2 sm:max-w-56 sm:justify-end"
        >
          <span className="text-sm">
            Remove {kept.count === 1 ? "1 video" : `${kept.count} videos`},{" "}
            {formatSize(kept.bytes)}?
          </span>
          <button
            type="button"
            onClick={() => void remove()}
            // Page-background ink on red: 4.7:1 light, 5.7:1 dark (white is 3.3:1 in dark).
            className="bg-destructive text-background press inline-flex h-8 items-center rounded-lg px-3 text-sm font-medium pointer-coarse:h-11"
          >
            Remove
          </button>
          <button
            ref={keep}
            type="button"
            onClick={cancel}
            className="text-foreground/80 hover:text-foreground hover:bg-muted press inline-flex h-8 items-center rounded-lg border px-3 text-sm font-medium pointer-coarse:h-11"
          >
            Keep them
          </button>
        </div>
      ) : (
        <button
          ref={start}
          type="button"
          onClick={() => setConfirming(true)}
          disabled={busy || kept.count === 0}
          className="text-foreground/80 hover:text-foreground hover:bg-muted press inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-sm font-medium whitespace-nowrap disabled:pointer-events-none disabled:opacity-50 pointer-coarse:h-11"
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin" aria-hidden />
          ) : (
            <Trash2 className="size-4" aria-hidden />
          )}
          Remove kept videos
        </button>
      )}
    </Row>
  );
}
