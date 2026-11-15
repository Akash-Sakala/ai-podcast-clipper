import NavHeader from "./nav-header";
import { cn } from "~/lib/utils";

/** Grey placeholder block; pulses gently (and not at all with reduced motion). */
function Bone({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn("bg-muted tally-pulse rounded-md", className)}
    />
  );
}

function Frame({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="flex-1" aria-busy="true" aria-label={label}>
        {children}
      </main>
    </div>
  );
}

/** Home: the headline, the input bar and a few rows of the video list. */
export function HomeSkeleton() {
  return (
    <Frame label="Loading your videos">
      <div className="mx-auto w-full max-w-4xl space-y-10 px-4 py-8 sm:px-6 sm:py-12">
        <div className="space-y-4">
          <Bone className="h-9 w-3/4 sm:h-10" />
          <Bone className="h-[3.75rem] w-full rounded-xl" />
        </div>
        <div className="space-y-3">
          <Bone className="h-4 w-24" />
          <ul className="bg-card divide-y overflow-hidden rounded-xl border">
            {[0, 1, 2].map((i) => (
              <li
                key={i}
                className="flex items-center gap-3 px-3 py-3 sm:gap-4 sm:px-4"
              >
                <Bone className="aspect-video w-24 shrink-0 sm:w-36" />
                <div className="flex-1 space-y-2.5">
                  <Bone className="h-4 w-4/5" />
                  <Bone className="h-3.5 w-1/3" />
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Frame>
  );
}

/** A video page: back link, title and a row of 9:16 clip tiles. */
export function VideoSkeleton() {
  return (
    <Frame label="Loading clips">
      <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
        <Bone className="h-8 w-28" />
        <div className="mt-3 mb-6 space-y-2">
          <Bone className="h-8 w-2/3 sm:h-9" />
          <Bone className="h-4 w-24" />
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-7 sm:grid-cols-3 lg:grid-cols-5">
          {Array.from({ length: 5 }, (_, i) => (
            <div
              key={i}
              className={cn(
                "space-y-3",
                i > 1 && "hidden sm:block",
                i > 2 && "sm:hidden lg:block",
              )}
            >
              <Bone className="aspect-[9/16] w-full rounded-lg" />
              <Bone className="h-4 w-4/5" />
            </div>
          ))}
        </div>
      </div>
    </Frame>
  );
}

/** Stats: the three tiles and a couple of cards. */
export function StatsSkeleton() {
  return (
    <Frame label="Loading stats">
      <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-8 sm:px-6 sm:py-12">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="space-y-2.5">
            <Bone className="h-9 w-32 sm:h-10" />
            <Bone className="h-4 w-64" />
          </div>
          <Bone className="h-9 w-48 rounded-lg" />
        </div>
        <div className="grid gap-3 sm:gap-4 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Bone key={i} className="h-28 rounded-xl" />
          ))}
        </div>
        <Bone className="h-48 rounded-xl" />
        <Bone className="h-64 rounded-xl" />
      </div>
    </Frame>
  );
}

/** Settings: a title and two grouped cards. */
export function SettingsSkeleton() {
  return (
    <Frame label="Loading settings">
      <div className="mx-auto w-full max-w-2xl space-y-5 px-4 py-8 sm:px-6 sm:py-12">
        <div className="space-y-2.5">
          <Bone className="h-9 w-40 sm:h-10" />
          <Bone className="h-4 w-56" />
        </div>
        <Bone className="h-28 rounded-xl" />
        <Bone className="h-72 rounded-xl" />
      </div>
    </Frame>
  );
}

/** A short message in the page frame (not found, errors). */
export function Message({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-h-screen flex-col">
      <NavHeader />
      <main className="flex-1">
        <div className="mx-auto w-full max-w-2xl space-y-4 px-4 py-12 sm:px-6 sm:py-20">
          <h1 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
            {title}
          </h1>
          <div className="text-muted-foreground space-y-4 text-sm leading-relaxed">
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}
