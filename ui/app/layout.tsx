import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "REGDOCS Atlas",
  description: "Evidence-first research over Canada Energy Regulator records.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
