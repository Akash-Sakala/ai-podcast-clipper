import { DashboardClient } from "~/components/dashboard-client";
import NavHeader from "~/components/nav-header";
import { Toaster } from "~/components/ui/sonner";
import { JOBS_DIR, listJobs } from "~/lib/jobs";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const jobs = await listJobs();

  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="container mx-auto flex-1 py-6">
        <DashboardClient initialJobs={jobs} jobsDir={JOBS_DIR} />
      </main>
      <Toaster />
    </div>
  );
}
