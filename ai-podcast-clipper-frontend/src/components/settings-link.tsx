"use client";

import { Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "~/lib/utils";

/** Header link to the Settings page, styled like the stats link. */
export function SettingsLink() {
  const active = usePathname() === "/settings";
  return (
    <Link
      href="/settings"
      aria-label="Settings"
      title="Settings"
      aria-current={active ? "page" : undefined}
      className={cn(
        "text-muted-foreground hover:text-foreground hover:bg-accent inline-flex size-9 items-center justify-center rounded-md transition-colors",
        active && "bg-accent text-foreground",
      )}
    >
      <Settings className="size-[18px]" aria-hidden />
    </Link>
  );
}
