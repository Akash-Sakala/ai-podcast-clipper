"use client";

import { useEffect, useState } from "react";
import { displayTitle, plural } from "~/lib/format";
import {
  onRunFinished,
  useRunSignal,
  type FinishedRun,
} from "~/lib/run-signal";

const APP = "Podcast Clipper";
/** How long the tab says "Clips ready" (or "Failed") after a run ends. */
const FINISHED_FOR_MS = 30_000;

/** The tab title's first words while a run is going, or just after it ended. */
function titleText(
  active: { stage: string }[],
  finished: FinishedRun | null,
  now: number,
) {
  if (active.length === 1) return active[0]!.stage;
  if (active.length > 1) return `${plural(active.length, "video")} processing`;
  if (finished && now - finished.at < FINISHED_FOR_MS)
    return finished.status === "failed"
      ? "Failed"
      : finished.clipCount > 0
        ? "Clips ready"
        : "No clips";
  return null;
}

/** The notification's heading, in the same words as the toasts. */
function finishedHeading(run: FinishedRun) {
  if (run.status === "failed") return "A video failed";
  if (run.clipCount === 0) return "No clips came out of a video";
  return run.clipCount === 1 ? "1 clip ready" : `${run.clipCount} clips ready`;
}

/**
 * Side effects of the run signal, mounted once for the whole app:
 * - the tab title shows the stage ("Rendering, 2 of 5 ready, Podcast Clipper") while a
 *   run is going and "Clips ready" for 30 s after it ends, then the page's own title;
 * - with Settings > "Notify me when a video is done" on, a browser notification when a
 *   run ends while this tab is in the background.
 */
export function RunSignalEffects() {
  const { active, finished } = useRunSignal();
  const [now, setNow] = useState(() => Date.now());

  // Ends the "Clips ready" title once its 30 s are up.
  useEffect(() => {
    if (!finished) return;
    setNow(Date.now());
    const left = finished.at + FINISHED_FOR_MS - Date.now();
    if (left <= 0) return;
    const timer = window.setTimeout(() => setNow(Date.now()), left + 50);
    return () => window.clearTimeout(timer);
  }, [finished]);

  const text = titleText(active, finished, now);

  // The page's own title (Next.js sets it on every navigation) is kept as the base: a
  // title change that isn't ours becomes the new base and ours goes back on top.
  useEffect(() => {
    if (!text) return;
    let base = document.title;
    const ours = () => `${text}, ${APP}`;
    const apply = () => {
      if (document.title !== ours()) document.title = ours();
    };
    apply();
    const titleElement = document.querySelector("title");
    const observer = new MutationObserver(() => {
      if (document.title === ours()) return;
      base = document.title;
      apply();
    });
    if (titleElement)
      observer.observe(titleElement, {
        childList: true,
        characterData: true,
        subtree: true,
      });
    return () => {
      observer.disconnect();
      if (document.title === ours()) document.title = base;
    };
  }, [text]);

  useEffect(
    () =>
      onRunFinished((run) => {
        if (document.visibilityState === "visible" && document.hasFocus())
          return;
        void notify(run);
      }),
    [],
  );

  return null;
}

async function notify(run: FinishedRun) {
  if (!("Notification" in window) || Notification.permission !== "granted")
    return;
  try {
    const response = await fetch("/api/settings", { cache: "no-store" });
    const settings = (await response.json()) as { notifyWhenDone?: boolean };
    if (!settings.notifyWhenDone) return;
  } catch {
    return;
  }
  const notification = new Notification(finishedHeading(run), {
    body: displayTitle(run),
    // One notification per run, even with several app tabs open.
    tag: `finished-${run.id}`,
    icon: "/favicon.ico",
  });
  notification.onclick = () => {
    window.focus();
    window.location.href = `/videos/${run.id}`;
    notification.close();
  };
}
