// Shared between server code and client components (no Node.js imports here).

/** Host without a leading www., m. or music., when it is a YouTube host. */
function youtubeHost(u: URL): "youtube.com" | "youtu.be" | null {
  const host = u.hostname.toLowerCase().replace(/^(www|m|music)\./, "");
  return host === "youtube.com" || host === "youtu.be" ? host : null;
}

/**
 * The one YouTube-link check (the link box, the API and the duplicate check all use
 * it). Returns the normalised URL, or null when `input` is not a YouTube link.
 *
 * Accepted: an http(s) link, or one without a scheme (https is assumed), whose host is
 * youtube.com or youtu.be, optionally behind www., m. or music., and that has a path
 * after the host: watch, shorts, live, embed, youtu.be/<id>, channel and playlist
 * links alike. Not accepted: other schemes, other hosts, a bare youtube.com.
 */
export function parseYoutubeLink(input: string | null | undefined): URL | null {
  const text = (input ?? "").trim();
  if (!text) return null;
  try {
    const url = new URL(
      /^[a-z][a-z\d+.-]*:\/\//i.test(text) ? text : `https://${text}`,
    );
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (!youtubeHost(url) || url.pathname.length < 2) return null;
    return url;
  } catch {
    return null;
  }
}

/** The 11-character video id from a YouTube watch, share, Shorts or live link. */
export function youtubeVideoId(url: string | null | undefined): string | null {
  const u = parseYoutubeLink(url);
  if (!u) return null;
  const host = youtubeHost(u);
  let id: string | null = null;
  if (host === "youtu.be") id = u.pathname.split("/")[1] ?? null;
  else {
    id =
      u.searchParams.get("v") ??
      /^\/(?:shorts|live|embed)\/([^/?#]+)/.exec(u.pathname)?.[1] ??
      null;
  }
  return id && /^[\w-]{11}$/.test(id) ? id : null;
}
