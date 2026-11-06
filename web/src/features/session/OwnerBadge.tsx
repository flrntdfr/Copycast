import { UserRound } from "lucide-react";

import { ownerLabel, useMe, type Owned } from "@/api/session";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Whose Feed this is, for an Admin looking at another User's Feed: "emilia", or "emilia (owner
 * removed)" once that User left the configuration. Nothing for a Member, for the Admin's own
 * Feeds, and in open mode.
 */
export function OwnerBadge({
  feed,
  className,
}: {
  feed: Owned | null | undefined;
  className?: string;
}) {
  const label = ownerLabel(useMe(), feed);
  if (!label) return null;
  return (
    <Badge
      variant="outline"
      className={cn(
        "max-w-full font-normal text-muted-foreground",
        feed?.owner_removed && "border-dashed",
        className,
      )}
      title={feed?.owner_removed ? "Its owner is no longer configured" : `Owned by ${label}`}
      data-testid="owner-badge"
      data-owner={feed?.owner}
    >
      <UserRound aria-hidden />
      <span className="sr-only">Owner: </span>
      <span className="truncate">{label}</span>
    </Badge>
  );
}
