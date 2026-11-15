"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Keeps data fresh from a JSON endpoint:
 * - asks with If-None-Match, and a 304 changes nothing (no state update, no re-render);
 * - does nothing while the tab is hidden, and fetches at once when it is shown again;
 * - ignores a slow answer that arrives after a newer one;
 * - fetches right away when `url` changes.
 * The interval only runs while `intervalMs` is a number; the visibility refresh runs
 * either way. Returns `refresh`, which fetches now (also in a hidden tab).
 */
export function usePolling<T>({
  url,
  intervalMs,
  onData,
}: {
  url: string;
  /** null: no interval (still refreshes when the tab becomes visible). */
  intervalMs: number | null;
  onData: (data: T) => void;
}) {
  const etag = useRef<{ url: string; value: string } | null>(null);
  const latest = useRef(0);
  const handler = useRef(onData);
  handler.current = onData;

  const fetchNow = useCallback(
    async (onlyIfVisible: boolean) => {
      if (onlyIfVisible && document.visibilityState !== "visible") return;
      const ticket = ++latest.current;
      const headers: Record<string, string> = {};
      if (etag.current?.url === url)
        headers["If-None-Match"] = etag.current.value;
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
        : setInterval(() => void fetchNow(true), intervalMs);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      if (timer) clearInterval(timer);
    };
  }, [fetchNow, intervalMs]);

  return useCallback(() => fetchNow(false), [fetchNow]);
}
