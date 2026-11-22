import type { Money } from "./format";
import type { JobCost, LedgerRun } from "./job-types";

export const num = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
export const sum = (values: number[]) => values.reduce((a, b) => a + b, 0);
export const mean = (values: number[]) =>
  values.length ? sum(values) / values.length : null;

export function addMoney(values: (Money | null)[]): Money | null {
  const present = values.filter((v): v is Money => v !== null);
  if (!present.length) return null;
  const inr = present.every((v) => v.inr !== null)
    ? sum(present.map((v) => v.inr ?? 0))
    : null;
  return { usd: sum(present.map((v) => v.usd)), inr };
}

export function scaleMoney(value: Money | null, factor: number): Money | null {
  return (
    value && {
      usd: value.usd * factor,
      inr: value.inr === null ? null : value.inr * factor,
    }
  );
}

export const madeClips = (run: LedgerRun) =>
  run.status === "done" && (run.clips ?? 0) > 0;

/** USD of a run's Gemini calls (0 when none were recorded). */
export const geminiUsd = (cost: JobCost | null | undefined) =>
  num(cost?.gemini?.usd) ?? 0;

/** USD of a whole run: Modal plus Gemini. */
export const runTotalUsd = (cost: JobCost) => cost.usd + geminiUsd(cost);
