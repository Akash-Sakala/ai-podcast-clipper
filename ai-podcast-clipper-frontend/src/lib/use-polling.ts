"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Keeps data fresh from a JSON endpoint:
 * - asks with If-None-Match, and a 304 changes nothing (no state update, no re-render);
 * - does nothing while the tab is hidden, and fetches at once when it is shown again;
 * - ignores a slow answer that arrives after a newer one;
 * - skips a timer tick while the last request is still waiting, so a slow server
 *   never builds up a queue of polls;
 * - fetches right away when `url` changes.
 * The interval only runs while `intervalMs` is a number; the visibility refresh runs
 * either way. `hiddenIntervalMs` (optional) keeps a slow poll going while the tab is
 * hidden (browsers may stretch it to a minute). Returns `refresh`, which fetches now
 * (also in a hidden tab).
 */
export function usePolling<T>({
  url,
  intervalMs,
  hiddenIntervalMs = null,
  onData,
}: {
  url: string;
  /** null: no interval (still refreshes when the tab becomes visible). */
  intervalMs: number | null;
  /** null (default): nothing while the tab is hidden. */
  hiddenIntervalMs?: number | null;
  onData: (data: T) => void;
}) {
  const etag = useRef<{ url: string; value: string } | null>(null);
  const latest = useRef(0);
  const handler = useRef(onData);
  handler.current = onData;

  const inFlight = useRef(0);

  const fetchNow = useCallback(
    async (onlyIfVisible: boolean, skipIfBusy = false) => {
      if (onlyIfVisible && document.visibilityState !== "visible") return;
      // A timer tick waits for the last answer: a slow server never gets a queue.
      if (skipIfBusy && inFlight.current > 0) return;
      const ticket = ++latest.current;
      const headers: Record<string, string> = {};
      if (etag.current?.url === url)
        headers["If-None-Match"] = etag.current.value;
      inFlight.current += 1;
      try {
        const response = await fetch(url, { cache: "no-store", headers });
        if (response.status === 304 || !response.ok) return;
        const tag = response.headers.get("etag");
        const data = (await response.json()) as T;
        if (ticket !== latest.current) return; // a newer request has started
        etag.current = tag ? { url, value: tag } : null;
        handler.current(data);
      } catch {
        // The dev server may be restarting; the next poll tries again.
      } finally {
        inFlight.current -= 1;
      }
    },
    [url],
  );

  // A new address (for example another period) is fetched immediately, not at the next tick.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void fetchNow(false);
  }, [fetchNow]);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void fetchNow(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    const timer =
      intervalMs === null
        ? null
        : setInterval(() => void fetchNow(true, true), intervalMs);
    // A hidden tab still hears about a run finishing (tab title, notification), slowly.
    const hiddenTimer =
      hiddenIntervalMs === null
        ? null
        : setInterval(() => {
            if (document.visibilityState === "hidden")
              void fetchNow(false, true);
          }, hiddenIntervalMs);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      if (timer) clearInterval(timer);
      if (hiddenTimer) clearInterval(hiddenTimer);
    };
  }, [fetchNow, intervalMs, hiddenIntervalMs]);

  return useCallback(() => fetchNow(false), [fetchNow]);
}
