import type { Metadata } from "next";
import { JetBrains_Mono, Noto_Kufi_Arabic } from "next/font/google";
import "./globals.css";
import { AppProvider } from "@/i18n/context";
import { AppShell } from "@/components/AppShell";

const notoKufi = Noto_Kufi_Arabic({
  subsets: ["arabic"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-arabic",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "منصة الوكلاء الموحدة | Unified Agent System",
  description: "Self-hosted Arabic-first RTL Personal AI Agent & SEO Platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl" className={`${notoKufi.variable} ${jetbrainsMono.variable}`}>
      <body className="font-sans antialiased">
        <AppProvider>
          <AppShell>{children}</AppShell>
        </AppProvider>
      </body>
    </html>
  );
}
