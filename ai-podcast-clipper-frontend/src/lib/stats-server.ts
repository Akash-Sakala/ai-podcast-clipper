import { readSettings } from "./app-settings";
import { todayUsdInr } from "./fx";
import { listJobBasics } from "./jobs";
import { readLedger } from "./ledger";
import { readModalBill } from "./modal-bill";
import { computeStats } from "./stats";
import type { Period, Stats } from "./stats-types";

/**
 * Everything the stats page shows, for one period. `runsLimit` is how many of the
 * newest runs are listed (the page asks for more with "Show more").
 */
export async function loadStats(
  period: Period = "month",
  runsLimit?: number,
): Promise<Stats> {
  const [ledger, jobs, settings, rate, { bill, refreshing }] =
    await Promise.all([
      readLedger(),
      listJobBasics(),
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
    runsLimit,
  });
}
