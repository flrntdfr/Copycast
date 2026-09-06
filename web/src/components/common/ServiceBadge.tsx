import { Badge } from "@/components/ui/badge";
import { sourceKindLabel } from "@/lib/labels";
import type { SourceKind } from "@/api/types";

/** "YouTube", "RSS" and friends; `service` wins over the Source kind. */
export function ServiceBadge({
  service,
  sourceKind,
}: {
  service: string | null | undefined;
  sourceKind: SourceKind;
}) {
  const text = service?.trim() || sourceKindLabel(sourceKind);
  return (
    <Badge variant="secondary" className="font-normal">
      {text}
    </Badge>
  );
}
