import { DashboardClient } from "~/components/dashboard-client";
import NavHeader from "~/components/nav-header";
import { Toaster } from "~/components/ui/sonner";
import { listJobs } from "~/lib/jobs";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const jobs = await listJobs();

  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="flex-1">
        <DashboardClient initialJobs={jobs} />
      </main>
      <Toaster />
    </div>
  );
}
