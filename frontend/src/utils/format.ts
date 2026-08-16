export const money = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        maximumFractionDigits: 0,
      }).format(value);
export const percent = (value: number | null | undefined) =>
  value == null || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(2)}%`;
export const decimal = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
export const dateLabel = (value: string) => {
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      }).format(parsed);
};
