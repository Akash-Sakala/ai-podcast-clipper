import type { ClipDetail, ClipPost, Job, Platform } from "./job-types";

/** Limits for text typed on the clips page (Gemini's text is checked by the backend too). */
export const POST_LIMITS = {
  title: 100, // YouTube's title limit
  caption: 2200, // Instagram's caption limit
  credit: 500,
  hashtags: 30, // Instagram allows 30
} as const;

export const PLATFORMS: { id: Platform; label: string }[] = [
  { id: "shorts", label: "Shorts" },
  { id: "reels", label: "Reels" },
];

/** "#a #b" or "a, b" or ["#a", "b"] as distinct one-word hashtags with a #. */
export function cleanHashtags(input: string | readonly string[]): string[] {
  const items = typeof input === "string" ? input.split(/[\s,]+/) : input;
  const seen = new Set<string>();
  const tags: string[] = [];
  for (const item of items) {
    const tag = item.replace(/[^\p{L}\p{N}_]/gu, "");
    if (!tag || tag.length > 50 || seen.has(tag.toLowerCase())) continue;
    seen.add(tag.toLowerCase());
    tags.push(`#${tag}`);
    if (tags.length >= POST_LIMITS.hashtags) break;
  }
  return tags;
}

/** The credit line a clip gets until it is edited. */
export function defaultCredit(job: Pick<Job, "youtubeUrl" | "meta">): string {
  const channel = job.meta?.channel?.trim();
  const url = job.youtubeUrl;
  if (channel && url) return `Clip from ${channel}, full episode: ${url}`;
  if (channel) return `Clip from ${channel}`;
  if (url) return `Full episode: ${url}`;
  return "";
}

export type PostDraft = {
  title: string;
  caption: string;
  /** As typed: cleaned when it is saved. */
  hashtags: string;
  credit: string;
};

/** The text the page shows for a clip: its saved post, else its title and the default credit. */
export function draftOf(
  detail: ClipDetail | undefined,
  fallbackTitle: string,
  credit: string,
): PostDraft {
  const post = detail?.post;
  return {
    title: post?.title ?? fallbackTitle.slice(0, POST_LIMITS.title),
    caption: post?.caption ?? "",
    hashtags: (post?.hashtags ?? []).join(" "),
    credit: post?.credit ?? credit,
  };
}

/** Everything in one block, in the order a post reads: title, caption, credit, hashtags. */
export function postAll(draft: PostDraft): string {
  return [
    draft.title,
    draft.caption,
    draft.credit,
    cleanHashtags(draft.hashtags).join(" "),
  ]
    .map((part) => part.trim())
    .filter(Boolean)
    .join("\n\n");
}

export function postedOn(post: ClipPost | null | undefined): Platform[] {
  return PLATFORMS.filter((p) => post?.posted?.[p.id]).map((p) => p.id);
}

/** A change sent to PATCH /api/jobs/[id]/clips/[clip]/post. */
export type PostChange = Partial<{
  title: string;
  caption: string;
  hashtags: string | string[];
  credit: string;
  posted: Partial<Record<Platform, boolean>>;
}>;

/**
 * A clip's post after a change from the page: text trimmed to the limits, hashtags
 * cleaned, a platform switched on keeps the first date it was marked, switched off is null.
 * Returns null when the change holds nothing usable.
 */
export function applyPostChange(
  current: ClipPost | null | undefined,
  change: unknown,
  now = new Date(),
): ClipPost | null {
  if (!change || typeof change !== "object") return null;
  const c = change as Record<string, unknown>;
  const next: ClipPost = { ...(current ?? {}) };
  let touched = false;
  const text = (key: "title" | "caption" | "credit", oneLine: boolean) => {
    const value = c[key];
    if (typeof value !== "string") return;
    const cleaned = oneLine ? value.replace(/\s+/g, " ").trim() : value.trim();
    next[key] = cleaned.slice(0, POST_LIMITS[key]);
    next.edited = true;
    touched = true;
  };
  text("title", true);
  text("caption", false);
  text("credit", true);
  if (typeof c.hashtags === "string" || Array.isArray(c.hashtags)) {
    next.hashtags = cleanHashtags(
      Array.isArray(c.hashtags) ? c.hashtags.map(String) : c.hashtags,
    );
    next.edited = true;
    touched = true;
  }
  if (c.posted && typeof c.posted === "object") {
    const posted = { ...(next.posted ?? {}) };
    for (const { id } of PLATFORMS) {
      const value = (c.posted as Record<string, unknown>)[id];
      if (typeof value !== "boolean") continue;
      posted[id] = value ? (posted[id] ?? now.toISOString()) : null;
      touched = true;
    }
    next.posted = posted;
  }
  return touched ? next : null;
}
