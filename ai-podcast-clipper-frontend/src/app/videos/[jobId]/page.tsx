import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";
import NavHeader from "~/components/nav-header";
import { Toaster } from "~/components/ui/sonner";
import { VideoPage } from "~/components/video/video-page";
import { readSettings } from "~/lib/app-settings";
import { todayUsdInr } from "~/lib/fx";
import { getJob } from "~/lib/jobs";

// One read per request: generateMetadata and the page both ask for the same job.
const loadJob = cache((jobId: string) => getJob(jobId));

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ jobId: string }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const job = await loadJob((await params).jobId);
  return { title: job ? `${job.title} | Podcast Clipper` : "Podcast Clipper" };
}

export default async function VideoRoute({ params }: Params) {
  const job = await loadJob((await params).jobId);
  if (!job) notFound();
  const settings = await readSettings();
  const rate = settings.currency === "inr" ? await todayUsdInr() : null;

  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="flex-1">
        <VideoPage
          initialJob={job}
          currency={settings.currency}
          rate={rate?.rate ?? null}
        />
      </main>
      <Toaster />
    </div>
  );
}
