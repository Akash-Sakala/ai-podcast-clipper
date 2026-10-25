"use client";

import { ChartColumn } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "~/lib/utils";

/** Header link to the stats page, styled like the theme menu's button. */
export function StatsLink() {
  const active = usePathname() === "/stats";
  return (
    <Link
      href="/stats"
      aria-label="Stats"
      title="Stats"
      aria-current={active ? "page" : undefined}
      className={cn(
        "text-muted-foreground hover:text-foreground hover:bg-accent inline-flex size-9 items-center justify-center rounded-md transition-colors",
        active && "bg-accent text-foreground",
      )}
    >
      <ChartColumn className="size-[18px]" aria-hidden />
    </Link>
  );
}
