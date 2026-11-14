import type { Metadata } from "next";
import NavHeader from "~/components/nav-header";
import { SettingsPage } from "~/components/settings-page";
import { readSettings } from "~/lib/app-settings";
import { todayUsdInr } from "~/lib/fx";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Settings | Podcast Clipper" };

export default async function SettingsRoute() {
  const [settings, rate] = await Promise.all([readSettings(), todayUsdInr()]);
  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="flex-1">
        <SettingsPage initialSettings={settings} rate={rate} />
      </main>
    </div>
  );
}
