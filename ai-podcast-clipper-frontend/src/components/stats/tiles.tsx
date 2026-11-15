import { formatMoney } from "~/lib/format";
import { MODAL_MONTHLY_CREDIT, type Stats } from "~/lib/stats-types";

export function Tile({
  label,
  value,
  second,
  note,
}: {
  label: string;
  value: string;
  second?: string;
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
