import type { Metadata } from "next";
import "./globals.css";
import { Geist } from "next/font/google";
import { cn } from "@/lib/utils";
import { TooltipProvider } from "@/components/ui/tooltip";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist-sans" });

const themeBootstrap = `
  (() => {
    try {
      const saved = localStorage.getItem("regdocs-atlas-theme");
      const preference = saved === "light" || saved === "dark" ? saved : "system";
      const dark = preference === "dark" ||
        (preference === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
      document.documentElement.classList.toggle("dark", dark);
      document.documentElement.style.colorScheme = dark ? "dark" : "light";
    } catch {}
  })();
`;

export const metadata: Metadata = {
  title: "REGDOCS Atlas | CER research, made usable",
  description: "Find, verify, collect, and export evidence from Canada Energy Regulator records.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={cn("font-sans", geist.variable)} suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeBootstrap }} /></head>
      <body><TooltipProvider>{children}</TooltipProvider></body>
    </html>
  );
}
