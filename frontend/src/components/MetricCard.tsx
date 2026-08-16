interface MetricCardProps {
  label: string;
  value: string;
  detail?: string;
  tone?: "neutral" | "positive" | "negative";
}
export function MetricCard({
  label,
  value,
  detail,
  tone = "neutral",
}: MetricCardProps) {
  return (
    <article className="metric-card">
      <span className="metric-label">{label}</span>
      <strong className={`metric-value ${tone}`}>{value}</strong>
      {detail && <span className="metric-detail">{detail}</span>}
    </article>
  );
}
