import { formatDateTime, formatRelative } from "@/lib/format";

/** "3 hours ago" with the absolute timestamp on hover and for assistive tech. */
export function RelativeTime({
  value,
  fallback = "",
}: {
  value: string | null | undefined;
  fallback?: string;
}) {
  if (!value) return <span className="text-muted-foreground">{fallback}</span>;
  return (
    <time dateTime={value} title={formatDateTime(value)}>
      {formatRelative(value)}
    </time>
  );
}
