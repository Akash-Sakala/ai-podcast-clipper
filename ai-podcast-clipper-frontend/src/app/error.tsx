"use client";

import Link from "next/link";
import { Message } from "~/components/skeletons";

export default function ErrorPage({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <Message title="Something went wrong">
      <p>
        The page couldn&apos;t be loaded. Your videos and clips are not
        affected.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={reset}
          className="bg-tally text-tally-ink hover:bg-tally/90 inline-flex h-9 items-center rounded-lg px-4 text-sm font-semibold transition-colors"
        >
          Try again
        </button>
        <Link
          href="/"
          className="text-muted-foreground hover:text-foreground hover:bg-accent inline-flex h-9 items-center rounded-lg px-3.5 text-sm font-medium transition-colors"
        >
          Back to your videos
        </Link>
      </div>
    </Message>
  );
}
