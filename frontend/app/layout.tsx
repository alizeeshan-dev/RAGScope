import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAGScope Corpus Studio",
  description: "Inspectable scientific corpus ingestion and indexing",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">Skip to main content</a>
        {children}
      </body>
    </html>
  );
}
