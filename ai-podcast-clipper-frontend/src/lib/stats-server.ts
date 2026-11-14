import { readSettings } from "./app-settings";
import { todayUsdInr } from "./fx";
import { listJobs } from "./jobs";
import { readLedger } from "./ledger";
import { readModalBill } from "./modal-bill";
import { computeStats, type Period, type Stats } from "./stats";

/** Everything the stats page shows, for one period. */
export async function loadStats(period: Period = "month"): Promise<Stats> {
  const [ledger, jobs, settings, rate, { bill, refreshing }] =
    await Promise.all([
      readLedger(),
      listJobs(),
      readSettings(),
      todayUsdInr(),
      readModalBill(),
    ]);
  return computeStats(ledger, jobs, {
    period,
    currency: settings.currency,
    rate,
    bill,
    billRefreshing: refreshing,
  });
}
