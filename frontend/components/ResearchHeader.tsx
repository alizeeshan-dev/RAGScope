interface ResearchHeaderProps {
  context: string;
}

export function ResearchHeader({ context }: ResearchHeaderProps) {
  // The shared floating navigation lives in the root layout. Retain this
  // compatibility component so existing page imports do not duplicate it.
  return <span className="sr-only">{context}</span>;
}
