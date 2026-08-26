export function ConfigSummary({ label, value }: { label: string; value: Record<string, unknown> }) {
  return (
    <div className="config-card">
      <span>{label}</span>
      <code>{Object.entries(value).map(([key, item]) => `${key}: ${String(item)}`).join(" · ") || "Default"}</code>
    </div>
  );
}
