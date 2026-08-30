"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const destinations = [
  { label: "Overview", href: "/", matches: (path: string) => path === "/" },
  { label: "Corpora", href: "/corpora", matches: (path: string) => path.startsWith("/corpora") || path.startsWith("/versions") || path.startsWith("/documents") },
  { label: "Query Lab", href: "/laboratory", matches: (path: string) => path.startsWith("/laboratory") },
  { label: "Comparisons", href: "/comparisons", matches: (path: string) => path.startsWith("/comparisons") },
  { label: "Datasets", href: "/datasets", matches: (path: string) => path.startsWith("/datasets") },
  { label: "Benchmarks", href: "/benchmarks", matches: (path: string) => path.startsWith("/benchmarks") },
  { label: "Experiments", href: "/experiments", matches: (path: string) => path.startsWith("/experiments") || path.startsWith("/results") },
  { label: "Pipelines", href: "/runtime", matches: (path: string) => path.startsWith("/runtime") },
] as const;

export function FloatingNavigation() {
  const pathname = usePathname();

  return (
    <header className="floating-navigation">
      <Link className="floating-brand" href="/" aria-label="RAGScope overview">
        <span className="floating-brand-mark" aria-hidden="true">R</span>
        <span>
          <strong>RAGScope</strong>
          <small>Research observatory</small>
        </span>
      </Link>
      <nav className="floating-navigation-links" aria-label="Primary navigation">
        {destinations.map((destination) => {
          const active = destination.matches(pathname);
          return (
            <Link
              key={destination.href}
              href={destination.href}
              className={active ? "active" : undefined}
              aria-current={active ? "page" : undefined}
            >
              {destination.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
