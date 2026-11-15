import Link from "next/link";
import { Message } from "~/components/skeletons";

export default function NotFound() {
  return (
    <Message title="Page not found">
      <p>
        This video or page doesn&apos;t exist. It may have been deleted from the
        data folder.
      </p>
      <Link
        href="/"
        className="bg-tally text-tally-ink hover:bg-tally/90 inline-flex h-9 items-center rounded-lg px-4 text-sm font-semibold transition-colors"
      >
        Back to your videos
      </Link>
    </Message>
  );
}
