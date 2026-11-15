"use client";

import { useEffect, useState } from "react";

/**
 * A shared clock for relative times and elapsed timers. One interval serves every
 * component: each second while any of them wants a live timer (`live`), otherwise
 * every 30 s (enough for "5 min ago"). It stops while the tab is hidden and catches
 * up when the tab is shown again.
 */
const SLOW_MS = 30_000;
const FAST_MS = 1_000;

type Listener = { live: boolean; set: (now: number) => void };
const listeners = new Set<Listener>();
let timer: ReturnType<typeof setInterval> | null = null;
let timerMs = 0;

function tick() {
  if (document.visibilityState !== "visible") return;
  const now = Date.now();
  for (const listener of listeners) listener.set(now);
}

function reschedule() {
  const wanted =
    listeners.size === 0
      ? 0
      : [...listeners].some((l) => l.live)
        ? FAST_MS
        : SLOW_MS;
  if (wanted === timerMs) return;
  if (timer) clearInterval(timer);
  timer = wanted ? setInterval(tick, wanted) : null;
  timerMs = wanted;
}

if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", tick);
}

/** The current time; re-renders the caller every second when `live`, else every 30 s. */
export function useNow(live = false) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const listener: Listener = { live, set: setNow };
    listeners.add(listener);
    reschedule();
    return () => {
      listeners.delete(listener);
      reschedule();
    };
  }, [live]);
  return now;
}
