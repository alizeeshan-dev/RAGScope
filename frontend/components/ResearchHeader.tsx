interface ResearchHeaderProps {
  context: string;
}

export function ResearchHeader({ context }: ResearchHeaderProps) {
  return (
    <header className="topbar">
      <a className="brand" href="/">
        <span className="brand-mark">R</span>
        <span><strong>RAGScope</strong><small>{context}</small></span>
      </a>
      <nav className="top-nav" aria-label="Primary navigation">
        <a href="/laboratory">Laboratory</a>
        <a href="/datasets">Datasets</a>
        <a href="/benchmarks">Benchmarks</a>
      </nav>
    </header>
  );
}
