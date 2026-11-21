"use client";

import { useRunSignal } from "~/lib/run-signal";
import { LogoMark } from "./logo";

/**
 * The header's mark: its bars move while a video on this page is being processed.
 * Decorative only (it sits inside the home link): the progress strip and the tab title
 * carry the same news in words.
 */
export function HeaderLogo() {
  const running = useRunSignal().active.length > 0;
  return (
    <LogoMark className="text-muted-foreground size-7" running={running} />
  );
}
