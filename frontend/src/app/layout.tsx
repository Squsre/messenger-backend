import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { AppProviders } from "@/components/common/app-providers";

/**
 * Local custom typography (public/fonts/, SIL OFL — see the bundled
 * *-OFL.txt files):
 *
 * - Vazirmatn (variable, 100–900) — the application font. Full Latin +
 *   Persian/Arabic coverage, so UI labels, English numbers and Farsi
 *   messages all render from one face.
 * - Rajdhani — Latin display face for headings and stats.
 * - Cinzel Decorative — the brand wordmark face.
 *
 * `display: "swap"` keeps first paint instant while the fonts stream in.
 */
const vazirmatn = localFont({
  src: "../../public/fonts/Vazirmatn-VariableFont_wght.ttf",
  weight: "100 900",
  variable: "--font-vazirmatn",
  display: "swap",
});

const rajdhani = localFont({
  src: [
    { path: "../../public/fonts/Rajdhani-Light.ttf", weight: "300" },
    { path: "../../public/fonts/Rajdhani-Regular.ttf", weight: "400" },
    { path: "../../public/fonts/Rajdhani-Medium.ttf", weight: "500" },
    { path: "../../public/fonts/Rajdhani-SemiBold.ttf", weight: "600" },
    { path: "../../public/fonts/Rajdhani-Bold.ttf", weight: "700" },
  ],
  variable: "--font-rajdhani",
  display: "swap",
});

const cinzelDecorative = localFont({
  src: [
    { path: "../../public/fonts/CinzelDecorative-Regular.ttf", weight: "400" },
    { path: "../../public/fonts/CinzelDecorative-Bold.ttf", weight: "700" },
    { path: "../../public/fonts/CinzelDecorative-Black.ttf", weight: "900" },
  ],
  variable: "--font-cinzel",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Messenger — Private realtime chat",
  description:
    "A modern messenger frontend: direct chats, groups, media and realtime delivery over WebSocket.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={`${vazirmatn.variable} ${rajdhani.variable} ${cinzelDecorative.variable} font-sans antialiased bg-background text-foreground`}
      >
        <AppProviders>{children}</AppProviders>
      </body>
    </html>
  );
}
