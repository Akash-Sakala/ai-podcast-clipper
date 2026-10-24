import "~/styles/globals.css";

import { type Metadata } from "next";
import localFont from "next/font/local";
import { ThemeProvider } from "~/components/theme-provider";

export const metadata: Metadata = {
  title: "Podcast Clipper",
  description: "Turn a podcast into ranked vertical clips with subtitles.",
  icons: [{ rel: "icon", url: "/favicon.ico" }],
};

// Self-hosted (src/fonts) instead of next/font/google: Google sometimes serves
// font URLs with several query parameters, which Turbopack can't resolve
// ("next/font/google queries have exactly one entry"). This also works offline.
const geist = localFont({
  src: "../fonts/geist-latin.woff2",
  variable: "--font-geist-sans",
  weight: "100 900",
  display: "swap",
});

const bricolage = localFont({
  src: "../fonts/bricolage-grotesque-latin.woff2",
  variable: "--font-bricolage",
  weight: "200 800",
  display: "swap",
});

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // next-themes sets the class before paint; suppress the expected mismatch.
    <html
      lang="en"
      className={`${geist.variable} ${bricolage.variable}`}
      suppressHydrationWarning
    >
      <body suppressHydrationWarning>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          disableTransitionOnChange
        >
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
