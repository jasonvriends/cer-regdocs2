import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "REGDOCS Atlas | CER research, made usable",
  description: "Find, verify, collect, and export evidence from Canada Energy Regulator records.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
