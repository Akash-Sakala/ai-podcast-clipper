import fsp from "node:fs/promises";
import path from "node:path";
import type { UsdInr } from "./job-types";
import { DATA_DIR } from "./jobs";

/**
 * Today's US dollar to Indian rupee rate, for the Modal bill and runs without their
 * own rate. Same sources and cache file as the backend's fx.py: Frankfurter, then
 * ExchangeRate-API's open endpoint, then the last saved rate. Free, no API key.
 */
const CACHE = path.join(DATA_DIR, "stats", "fx.json");
const MAX_AGE_MS = 6 * 3600 * 1000;

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

let pending: Promise<UsdInr | null> | null = null;

export async function todayUsdInr(): Promise<UsdInr | null> {
  let cached: Cached | null = null;
  try {
    cached = JSON.parse(await fsp.readFile(CACHE, "utf8")) as Cached;
    if (
      plausible(cached.rate) &&
      Date.now() - new Date(cached.fetchedAt).getTime() < MAX_AGE_MS
    )
      return cached;
  } catch {
    cached = null;
  }
  pending ??= (async () => {
    for (const source of [fromFrankfurter, fromOpenEr]) {
      try {
        const found = await source();
        if (found) {
          const saved: Cached = {
            ...found,
            fetchedAt: new Date().toISOString(),
          };
          await fsp.mkdir(path.dirname(CACHE), { recursive: true });
          await fsp.writeFile(CACHE, JSON.stringify(saved));
          return found;
        }
      } catch {
        // Try the next source.
      }
    }
    return cached && plausible(cached.rate) ? { ...cached, stale: true } : null;
  })().finally(() => {
    pending = null;
  });
  return pending;
}
