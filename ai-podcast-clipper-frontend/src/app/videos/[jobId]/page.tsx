import type { Metadata } from "next";
import { notFound } from "next/navigation";
import NavHeader from "~/components/nav-header";
import { Toaster } from "~/components/ui/sonner";
import { VideoPage } from "~/components/video-page";
import { getJob } from "~/lib/jobs";

export const dynamic = "force-dynamic";

type Params = { params: Promise<{ jobId: string }> };

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const job = await getJob((await params).jobId);
  return { title: job ? `${job.title} | Podcast Clipper` : "Podcast Clipper" };
}

export default async function VideoRoute({ params }: Params) {
  const job = await getJob((await params).jobId);
  if (!job) notFound();

  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="flex-1">
        <VideoPage initialJob={job} />
      </main>
      <Toaster />
    </div>
  );
}
