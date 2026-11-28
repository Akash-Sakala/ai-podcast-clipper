import type { MetadataRoute } from "next";

/**
 * Makes the app installable (Edge or Chrome: the install icon in the address bar, or
 * "Apps > Install this site as an app"): it then opens in its own window, with a
 * taskbar icon, like a desktop app. The server still has to be running (scripts/clipper.ps1).
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Podcast Clipper",
    short_name: "Clipper",
    description: "Turn a podcast into ranked vertical clips with subtitles.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#15171c",
    theme_color: "#15171c",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      {
        src: "/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
