"use client";

import { usePathname } from "next/navigation";
import { useAllJobs, useJobsSync } from "~/lib/job-sync";
import { useRunReport } from "~/lib/run-signal";

/**
 * Keeps the tab's jobs fresh on every page (lib/job-sync.ts) and feeds them to the
 * run signal, so the header logo, the tab title and the "done" notification follow
 * every running video on every page. Mounted once, in the layout; renders nothing.
 */
export function JobsSync() {
  useJobsSync(usePathname());
  useRunReport(useAllJobs());
  return null;
}
