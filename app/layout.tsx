import type { Metadata, Viewport } from "next";
import { Fraunces, Outfit } from "next/font/google";
import "./globals.css";

const outfit = Outfit({
  variable: "--font-outfit",
  subsets: ["latin"],
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Sign To Speak",
  description:
    "Watch ASL fingerspelling through the camera, recover a 3D hand mesh, and speak it with a voice you pick.",
  applicationName: "Sign To Speak",
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: "Sign To Speak",
    statusBarStyle: "black-translucent",
  },
};

export const viewport: Viewport = {
  themeColor: "#14110e",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${outfit.variable} ${fraunces.variable} dark h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-[#14110e] font-sans text-[#f6efe4]">
        {children}
      </body>
    </html>
  );
}
