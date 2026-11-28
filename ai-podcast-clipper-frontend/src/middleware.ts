import { NextResponse, type NextRequest } from "next/server";

/**
 * The app listens on 127.0.0.1 only, but any web page open in the same browser can still
 * send requests to it. Two checks keep other sites out:
 * - the Host must be this laptop (a site that points its own name at 127.0.0.1, DNS
 *   rebinding, arrives with its own name and is refused);
 * - anything that changes something (POST, PUT, DELETE) must come from the app's own
 *   pages: a cross-site form or fetch could otherwise start a paid run or delete a video.
 */

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const READ_ONLY = new Set(["GET", "HEAD", "OPTIONS"]);

function hostnameOf(host: string | null) {
  if (!host) return null;
  try {
    return new URL(`http://${host}`).hostname;
  } catch {
    return null;
  }
}

function refuse(reason: string) {
  return NextResponse.json({ error: reason }, { status: 403 });
}

export function middleware(request: NextRequest) {
  const host = request.headers.get("host");
  const hostname = hostnameOf(host);
  if (!hostname || !LOCAL_HOSTS.has(hostname)) {
    return refuse("This app only answers on this computer (127.0.0.1)");
  }
  if (!READ_ONLY.has(request.method)) {
    const site = request.headers.get("sec-fetch-site");
    if (site && site !== "same-origin" && site !== "none") {
      return refuse("Requests from other sites are not allowed");
    }
    const origin = request.headers.get("origin");
    if (origin) {
      let originHost: string | null = null;
      try {
        originHost = new URL(origin).host;
      } catch {
        // "null" (a sandboxed page or a file) or not a URL
      }
      if (originHost !== host) {
        return refuse("Requests from other sites are not allowed");
      }
    }
  }
  return NextResponse.next();
}

export const config = {
  // Everything but the build's static files: pages and the API alike.
  matcher: ["/((?!_next/static|_next/image).*)"],
};
