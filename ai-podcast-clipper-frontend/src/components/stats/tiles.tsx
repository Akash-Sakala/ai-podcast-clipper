"use client";

import {
  formatMoney,
  type Money,
  plural,
  stepTime,
  tokenCount,
} from "~/lib/format";
import type { Currency } from "~/lib/settings-types";
import { MODAL_MONTHLY_CREDIT, type Stats } from "~/lib/stats-types";
import { useTween } from "~/lib/use-tween";

/** An amount that glides to its new value when the period changes ("—" when none). */
export function MoneyValue({
  value,
  currency,
  period,
}: {
  value: Money | null;
  currency: Currency;
  period: string;
}) {
  const [usd, inr] = useTween(
    value ? [value.usd, value.inr ?? -1] : [],
    period,
  );
  if (!value || usd === undefined) return "—";
  return formatMoney(
    { usd, inr: value.inr === null ? null : (inr ?? 0) },
    currency,
  );
}

/** A duration that glides to its new value when the period changes. */
export function TimeValue({
  seconds,
  period,
}: {
  seconds: number;
  period: string;
}) {
  const [shown] = useTween([seconds], period);
  return stepTime(shown ?? seconds);
}

export function Tile({
  label,
  value,
  second,
  note,
}: {
  label: string;
  value: React.ReactNode;
  second?: React.ReactNode;
  note?: string;
}) {
  return (
    <div className="bg-card flex flex-col gap-1.5 rounded-xl border p-4 sm:p-5">
      <span className="text-muted-foreground text-sm">{label}</span>
      <span className="tabular flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-3xl font-semibold tracking-tight">{value}</span>
        {second && <span className="text-xl font-medium">{second}</span>}
      </span>
      {note && (
        <span className="text-muted-foreground text-xs leading-relaxed">
          {note}
        </span>
      )}
    </div>
  );
}

export function BillTile({ stats }: { stats: Stats }) {
  const bill = stats.bill;
  const credit0 =
    stats.currency === "inr" && stats.rate
      ? `₹${Math.round(MODAL_MONTHLY_CREDIT * stats.rate.rate).toLocaleString("en-IN")}`
      : `$${MODAL_MONTHLY_CREDIT}`;
  return (
    <div className="bg-card flex flex-col gap-1.5 rounded-xl border p-4 sm:p-5">
      <span className="text-muted-foreground text-sm">
        Modal bill{bill ? `, ${bill.monthLabel}` : ", this month"}
      </span>
      <span className="tabular flex flex-wrap items-baseline gap-x-2">
        <span className="text-3xl font-semibold tracking-tight">
          {bill ? formatMoney(bill.metered, stats.currency) : "—"}
        </span>
        <span className="text-muted-foreground text-sm">
          of {credit0} credit
        </span>
      </span>
      <span className="text-muted-foreground text-xs leading-relaxed">
        {bill
          ? `${formatMoney(bill.billed, stats.currency)} paid. From Modal's bill.`
          : stats.billRefreshing
            ? "Reading Modal's bill…"
            : "Modal's bill could not be read yet."}
      </span>
    </div>
  );
}

/** Estimated Gemini spend of the period, from the calls' token counts. */
export function GeminiTile({ stats }: { stats: Stats }) {
  const g = stats.gemini;
  const when =
    stats.period === "month" ? stats.periodLabel.split(" ")[0] : "all time";
  let note = "Token counts start with the next run.";
  if (g) {
    note = `${plural(g.calls, "call")}, ${tokenCount(g.tokensIn)} tokens in, ${tokenCount(g.tokensOut)} out. Estimated from ${plural(g.runs, "run")}.`;
    if (g.unpriced.length) note += ` No price for ${g.unpriced.join(", ")}.`;
  }
  return (
    <Tile
      label={`Gemini, ${when}`}
      value={
        <MoneyValue
          value={g?.spend ?? null}
          currency={stats.currency}
          period={stats.period}
        />
      }
      note={note}
    />
  );
}
