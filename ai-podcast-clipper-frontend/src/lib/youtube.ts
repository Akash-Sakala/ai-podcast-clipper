// Shared between server code and client components (no Node.js imports here).

/** The 11-character video id from a YouTube watch, share, Shorts or live link. */
export function youtubeVideoId(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url.includes("://") ? url : `https://${url}`);
    const host = u.hostname.replace(/^(www|m|music)\./, "");
    let id: string | null = null;
    if (host === "youtu.be") id = u.pathname.split("/")[1] ?? null;
    else if (host === "youtube.com") {
      id =
        u.searchParams.get("v") ??
        /^\/(?:shorts|live|embed)\/([^/?#]+)/.exec(u.pathname)?.[1] ??
        null;
    }
    return id && /^[\w-]{11}$/.test(id) ? id : null;
  } catch {
    return null;
  }
}
