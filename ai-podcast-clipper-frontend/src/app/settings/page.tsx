import type { Metadata } from "next";
import NavHeader from "~/components/nav-header";
import { SettingsPage } from "~/components/settings-page";
import { Toaster } from "~/components/ui/sonner";
import { readSettings } from "~/lib/app-settings";
import { todayUsdInr } from "~/lib/fx";
import { keptVideosSummary } from "~/lib/video-store";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Settings | Podcast Clipper" };

export default async function SettingsRoute() {
  const [settings, rate, kept] = await Promise.all([
    readSettings(),
    todayUsdInr(),
    keptVideosSummary(),
  ]);
  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="flex-1">
        <SettingsPage
          initialSettings={settings}
          rate={rate}
          initialKept={kept}
        />
      </main>
      <Toaster />
    </div>
  );
}
