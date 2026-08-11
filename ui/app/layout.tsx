import type { Metadata } from "next";
import "./globals.css";

const themeBootstrapScript = `
  (() => {
    try {
      const saved = localStorage.getItem("regdocs-atlas-theme");
      const preference = saved === "light" || saved === "dark" ? saved : "system";
      const theme = preference === "system"
        ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
        : preference;
      document.documentElement.dataset.theme = theme;
      document.documentElement.dataset.themePreference = preference;
      document.documentElement.style.colorScheme = theme;
    } catch {
      document.documentElement.dataset.theme = "light";
    }
  })();
`;

export const metadata: Metadata = {
  title: "REGDOCS Atlas",
  description: "Evidence-first research over Canada Energy Regulator records.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrapScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
