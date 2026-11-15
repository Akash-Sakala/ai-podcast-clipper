import fsp from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import type { UsdInr } from "./job-types";
import { STATS_DIR } from "./paths";

/**
 * Today's US dollar to Indian rupee rate, for the Modal bill and runs without their
 * own rate. Same sources and cache file as the backend's fx.py: Frankfurter, then
 * ExchangeRate-API's open endpoint, then the last saved rate. Free, no API key.
 *
 * Pages and APIs only ever READ the cache file (todayUsdInr is a file read). When the
 * rate is older than 6 hours a refresh runs in the background, never awaited by a
 * request; after a failed refresh the network is not tried again for 5 minutes.
 */
const CACHE = path.join(STATS_DIR, "fx.json");
const MAX_AGE_MS = 6 * 3600 * 1000;
const RETRY_AFTER_FAILURE_MS = 5 * 60 * 1000;

type Cached = UsdInr & { fetchedAt: string };

const plausible = (value: unknown) =>
  typeof value === "number" && value > 40 && value < 250 ? value : null;

async function getJson(url: string): Promise<unknown> {
  const response = await fetch(url, {
    signal: AbortSignal.timeout(8000),
    headers: { "User-Agent": "ai-podcast-clipper (personal use)" },
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function fromFrankfurter(): Promise<UsdInr | null> {
  const v2 = (await getJson(
    "https://api.frankfurter.dev/v2/rates?base=usd&quotes=inr",
  )) as { date?: string; rate?: number }[];
  const rate = plausible(Array.isArray(v2) ? v2[0]?.rate : null);
  return rate
    ? { rate, date: v2[0]?.date ?? null, source: "frankfurter" }
    : null;
}

async function fromOpenEr(): Promise<UsdInr | null> {
  const data = (await getJson("https://open.er-api.com/v6/latest/USD")) as {
    result?: string;
    time_last_update_unix?: number;
    rates?: Record<string, number>;
  };
  const rate = data.result === "success" ? plausible(data.rates?.INR) : null;
  if (!rate) return null;
  const date = new Date((data.time_last_update_unix ?? 0) * 1000)
    .toISOString()
    .slice(0, 10);
  return { rate, date, source: "exchangerate-api" };
}

// Shared by every route bundle (and dev hot reloads).
const state = ((
  globalThis as { __clipperFx?: { refreshing: boolean; failedAt: number } }
).__clipperFx ??= { refreshing: false, failedAt: 0 });

async function readCache(): Promise<Cached | null> {
  try {
    const cached = JSON.parse(await fsp.readFile(CACHE, "utf8")) as Cached;
    return plausible(cached.rate) ? cached : null;
  } catch {
    return null;
  }
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    for (const source of [fromFrankfurter, fromOpenEr]) {
      try {
        const found = await source();
        if (!found) continue;
        const saved: Cached = { ...found, fetchedAt: new Date().toISOString() };
        await fsp.mkdir(path.dirname(CACHE), { recursive: true });
        const tmp = `${CACHE}.${process.pid}.${randomBytes(3).toString("hex")}.tmp`;
        await fsp.writeFile(tmp, JSON.stringify(saved));
        await fsp.rename(tmp, CACHE);
        state.failedAt = 0;
        return;
      } catch {
        // Try the next source.
      }
    }
    state.failedAt = Date.now();
  } finally {
    state.refreshing = false;
  }
}

/**
 * The saved rate (null when none is saved yet, so costs show in dollars), flagged
 * `stale` when the last refresh failed. Starts a background refresh when it is old.
 */
export async function todayUsdInr(): Promise<UsdInr | null> {
  const cached = await readCache();
  const age = cached
    ? Date.now() - new Date(cached.fetchedAt).getTime()
    : Infinity;
  if (
    !(age < MAX_AGE_MS) &&
    Date.now() - state.failedAt > RETRY_AFTER_FAILURE_MS
  ) {
    void refresh();
  }
  if (!cached) return null;
  return state.failedAt && !(age < MAX_AGE_MS)
    ? { ...cached, stale: true }
    : cached;
}
