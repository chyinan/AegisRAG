import type { Metadata } from "next";
import { QueryProvider } from "@/providers/query-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Enterprise RAG Workbench",
  description: "Secure enterprise knowledge workbench for RAG, citations, diagnostics and governance.",
  other: {
    google: "notranslate"
  }
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" translate="no" className="notranslate">
      <body translate="no" className="notranslate">
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
