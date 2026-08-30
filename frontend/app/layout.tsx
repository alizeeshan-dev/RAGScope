import type { Metadata } from "next";
import { FloatingNavigation } from "@/components/FloatingNavigation";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAGScope — Observable RAG research",
  description: "Inspect, compare, and evaluate reproducible retrieval-augmented generation pipelines.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">Skip to main content</a>
        <FloatingNavigation />
        {children}
      </body>
    </html>
  );
}
