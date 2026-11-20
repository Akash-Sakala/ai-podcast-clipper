"use client";

import { Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { iconButtonClass } from "./ui/icon-button";
import { Tooltip } from "./ui/tooltip";

/** Header link to the Settings page, styled like the stats link. */
export function SettingsLink() {
  const active = usePathname() === "/settings";
  return (
    <Tooltip label="Settings">
      <Link
        href="/settings"
        aria-label="Settings"
        aria-current={active ? "page" : undefined}
        className={iconButtonClass("md", active)}
      >
        <Settings className="size-[18px]" aria-hidden />
      </Link>
    </Tooltip>
  );
}
