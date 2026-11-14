import type { Metadata } from "next";
import NavHeader from "~/components/nav-header";
import { StatsPage } from "~/components/stats-page";
import { loadStats } from "~/lib/stats-server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Stats | Podcast Clipper" };

export default async function StatsRoute() {
  const stats = await loadStats("month");

  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="flex-1">
        <StatsPage initialStats={stats} />
      </main>
    </div>
  );
}
