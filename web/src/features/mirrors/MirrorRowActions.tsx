import { Link } from "@tanstack/react-router";
import { Copy, MoreHorizontal, Pause, Play, RefreshCw, Trash2, Link2 } from "lucide-react";
import { useState } from "react";

import { useRequestRefresh, useSetPaused } from "@/features/feeds/mutations";
import { DeleteFeedDialog } from "@/features/feeds/DeleteFeedDialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { copyWithToast } from "@/lib/copy";
import type { BucketRead, MirrorRead } from "@/api/types";

function IconAction({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          onClick={onClick}
          disabled={disabled}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

/** Copy feed URL / Refresh / Pause plus the overflow menu; compact mode keeps only the menu. */
export function MirrorRowActions({
  mirror,
  compact = false,
}: {
  mirror: MirrorRead;
  compact?: boolean;
}) {
  const refresh = useRequestRefresh();
  const { setPaused, isPending: pausing } = useSetPaused();
  const [deleting, setDeleting] = useState(false);

  const copy = () => void copyWithToast(mirror.feed_url, "Feed URL");
  const doRefresh = () => refresh.mutate({ params: { path: { feed_id: mirror.id } } });
  const togglePause = () => void setPaused(mirror.id, !mirror.paused);

  return (
    <div className="flex items-center justify-end gap-0.5">
      {!compact ? (
        <>
          <IconAction label="Copy feed URL" onClick={copy}>
            <Copy />
          </IconAction>
          <IconAction label="Refresh now" onClick={doRefresh} disabled={refresh.isPending}>
            <RefreshCw className={refresh.isPending ? "animate-spin" : undefined} />
          </IconAction>
          <IconAction
            label={mirror.paused ? "Resume" : "Pause"}
            onClick={togglePause}
            disabled={pausing}
          >
            {mirror.paused ? <Play /> : <Pause />}
          </IconAction>
        </>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label={`More actions for ${mirror.title}`}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem asChild>
            <Link to="/mirrors/$mirrorId" params={{ mirrorId: mirror.id }}>
              Open Mirror
            </Link>
          </DropdownMenuItem>
          {compact ? (
            <>
              <DropdownMenuItem onSelect={copy}>
                <Copy /> Copy feed URL
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={doRefresh}>
                <RefreshCw /> Refresh now
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={togglePause}>
                {mirror.paused ? <Play /> : <Pause />} {mirror.paused ? "Resume" : "Pause"}
              </DropdownMenuItem>
            </>
          ) : null}
          <DropdownMenuItem asChild>
            <Link
              to="/mirrors/$mirrorId"
              params={{ mirrorId: mirror.id }}
              search={{ settings: true }}
            >
              <Link2 /> Change Source URL…
            </Link>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(true)}>
            <Trash2 /> Delete…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <DeleteFeedDialog
        feed={mirror}
        open={deleting}
        onOpenChange={setDeleting}
        navigateAway={false}
      />
    </div>
  );
}

/**
 * The overflow menu of a Bucket Feed's row: open its bucket page, delete it. The default Bucket
 * can never be deleted, so its menu only opens.
 */
export function BucketRowActions({ bucket }: { bucket: BucketRead }) {
  const [deleting, setDeleting] = useState(false);
  return (
    <div className="flex items-center justify-end gap-0.5">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon-sm" aria-label={`More actions for ${bucket.name}`}>
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem asChild>
            <Link to="/buckets/$bucketId" params={{ bucketId: bucket.id }}>
              Open
            </Link>
          </DropdownMenuItem>
          {bucket.is_default ? null : (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setDeleting(true)}>
                <Trash2 /> Delete…
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <DeleteFeedDialog
        feed={bucket}
        open={deleting}
        onOpenChange={setDeleting}
        navigateAway={false}
      />
    </div>
  );
}
